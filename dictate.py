#!/usr/bin/env python3
"""
Whisper Dictate - Global hotkey dictation using OpenAI Whisper
Hold Ctrl+Space to record, release to stop and transcribe.
Transcribed text is automatically typed at your cursor position.

Requirements:
- macOS Accessibility permission for the terminal/Python app
- macOS Microphone permission for the terminal/Python app
"""

import os
import sys
import signal
import subprocess
import numpy as np
import sounddevice as sd
from pynput import keyboard
from pynput.keyboard import Controller as KeyboardController
import whisper
import time
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import multiprocessing
import threading
import queue
try:
    from Quartz import (
        CGEventSourceKeyState, kCGEventSourceStateHIDSystemState,
        CGEventTapCreate, CGEventTapEnable, CGEventGetIntegerValueField,
        CGEventMaskBit, CFMachPortCreateRunLoopSource, CFRunLoopGetCurrent,
        CFRunLoopAddSource, CFRunLoopRun, CFRunLoopStop,
        kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionListenOnly,
        kCGEventKeyUp, kCGEventFlagsChanged, kCGKeyboardEventKeycode,
        kCFRunLoopDefaultMode,
    )
    QUARTZ_AVAILABLE = True
except ImportError:
    QUARTZ_AVAILABLE = False

# ============================================================================
# Configuration
# ============================================================================
SAMPLE_RATE = 16000
MODEL_NAME = "medium"  # Options: tiny, base, small, medium, large
LANGUAGE = "en"  # None = auto-detect, or "en", "es", "fr", etc.
SILENCE_THRESHOLD = 0.01  # Audio level below this is considered silence
SILENCE_TRIM_MS = 100  # Keep this much silence at edges (milliseconds)
MIN_RECORDING_SECONDS = 0.5  # Ignore recordings shorter than this (prevents hallucinations)
# Whisper initial_prompt conditions the model's style. It can leak into output, so we
# store it as a constant and strip it from transcriptions if detected.
WHISPER_INITIAL_PROMPT = "Transcribe spoken English accurately with proper punctuation."
WATCHDOG_MAX_RECORDING_SECONDS = 300  # Absolute max recording duration (5 min hard limit)
WATCHDOG_RELEASE_GRACE_SECONDS = 10  # After independent monitor says keys released, wait this long before force-stop
WATCHDOG_LOG_INTERVAL = 10  # Log watchdog status every N seconds during recording (avoids log spam)
# macOS virtual key codes for Ctrl+Space (used by Quartz CGEventSourceKeyState)
KEYCODE_SPACE = 49
KEYCODE_CTRL_LEFT = 59
KEYCODE_CTRL_RIGHT = 62

# Known Whisper hallucinations (model artifacts from training data, not real transcriptions)
HALLUCINATION_PATTERNS = [
    "transcribe spoken english accurately with proper punctuation.",
    "transcribe spoken english accurately with proper punctuation",
    "transcribed by https://otter.ai",
    "otter.ai",
    "thanks for watching",
    "thank you for watching",
    "subscribe to my channel",
    "please subscribe",
    "like and subscribe",
    "see you in the next video",
    "see you next time",
    "the end",
    "you",
    "bye",
    "bye bye",
    "bye-bye",
]

# Logging configuration
LOG_FILE = Path.home() / "whisper-dictate" / "dictate.log"
LOG_MAX_BYTES = 1 * 1024 * 1024  # 1 MB max log size
LOG_BACKUP_COUNT = 3  # Keep 3 backup logs

# ============================================================================
# Logging Setup with Rotation
# ============================================================================
def setup_logging():
    """Configure logging with rotation to prevent unbounded growth"""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Get or create named logger (avoid duplicates with root logger)
    logger = logging.getLogger('whisper_dictate')

    # Clear any existing handlers to avoid duplicates on reload
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False  # Don't propagate to root logger

    # File handler with rotation
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)

    # Only add console handler if running interactively (not under launchd)
    # When running under launchd, stdout goes to the log file anyway
    if sys.stdout.isatty():
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)
        logger.addHandler(console_handler)

    return logger

logger = setup_logging()

# ============================================================================
# Permission Checks
# ============================================================================
def check_accessibility_permission():
    """Check if Accessibility permission is granted (macOS)"""
    try:
        # Try to create a keyboard listener - this will fail without permission
        test_listener = keyboard.Listener(on_press=lambda k: None)
        test_listener.start()
        time.sleep(0.1)
        test_listener.stop()
        return True
    except Exception as e:
        logger.error(f"Accessibility check failed: {e}")
        return False

