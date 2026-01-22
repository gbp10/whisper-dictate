#!/usr/bin/env python3
"""
Whisper Dictate - Global hotkey dictation using OpenAI Whisper
Hold Ctrl+Space to record, release to stop and transcribe.
Transcribed text is automatically typed at your cursor position.
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

# Configuration
SAMPLE_RATE = 16000
MODEL_NAME = "medium"  # Options: tiny, base, small, medium, large
LANGUAGE = "en"  # None = auto-detect, or "en", "es", "fr", etc.
SILENCE_THRESHOLD = 0.01  # Audio level below this is considered silence
SILENCE_TRIM_MS = 100  # Keep this much silence at edges (milliseconds)

class WhisperDictate:
    def __init__(self):
        print("Loading Whisper model... (this may take a moment)")
        self.model = whisper.load_model(MODEL_NAME)
        print(f"Model '{MODEL_NAME}' loaded successfully!")

        self.recording = False
        self.audio_data = []
        self.ctrl_pressed = False
        self.space_pressed = False
        self.keyboard_controller = KeyboardController()
        self.stream = None
        self.listener = None

        # List available audio devices
        print("\nAvailable audio devices:")
        print(sd.query_devices())
        print(f"\nUsing default input device: {sd.query_devices(kind='input')['name']}\n")

    def audio_callback(self, indata, frames, time_info, status):
        """Callback for audio recording"""
        if status:
            print(f"Audio status: {status}")
        if self.recording:
            self.audio_data.append(indata.copy())

    def trim_silence(self, audio):
        """Trim silence from beginning and end of audio"""
        # Calculate the amplitude envelope
        amplitude = np.abs(audio)

        # Find where audio exceeds threshold
        above_threshold = amplitude > SILENCE_THRESHOLD

        if not np.any(above_threshold):
            return audio  # All silence, return as-is

        # Find first and last non-silent samples
        non_silent_indices = np.where(above_threshold)[0]
        start_idx = non_silent_indices[0]
        end_idx = non_silent_indices[-1]

        # Add small buffer (convert ms to samples)
        buffer_samples = int(SILENCE_TRIM_MS * SAMPLE_RATE / 1000)
        start_idx = max(0, start_idx - buffer_samples)
        end_idx = min(len(audio), end_idx + buffer_samples)

        trimmed = audio[start_idx:end_idx]

        original_duration = len(audio) / SAMPLE_RATE
        trimmed_duration = len(trimmed) / SAMPLE_RATE
        print(f"Trimmed silence: {original_duration:.2f}s → {trimmed_duration:.2f}s")

        return trimmed

    def start_recording(self):
        """Start recording audio"""
        if self.recording:
            return
        self.audio_data = []
        self.recording = True
        try:
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype=np.float32,
                callback=self.audio_callback
            )
            self.stream.start()
            print("🎤 Recording... (release Ctrl+Space to stop)")
        except Exception as e:
            print(f"Error starting recording: {e}")
            self.recording = False

    def stop_recording(self):
        """Stop recording and transcribe"""
        if not self.recording:
            return
        self.recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        if not self.audio_data:
            print("No audio recorded")
            return

        # Combine audio chunks
        audio = np.concatenate(self.audio_data, axis=0).flatten()

        # Check if there's actual audio content
        audio_level = np.abs(audio).mean()
        print(f"Audio level: {audio_level:.6f}")

        if audio_level < 0.0001:
            print("⚠️  Audio level too low - check your microphone permissions or input device")
            return

        # Trim silence
        audio = self.trim_silence(audio)

        print("⏳ Transcribing...")

        # Transcribe with Whisper (task="transcribe" keeps original language, not translate)
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
            print(f"🌐 Detected language: {result['language']}")

        if text:
            print(f"📝 Transcribed: {text}")
            self.type_text(text)
        else:
            print("No speech detected")

    def type_text(self, text):
        """Type the transcribed text at cursor position"""
        time.sleep(0.1)

        # Use pbcopy + pbpaste approach for reliability on macOS
        process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
        process.communicate(text.encode('utf-8'))

        # Paste with Cmd+V
        with self.keyboard_controller.pressed(keyboard.Key.cmd):
            self.keyboard_controller.tap('v')

        print("✅ Text pasted!")

    def on_press(self, key):
        """Handle key press events"""
        if key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            self.ctrl_pressed = True
        elif key == keyboard.Key.space:
            self.space_pressed = True

        # Start recording when both keys are pressed
        if self.ctrl_pressed and self.space_pressed and not self.recording:
            self.start_recording()

    def on_release(self, key):
        """Handle key release events"""
        if key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            self.ctrl_pressed = False
        elif key == keyboard.Key.space:
            self.space_pressed = False

        # Stop recording when either key is released
        if self.recording and (not self.ctrl_pressed or not self.space_pressed):
            self.stop_recording()

    def cleanup(self, *args):
        """Clean up resources"""
        print("\nShutting down...")
        self.recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        if self.listener:
            self.listener.stop()
        sys.exit(0)

    def run(self):
        """Main loop"""
        # Handle Ctrl+C gracefully
        signal.signal(signal.SIGINT, self.cleanup)
        signal.signal(signal.SIGTERM, self.cleanup)

        print("\n" + "="*50)
        print("🎙️  Whisper Dictate Ready!")
        print("="*50)
        print(f"Hotkey: Hold Ctrl+Space to record")
        print(f"Model: {MODEL_NAME}")
        print(f"Language: {'Auto-detect' if LANGUAGE is None else LANGUAGE}")
        print("Press Ctrl+C to quit")
        print("="*50 + "\n")

        self.listener = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
        self.listener.start()

        try:
            while self.listener.is_alive():
                self.listener.join(timeout=0.5)
        except KeyboardInterrupt:
            self.cleanup()

if __name__ == "__main__":
    app = WhisperDictate()
    app.run()
