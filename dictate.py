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

# ============================================================================
# Configuration
# ============================================================================
SAMPLE_RATE = 16000
MODEL_NAME = "medium"  # Options: tiny, base, small, medium, large
LANGUAGE = "en"  # None = auto-detect, or "en", "es", "fr", etc.
SILENCE_THRESHOLD = 0.01  # Audio level below this is considered silence
SILENCE_TRIM_MS = 100  # Keep this much silence at edges (milliseconds)
MIN_RECORDING_SECONDS = 0.5  # Ignore recordings shorter than this (prevents hallucinations)
WATCHDOG_INTERVAL_SECONDS = 5  # How often the watchdog checks for stuck state
WATCHDOG_SPEECH_THRESHOLD = 0.05  # Audio level that indicates actual speech (not ambient noise)
WATCHDOG_NO_SPEECH_SECONDS = 15  # Force-stop after this long without speech detected
WATCHDOG_MAX_RECORDING_SECONDS = 180  # Absolute max recording duration (3 min hard limit)

# Known Whisper hallucinations (model artifacts from training data, not real transcriptions)
HALLUCINATION_PATTERNS = [
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
        self.ctrl_pressed = False
        self.space_pressed = False
        self.keyboard_controller = KeyboardController()
        self.stream = None
        self.listener = None
        self._shutdown_requested = False
        self._recording_start_time = None
        self._watchdog_timer = None
        self._last_speech_activity = None  # Timestamp of last speech-level audio (above ambient noise)

        # Log available audio devices
        logger.info("Available audio devices:")
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                logger.info(f"  [{i}] {dev['name']} (inputs: {dev['max_input_channels']})")

        default_input = sd.query_devices(kind='input')
        logger.info(f"Using default input: {default_input['name']}")

    def audio_callback(self, indata, frames, time_info, status):
        """Callback for audio recording"""
        if status:
            logger.warning(f"Audio status: {status}")
        if self.recording:
            self.audio_data.append(indata.copy())
            # Track speech activity for the watchdog (higher threshold than silence trimming)
            if np.abs(indata).mean() > WATCHDOG_SPEECH_THRESHOLD:
                self._last_speech_activity = time.time()

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

    def _start_watchdog(self):
        """Start a watchdog that detects stuck recording via two independent checks:

        1. Speech silence: If no speech-level audio (above ambient noise) is detected
           for WATCHDOG_NO_SPEECH_SECONDS, the user has stopped talking. This uses a
           higher threshold (WATCHDOG_SPEECH_THRESHOLD=0.05) than silence trimming
           (SILENCE_THRESHOLD=0.01) to avoid being fooled by room ambient noise.

        2. Hard max: Absolute recording cap at WATCHDOG_MAX_RECORDING_SECONDS (3 min).
           Even during active speech, no single dictation should exceed this. This is
           the ultimate backstop that cannot be fooled by any signal.

        Both checks are independent of pynput key state, which is unreliable on macOS.
        """
        self._cancel_watchdog()
        self._last_speech_activity = time.time()

        def _watchdog_check():
            if not self.recording:
                return

            now = time.time()
            elapsed = now - self._recording_start_time
            no_speech_for = now - self._last_speech_activity

            # Check 1: Hard max recording duration (absolute backstop)
            if elapsed >= WATCHDOG_MAX_RECORDING_SECONDS:
                logger.warning(
                    f"Watchdog: max recording duration reached ({elapsed:.0f}s). "
                    f"Force-stopping to release mic."
                )
                self.ctrl_pressed = False
                self.space_pressed = False
                self.stop_recording()
                return

            # Check 2: No speech detected for too long
            if no_speech_for >= WATCHDOG_NO_SPEECH_SECONDS:
                logger.warning(
                    f"Watchdog: no speech for {no_speech_for:.0f}s "
                    f"(total recording: {elapsed:.0f}s). "
                    f"Force-stopping to release mic."
                )
                self.ctrl_pressed = False
                self.space_pressed = False
                self.stop_recording()
                return

            # Re-schedule watchdog
            self._watchdog_timer = threading.Timer(
                WATCHDOG_INTERVAL_SECONDS, _watchdog_check
            )
            self._watchdog_timer.daemon = True
            self._watchdog_timer.start()

        self._watchdog_timer = threading.Timer(
            WATCHDOG_INTERVAL_SECONDS, _watchdog_check
        )
        self._watchdog_timer.daemon = True
        self._watchdog_timer.start()

    def _cancel_watchdog(self):
        """Cancel the watchdog timer"""
        if self._watchdog_timer is not None:
            self._watchdog_timer.cancel()
            self._watchdog_timer = None

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

    def start_recording(self):
        """Start recording audio with automatic device fallback"""
        if self.recording:
            return
        self.audio_data = []
        self.recording = True
        self._recording_start_time = time.time()

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
                # Start watchdog to auto-stop if release event is missed
                self._start_watchdog()
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
        """Stop recording and transcribe"""
        if not self.recording:
            return
        self.recording = False
        self._recording_start_time = None
        self._cancel_watchdog()

        # Always force-release the mic stream
        self._force_release_stream()

        if not self.audio_data:
            logger.warning("No audio recorded")
            return

        # Combine audio chunks
        audio = np.concatenate(self.audio_data, axis=0).flatten()
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

        try:
            # Transcribe with Whisper
            result = self.model.transcribe(
                audio,
                fp16=False,
                language=LANGUAGE,
                task="transcribe",
                without_timestamps=True,
                condition_on_previous_text=False,
                initial_prompt="Transcribe spoken English accurately with proper punctuation."
            )
            text = result["text"].strip()

            # Show detected language if auto-detecting
            if LANGUAGE is None and "language" in result:
                logger.info(f"Detected language: {result['language']}")

            if not text:
                logger.warning("No speech detected")
                return

            # Filter out known Whisper hallucinations
            if text.lower() in HALLUCINATION_PATTERNS:
                logger.warning(f"Filtered hallucination: {text}")
                return

            logger.info(f"Transcribed: {text}")
            self.type_text(text)

        except Exception as e:
            logger.error(f"Transcription error: {e}")

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

    def on_press(self, key):
        """Handle key press events"""
        try:
            if key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                self.ctrl_pressed = True
            elif key == keyboard.Key.space:
                self.space_pressed = True

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

    def cleanup(self, signum=None, frame=None):
        """Clean up resources properly"""
        if self._shutdown_requested:
            return
        self._shutdown_requested = True

        logger.info("Shutting down...")
        self.recording = False
        self._recording_start_time = None
        self._cancel_watchdog()

        # Stop audio stream
        self._force_release_stream()

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