def check_microphone_permission():
    """Check if Microphone permission is granted"""
    try:
        # Try to open an audio stream briefly
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype=np.float32):
            pass
        return True
    except sd.PortAudioError as e:
        logger.error(f"Microphone permission check failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Microphone check error: {e}")
        return False

def print_permission_instructions():
    """Print instructions for granting permissions"""
    print("\n" + "=" * 60)
    print("PERMISSION SETUP REQUIRED")
    print("=" * 60)
    print("\nPlease grant the following permissions in:")
    print("  System Settings -> Privacy & Security")
    print("\n1. ACCESSIBILITY (required for hotkey)")
    print("   - Add Terminal.app (or your terminal)")
    print("   - OR add Python.app from:")
    print(f"     {sys.executable}")
    print("\n2. MICROPHONE (required for recording)")
    print("   - Add Terminal.app (or your terminal)")
    print("\nAfter granting permissions, restart this script.")
    print("=" * 60 + "\n")

def verify_permissions():
    """Verify all required permissions are granted"""
    logger.info("Checking permissions...")

    # Check microphone first (less intrusive test)
    mic_ok = check_microphone_permission()
    if mic_ok:
        logger.info("Microphone permission: OK")
    else:
        logger.warning("Microphone permission: DENIED")

    # Check accessibility (will show warning in console if denied)
    # We check this by observing if pynput prints the "not trusted" warning
    acc_ok = True  # We'll verify this differently

    if not mic_ok:
        print_permission_instructions()
        return False

    return True

# ============================================================================
# Independent Key Monitor (bypasses pynput entirely)
# ============================================================================
class IndependentKeyMonitor:
    """Monitors key state via a SEPARATE Quartz event tap on its own thread.

    pynput's event tap drops key-release events under load, and
    CGEventSourceKeyState(HIDSystemState) reflects the same corrupted state.
    This monitor creates a second, listen-only event tap that runs on its own
    CFRunLoop thread. Since it's listen-only and independent of pynput, it
    reliably sees ALL key events including the releases pynput misses.
    """

    def __init__(self):
        self.ctrl_held = False
        self.space_held = False
        self._lock = threading.Lock()
        self._thread = None
        self._run_loop = None
        self.available = False

    def start(self):
        if not QUARTZ_AVAILABLE:
            logger.warning("IndependentKeyMonitor: Quartz not available")
            return

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        """Create a listen-only event tap and run its CFRunLoop."""
        # Listen for key-up events and modifier flag changes
        event_mask = CGEventMaskBit(kCGEventKeyUp) | CGEventMaskBit(kCGEventFlagsChanged)

        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionListenOnly,  # Listen only - never interferes
            event_mask,
            self._tap_callback,
            None
        )

        if tap is None:
            logger.warning("IndependentKeyMonitor: failed to create event tap (need Accessibility permission)")
            return

        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        run_loop = CFRunLoopGetCurrent()
        CFRunLoopAddSource(run_loop, source, kCFRunLoopDefaultMode)
        CGEventTapEnable(tap, True)

        self.available = True
        logger.info("IndependentKeyMonitor: started (separate event tap active)")

        # This blocks the thread - CFRunLoopRun processes events
        CFRunLoopRun()

    def _tap_callback(self, proxy, event_type, event, refcon):
        """Called by macOS for every key-up and modifier change event."""
        try:
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)

            if event_type == kCGEventKeyUp:
                if keycode == KEYCODE_SPACE:
                    with self._lock:
                        self.space_held = False

            elif event_type == kCGEventFlagsChanged:
                # For modifier keys, flags-changed fires on both press and release.
                # On release, the modifier flag is cleared from the event flags.
                # We track ctrl by keycode: if we get a flags-changed for ctrl keycode
                # and the ctrl modifier bit is NOT set, it's a release.
                if keycode in (KEYCODE_CTRL_LEFT, KEYCODE_CTRL_RIGHT):
                    # Check if ctrl modifier is still active in the event flags
                    # Ctrl flag = 0x40000 (kCGEventFlagMaskControl = 1 << 18)
                    from Quartz import CGEventGetFlags
                    flags = CGEventGetFlags(event)
                    ctrl_flag = 1 << 18  # kCGEventFlagMaskControl
                    with self._lock:
                        self.ctrl_held = bool(flags & ctrl_flag)

                elif keycode == KEYCODE_SPACE:
                    # Space is not a modifier, shouldn't get flags-changed for it
                    pass
        except Exception as e:
            logger.error(f"IndependentKeyMonitor callback error: {e}")

        return event  # Must return the event for listen-only taps

    def set_pressed(self, ctrl=None, space=None):
        """Called by pynput on_press to sync press events (releases tracked independently)."""
        with self._lock:
            if ctrl is not None:
                self.ctrl_held = ctrl
            if space is not None:
                self.space_held = space

    def is_hotkey_held(self):
        """Check if Ctrl+Space is held according to this independent monitor."""
        if not self.available:
            return None
        with self._lock:
            return self.ctrl_held and self.space_held


