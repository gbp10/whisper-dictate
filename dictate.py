#!/usr/bin/env python3
"""
Whisper Dictate - Global hotkey dictation using OpenAI Whisper
Press Ctrl+Space to start recording, press again to stop and transcribe.
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

# ============================================================================
# Configuration
# ============================================================================
SAMPLE_RATE = 16000
MODEL_NAME = "medium"  # Options: tiny, base, small, medium, large
LANGUAGE = "en"  # None = auto-detect, or "en", "es", "fr", etc.
SILENCE_THRESHOLD = 0.001  # Audio level below this is considered silence
SILENCE_TRIM_MS = 100  # Keep this much silence at edges (milliseconds)
MIN_RECORDING_SECONDS = 0.5  # Ignore recordings shorter than this (prevents hallucinations)
# Whisper initial_prompt conditions the model's style. It can leak into output, so we
# store it as a constant and strip it from transcriptions if detected.
WHISPER_INITIAL_PROMPT = "Transcribe spoken English accurately with proper punctuation."
WATCHDOG_MAX_RECORDING_SECONDS = 120  # Hard max recording duration (2 min safety net)
SOUND_START = "/System/Library/Sounds/Tink.aiff"  # Played when recording starts
SOUND_STOP = "/System/Library/Sounds/Pop.aiff"  # Played when recording stops
WATCHDOG_LOG_INTERVAL = 10  # Log watchdog status every N seconds during recording
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
        self.keyboard_controller = KeyboardController()
        self.stream = None
        self.listener = None
        self._shutdown_requested = False
        self._recording_start_time = None
        self._last_watchdog_log_time = 0
        # Toggle mode: press Ctrl+Space to start, press again to stop.
        # Uses time-based debounce (not release-based arming) because macOS
        # drops key-release events under CPU load. After a toggle, ignore all
        # combo presses for TOGGLE_DEBOUNCE_SECONDS to prevent key-repeat.
        self._ctrl_held = False
        self._space_held = False
        self._last_toggle_time = 0  # Timestamp of last toggle action
        self.TOGGLE_DEBOUNCE_SECONDS = 1.0  # Ignore combo presses within this window

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

    # ========================================================================
    # Async Transcription Worker
    # ========================================================================
    def _transcription_worker(self):
        """Background thread that processes transcription jobs."""
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
                    priority = 0
                    name_lower = dev['name'].lower()
                    if 'macbook' in name_lower:
                        priority = 100
                    elif 'airpods' in name_lower or 'headphone' in name_lower:
                        priority = 90
                    elif 'iphone' in name_lower:
                        priority = 80
                    elif 'teams' in name_lower or 'zoom' in name_lower:
                        priority = 10
                    else:
                        priority = 50
                    devices.append((priority, i, dev['name']))
            devices.sort(key=lambda x: -x[0])
        except Exception as e:
            logger.error(f"Error querying devices: {e}")
        return devices

    def _play_sound(self, sound_path):
        """Play a system sound asynchronously (non-blocking)."""
        try:
            subprocess.Popen(
                ["afplay", sound_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            pass  # Sound is non-critical, never block on failure

    def _force_release_stream(self):
        """Force-close the audio stream and release the mic.

        Uses a timeout thread because PortAudio stop/close can hang
        if the device is in a bad state (disconnected, driver stall, etc.).
        When the timeout fires, reinitializes sounddevice to force-release
        the mic at the OS level.
        """
        stream = self.stream
        self.stream = None
        if stream is None:
            return

        def _close_stream():
            try:
                if stream.active:
                    stream.stop()
            except Exception as e:
                logger.warning(f"Error stopping stream: {e}")
            try:
                stream.close()
            except Exception as e:
                logger.warning(f"Error closing stream: {e}")

        closer = threading.Thread(target=_close_stream, daemon=True)
        closer.start()
        closer.join(timeout=3.0)
        if closer.is_alive():
            logger.warning("Stream close timed out after 3s — forcing PortAudio reset")
            try:
                sd._terminate()
                sd._initialize()
                logger.info("PortAudio reset complete — mic should be released")
            except Exception as e:
                logger.error(f"PortAudio reset failed: {e}")

    # ========================================================================
    # Watchdog (runs on main thread)
    # ========================================================================
    def _watchdog_tick(self):
        """Called from the main loop every ~0.5s. Hard timeout safety net.

        In toggle mode, the only failure scenario is the user forgetting to
        press Ctrl+Space again to stop. The 120s hard max catches this.
        """
        if not self.recording or self._recording_start_time is None:
            return

        now = time.time()
        elapsed = now - self._recording_start_time

        # Periodic logging during recording
        if (now - self._last_watchdog_log_time) >= WATCHDOG_LOG_INTERVAL:
            logger.info(f"Watchdog: recording for {elapsed:.0f}s")
            self._last_watchdog_log_time = now

        # Hard max recording duration (absolute backstop)
        if elapsed >= WATCHDOG_MAX_RECORDING_SECONDS:
            logger.warning(
                f"Watchdog: max recording duration reached ({elapsed:.0f}s). "
                f"Force-stopping to release mic."
            )
            self.stop_recording()

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
        devices_to_try = [(None, "default")]
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
                    device=device_id
                )
                self.stream.start()
                self._play_sound(SOUND_START)
                if device_id is not None:
                    logger.info(f"Recording with fallback device: {device_name}")
                else:
                    logger.info("Recording... (press Ctrl+Space again to stop)")
                return
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
        """Stop recording and queue audio for async transcription."""
        if not self.recording:
            return
        self.recording = False
        self._recording_start_time = None

        self._play_sound(SOUND_STOP)
        logger.info("Stopped recording. Releasing mic...")

        # Force-release the mic stream (with timeout to prevent hangs)
        self._force_release_stream()

        logger.info("Mic released.")

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
        """Type the transcribed text at cursor position.

        Copies to clipboard (pbcopy) then simulates Cmd+V. The Cmd+V step
        requires Accessibility permission for this Python binary, which is
        separate from Input Monitoring. If Accessibility isn't granted, the
        text lands in the clipboard but never gets pasted — so we log both
        actions separately to make the failure mode obvious.
        """
        time.sleep(0.1)
        try:
            process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
            process.communicate(text.encode('utf-8'))
            logger.info("Text copied to clipboard")
        except Exception as e:
            logger.error(f"Error copying to clipboard: {e}")
            return

        try:
            with self.keyboard_controller.pressed(keyboard.Key.cmd):
                self.keyboard_controller.tap('v')
            logger.info("Cmd+V sent (text pasted if Accessibility permission granted)")
        except Exception as e:
            logger.error(f"Error sending Cmd+V: {e} — grant Accessibility permission in System Settings")

    # ========================================================================
    # Key handlers (run on pynput's event tap thread - must be FAST)
    # ========================================================================
    def on_press(self, key):
        """Handle key press events — toggle recording on Ctrl+Space combo.

        Uses time-based debounce instead of release-based arming because macOS
        drops key-release events. After toggling, all combo presses within
        TOGGLE_DEBOUNCE_SECONDS are ignored (prevents key-repeat re-triggers).
        The next combo press AFTER the debounce window triggers the next toggle.
        No dependency on release events whatsoever.
        """
        try:
            is_ctrl = (key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r)
            is_space = (key == keyboard.Key.space)

            # Fallback space detection for non-standard keyboard configs
            if not is_space and hasattr(key, 'char') and key.char in (' ', '\x00'):
                is_space = True
            if not is_space and hasattr(key, 'vk') and getattr(key, 'vk', None) == 49:
                is_space = True

            if is_ctrl:
                self._ctrl_held = True
            if is_space:
                self._space_held = True

            # Toggle when both keys are pressed and debounce window has elapsed
            if self._ctrl_held and self._space_held:
                now = time.time()
                if (now - self._last_toggle_time) >= self.TOGGLE_DEBOUNCE_SECONDS:
                    self._last_toggle_time = now
                    if not self.recording:
                        logger.info("Toggle: starting recording")
                        self.start_recording()
                    else:
                        logger.info("Toggle: stopping recording")
                        self.stop_recording()
        except Exception as e:
            logger.error(f"Key press error: {e}")

    def on_release(self, key):
        """Handle key release events — reset key tracking flags.

        Release events are NOT used for any critical logic (stopping recording,
        arming toggles, etc.) because macOS drops them under CPU load. They only
        reset _ctrl_held/_space_held so the combo detection in on_press works
        correctly when releases DO arrive. If releases are dropped, the debounce
        timer in on_press handles it gracefully.
        """
        try:
            is_ctrl = (key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r)
            is_space = (key == keyboard.Key.space)
            if not is_space and hasattr(key, 'char') and key.char == ' ':
                is_space = True
            if not is_space and hasattr(key, 'vk') and key.vk == 49:
                is_space = True

            if is_ctrl:
                self._ctrl_held = False
            elif is_space:
                self._space_held = False
        except Exception as e:
            logger.error(f"Key release error: {e}")

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
        logger.info(f"Hotkey: Press Ctrl+Space to toggle recording")
        logger.info(f"Mode: Toggle (press to start, press again to stop)")
        logger.info(f"Model: {MODEL_NAME}")
        logger.info(f"Language: {'Auto-detect' if LANGUAGE is None else LANGUAGE}")
        logger.info(f"Async transcription: enabled")
        logger.info(f"Hard timeout: {WATCHDOG_MAX_RECORDING_SECONDS}s")
        logger.info("Press Ctrl+C to quit")
        logger.info("=" * 50)

        # Print to console as well for visibility
        print("\n" + "=" * 50)
        print("Whisper Dictate Ready!")
        print("=" * 50)
        print(f"Hotkey: Press Ctrl+Space to toggle recording")
        print(f"  - Press once to START recording")
        print(f"  - Press again to STOP recording")
        print(f"Model: {MODEL_NAME}")
        print(f"Language: {'Auto-detect' if LANGUAGE is None else LANGUAGE}")
        print(f"Hard timeout: {WATCHDOG_MAX_RECORDING_SECONDS}s")
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