# ============================================================================
# Main Whisper Dictate Class
# ============================================================================
class WhisperDictate:
    def __init__(self):
        logger.info("Initializing Whisper Dictate...")
        logger.info(f"Python: {sys.executable}")
        logger.info(f"Loading Whisper model '{MODEL_NAME}'... (this may take a moment)")

        self.model = whisper.load_model(MODEL_NAME)
        logger.info(f"Model '{MODEL_NAME}' loaded successfully!")

        self.recording = False
        self.audio_data = []
        self.ctrl_pressed = False
        self.space_pressed = False
        self.keyboard_controller = KeyboardController()
        self.stream = None
        self.listener = None
        self._shutdown_requested = False
        self._recording_start_time = None
        self._independent_release_detected_at = None
        self._last_watchdog_log_time = 0  # Throttle verbose watchdog logs

        # Independent key monitor: separate Quartz event tap that catches
        # key-release events pynput misses
        self._key_monitor = IndependentKeyMonitor()
        self._key_monitor.start()
        time.sleep(0.3)  # Give the monitor thread time to create the event tap

        # Async transcription: pynput callbacks return instantly, transcription
        # happens in a background thread. This prevents macOS from disabling the
        # event tap when callbacks take too long (>1s triggers tap disable).
        self._transcription_queue = queue.Queue()
        self._transcription_thread = threading.Thread(
            target=self._transcription_worker, daemon=True
        )
        self._transcription_thread.start()

        # Log available audio devices
        logger.info("Available audio devices:")
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                logger.info(f"  [{i}] {dev['name']} (inputs: {dev['max_input_channels']})")

        default_input = sd.query_devices(kind='input')
        logger.info(f"Using default input: {default_input['name']}")
        logger.info(f"Independent key monitor: {'active' if self._key_monitor.available else 'NOT available'}")

    # ========================================================================
    # Async Transcription Worker
    # ========================================================================
    def _transcription_worker(self):
        """Background thread that processes transcription jobs.

        By moving transcription off the pynput callback thread, we ensure:
        1. pynput callbacks return in <1ms (macOS won't disable the event tap)
        2. Key events (including releases) are never missed during transcription
        3. The main loop watchdog ticks uninterrupted
        """
        while True:
            audio_chunks = self._transcription_queue.get()
            if audio_chunks is None:
                break  # Shutdown signal
            try:
                self._do_transcription(audio_chunks)
            except Exception as e:
                logger.error(f"Transcription worker error: {e}")

    def _do_transcription(self, audio_chunks):
        """Actually transcribe audio. Runs in the worker thread."""
        audio = np.concatenate(audio_chunks, axis=0).flatten()
        duration = len(audio) / SAMPLE_RATE

        # Reject recordings that are too short (prevents hallucinations on accidental taps)
        if duration < MIN_RECORDING_SECONDS:
            logger.warning(f"Recording too short ({duration:.2f}s < {MIN_RECORDING_SECONDS}s), ignoring")
            return

        # Check if there's actual audio content
        audio_level = np.abs(audio).mean()
        logger.info(f"Audio level: {audio_level:.6f} (duration: {duration:.2f}s)")

        if audio_level < 0.0001:
            logger.warning("Audio level too low - check microphone permissions or input device")
            return

        # Trim silence
        audio = self.trim_silence(audio)

        logger.info("Transcribing...")

        # Transcribe with Whisper
        result = self.model.transcribe(
            audio,
            fp16=False,
            language=LANGUAGE,
            task="transcribe",
            without_timestamps=True,
            condition_on_previous_text=False,
            initial_prompt=WHISPER_INITIAL_PROMPT
        )
        text = result["text"].strip()

        # Strip leaked initial_prompt from the beginning of transcription.
        # Whisper sometimes regurgitates the prompt when the audio starts softly.
        prompt_lower = WHISPER_INITIAL_PROMPT.lower().rstrip(".")
        text_lower = text.lower()
        if text_lower.startswith(prompt_lower):
            text = text[len(prompt_lower):].lstrip(" .,;:").strip()
            logger.warning(f"Stripped leaked initial_prompt from transcription")

        # Show detected language if auto-detecting
        if LANGUAGE is None and "language" in result:
            logger.info(f"Detected language: {result['language']}")

        if not text:
            logger.warning("No speech detected (after prompt stripping)")
            return

        # Filter out known Whisper hallucinations
        if text.lower() in HALLUCINATION_PATTERNS:
            logger.warning(f"Filtered hallucination: {text}")
            return

        logger.info(f"Transcribed: {text}")
        self.type_text(text)

    # ========================================================================
    # Audio
    # ========================================================================
    def audio_callback(self, indata, frames, time_info, status):
        """Callback for audio recording"""
        if status:
            logger.warning(f"Audio status: {status}")
        if self.recording:
            self.audio_data.append(indata.copy())

    def trim_silence(self, audio):
        """Trim silence from beginning and end of audio"""
        amplitude = np.abs(audio)
        above_threshold = amplitude > SILENCE_THRESHOLD

        if not np.any(above_threshold):
            return audio  # All silence, return as-is

        non_silent_indices = np.where(above_threshold)[0]
        start_idx = non_silent_indices[0]
        end_idx = non_silent_indices[-1]

        buffer_samples = int(SILENCE_TRIM_MS * SAMPLE_RATE / 1000)
        start_idx = max(0, start_idx - buffer_samples)
        end_idx = min(len(audio), end_idx + buffer_samples)

        trimmed = audio[start_idx:end_idx]

        original_duration = len(audio) / SAMPLE_RATE
        trimmed_duration = len(trimmed) / SAMPLE_RATE
        logger.info(f"Trimmed silence: {original_duration:.2f}s -> {trimmed_duration:.2f}s")

        return trimmed

    def _get_available_input_devices(self):
        """Get list of available input devices, sorted by preference"""
        devices = []
        try:
            all_devices = sd.query_devices()
            for i, dev in enumerate(all_devices):
                if dev['max_input_channels'] > 0:
                    # Prioritize: MacBook mic > other mics > virtual devices
                    priority = 0
                    name_lower = dev['name'].lower()
                    if 'macbook' in name_lower:
                        priority = 100
                    elif 'airpods' in name_lower or 'headphone' in name_lower:
                        priority = 90
                    elif 'iphone' in name_lower:
                        priority = 80
                    elif 'teams' in name_lower or 'zoom' in name_lower:
                        priority = 10  # Virtual devices as last resort
                    else:
                        priority = 50
                    devices.append((priority, i, dev['name']))
            # Sort by priority descending
            devices.sort(key=lambda x: -x[0])
        except Exception as e:
            logger.error(f"Error querying devices: {e}")
        return devices

    def _force_release_stream(self):
        """Force-close the audio stream and release the mic, handling all error cases"""
        stream = self.stream
        self.stream = None
        if stream is not None:
            try:
                if stream.active:
                    stream.stop()
            except Exception as e:
                logger.warning(f"Error stopping stream: {e}")
            try:
                stream.close()
            except Exception as e:
                logger.warning(f"Error closing stream: {e}")

    # ========================================================================
    # Watchdog (runs on main thread)
    # ========================================================================
    def _watchdog_tick(self):
        """Called from the main loop every ~0.5s. Checks if recording is stuck.

        Uses three layers of protection:
        1. Independent key monitor (separate Quartz event tap) - most reliable
        2. Hard max recording duration (300s) - absolute backstop
        3. Diagnostic logging for debugging
        """
        if not self.recording or self._recording_start_time is None:
            self._independent_release_detected_at = None
            return

        now = time.time()
        elapsed = now - self._recording_start_time

        # Verbose logging every WATCHDOG_LOG_INTERVAL seconds during recording
        if (now - self._last_watchdog_log_time) >= WATCHDOG_LOG_INTERVAL:
            monitor_state = self._key_monitor.is_hotkey_held()
            logger.info(
                f"Watchdog tick: recording for {elapsed:.0f}s, "
                f"monitor_held={monitor_state}, "
                f"pynput_ctrl={self.ctrl_pressed}, pynput_space={self.space_pressed}"
            )
            self._last_watchdog_log_time = now

        # Check 1: Hard max recording duration (absolute backstop, always works)
        if elapsed >= WATCHDOG_MAX_RECORDING_SECONDS:
            logger.warning(
                f"Watchdog: max recording duration reached ({elapsed:.0f}s). "
                f"Force-stopping to release mic."
            )
            self._independent_release_detected_at = None
            self.ctrl_pressed = False
            self.space_pressed = False
            self.stop_recording()
            return

        # Check 2: Independent key monitor (separate event tap)
        monitor_result = self._key_monitor.is_hotkey_held()
        if monitor_result is not None and not monitor_result:
            # Keys NOT held according to independent monitor
            if self._independent_release_detected_at is None:
                self._independent_release_detected_at = now
                logger.info(
                    f"Watchdog: independent monitor says keys released "
                    f"(recording for {elapsed:.1f}s). "
                    f"Waiting {WATCHDOG_RELEASE_GRACE_SECONDS}s to confirm..."
                )
            elif (now - self._independent_release_detected_at) >= WATCHDOG_RELEASE_GRACE_SECONDS:
                logger.warning(
                    f"Watchdog: Keys released for "
                    f"{now - self._independent_release_detected_at:.0f}s "
                    f"(independent monitor confirmed). Force-stopping to release mic."
                )
                self._independent_release_detected_at = None
                self.ctrl_pressed = False
                self.space_pressed = False
                self.stop_recording()
                return
        else:
            # Keys still held (or monitor unavailable), reset
            if self._independent_release_detected_at is not None:
                logger.info("Watchdog: monitor says keys held again, resetting")
            self._independent_release_detected_at = None

    # ========================================================================
    # Recording control
    # ========================================================================
    def start_recording(self):
        """Start recording audio with automatic device fallback"""
        if self.recording:
            return
        self.audio_data = []
        self.recording = True
        self._recording_start_time = time.time()
        self._last_watchdog_log_time = 0  # Reset so first tick logs immediately

        # Try default device first, then fallback to others
        devices_to_try = [(None, "default")]  # None = use system default

        # Add fallback devices
        for priority, device_id, name in self._get_available_input_devices():
            devices_to_try.append((device_id, name))

        last_error = None
        for device_id, device_name in devices_to_try:
            try:
                self.stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype=np.float32,
                    callback=self.audio_callback,
                    device=device_id  # None = default
                )
                self.stream.start()
                if device_id is not None:
                    logger.info(f"Recording with fallback device: {device_name}")
                else:
                    logger.info("Recording... (release Ctrl+Space to stop)")
                return  # Success
            except Exception as e:
                last_error = e
                if device_id is None:
                    logger.warning(f"Default audio device failed: {e}")
                else:
                    logger.debug(f"Device '{device_name}' failed: {e}")
                continue

        # All devices failed
        logger.error(f"All audio devices failed. Last error: {last_error}")
        self.recording = False
        self._recording_start_time = None

    def stop_recording(self):
        """Stop recording and queue audio for async transcription.

        CRITICAL: This method returns in <1ms. Transcription happens in the
        background worker thread. This keeps pynput's event tap callback fast,
        preventing macOS from disabling the tap and dropping future key events.
        """
        if not self.recording:
            return
        self.recording = False
        self._recording_start_time = None
        self._independent_release_detected_at = None

        # Always force-release the mic stream FIRST (instant)
        self._force_release_stream()

        # Grab the audio data and queue it for async transcription
        audio_data = self.audio_data
        self.audio_data = []
        if audio_data:
            self._transcription_queue.put(audio_data)
        else:
            logger.warning("No audio recorded")

    # ========================================================================
    # Output
    # ========================================================================
    def type_text(self, text):
        """Type the transcribed text at cursor position"""
        time.sleep(0.1)

        try:
            # Use pbcopy + pbpaste approach for reliability on macOS
            process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
            process.communicate(text.encode('utf-8'))

            # Paste with Cmd+V
            with self.keyboard_controller.pressed(keyboard.Key.cmd):
                self.keyboard_controller.tap('v')

            logger.info("Text pasted!")
        except Exception as e:
            logger.error(f"Error pasting text: {e}")

    # ========================================================================
    # Key handlers (run on pynput's event tap thread - must be FAST)
    # ========================================================================
    def on_press(self, key):
        """Handle key press events"""
        try:
            if key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                self.ctrl_pressed = True
                self._key_monitor.set_pressed(ctrl=True)
            elif key == keyboard.Key.space:
                self.space_pressed = True
                self._key_monitor.set_pressed(space=True)

            # Start recording when both keys are pressed
            if self.ctrl_pressed and self.space_pressed and not self.recording:
                self.start_recording()
        except Exception as e:
            logger.error(f"Key press error: {e}")

    def on_release(self, key):
        """Handle key release events"""
        try:
            if key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                self.ctrl_pressed = False
            elif key == keyboard.Key.space:
                self.space_pressed = False

            # Stop recording when either key is released
            if self.recording and (not self.ctrl_pressed or not self.space_pressed):
                self.stop_recording()

            # Safety: if we're not recording, ensure key state is clean
            # This prevents ghost key states from missed events
            if not self.recording and not self.ctrl_pressed and not self.space_pressed:
                pass  # Normal idle state
        except Exception as e:
            logger.error(f"Key release error: {e}")
            # On any error in release handler, force-stop recording to release mic
            if self.recording:
                logger.warning("Force-stopping recording due to release handler error")
                self.ctrl_pressed = False
                self.space_pressed = False
                self.stop_recording()

    # ========================================================================
    # Lifecycle
    # ========================================================================
    def cleanup(self, signum=None, frame=None):
        """Clean up resources properly"""
        if self._shutdown_requested:
            return
        self._shutdown_requested = True

        logger.info("Shutting down...")
        self.recording = False
        self._recording_start_time = None
        self._independent_release_detected_at = None

        # Stop audio stream
        self._force_release_stream()

        # Signal transcription worker to stop
        self._transcription_queue.put(None)

        # Stop keyboard listener
        if self.listener:
            try:
                self.listener.stop()
            except Exception as e:
                logger.warning(f"Error stopping listener: {e}")
            self.listener = None

        logger.info("Cleanup complete")
        sys.exit(0)

    def run(self):
        """Main loop"""
        # Handle signals gracefully
        signal.signal(signal.SIGINT, self.cleanup)
        signal.signal(signal.SIGTERM, self.cleanup)

        logger.info("=" * 50)
        logger.info("Whisper Dictate Ready!")
        logger.info("=" * 50)
        logger.info(f"Hotkey: Hold Ctrl+Space to record")
        logger.info(f"Model: {MODEL_NAME}")
        logger.info(f"Language: {'Auto-detect' if LANGUAGE is None else LANGUAGE}")
        logger.info(f"Async transcription: enabled")
        logger.info("Press Ctrl+C to quit")
        logger.info("=" * 50)

        # Print to console as well for visibility
        print("\n" + "=" * 50)
        print("Whisper Dictate Ready!")
        print("=" * 50)
        print(f"Hotkey: Hold Ctrl+Space to record")
        print(f"Model: {MODEL_NAME}")
        print(f"Language: {'Auto-detect' if LANGUAGE is None else LANGUAGE}")
        print("Press Ctrl+C to quit")
        print("=" * 50 + "\n")

        self.listener = keyboard.Listener(
            on_press=self.on_press,
            on_release=self.on_release
        )
        self.listener.start()

        try:
            while self.listener.is_alive() and not self._shutdown_requested:
                self.listener.join(timeout=0.5)
                # Main-thread watchdog: check for stuck recording every 0.5s
                self._watchdog_tick()
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()


# ============================================================================
# Entry Point
# ============================================================================
if __name__ == "__main__":
    # Fix multiprocessing semaphore leak warning
    multiprocessing.set_start_method('spawn', force=True)

    logger.info("Starting Whisper Dictate...")
    logger.info(f"Log file: {LOG_FILE}")

    # Verify permissions
    if not verify_permissions():
        logger.error("Permission check failed. Please grant required permissions.")
        sys.exit(1)

    try:
        app = WhisperDictate()
        app.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
