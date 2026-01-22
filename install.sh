#!/bin/bash
#
# Whisper Dictate Installer for macOS
# Hold Ctrl+Space to record, release to transcribe and paste
#
# Usage: curl -fsSL https://raw.githubusercontent.com/YOUR_REPO/install.sh | bash
#

set -e

echo "=========================================="
echo "🎙️  Whisper Dictate Installer"
echo "=========================================="
echo ""

# Check macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo "❌ This script only works on macOS"
    exit 1
fi

# Check for Homebrew
if ! command -v brew &> /dev/null; then
    echo "📦 Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# Install ffmpeg (required by Whisper)
echo "📦 Installing ffmpeg..."
brew install ffmpeg 2>/dev/null || true

# Create directories
echo "📁 Creating directories..."
mkdir -p ~/whisper-dictate
mkdir -p ~/bin
mkdir -p ~/Library/Logs

# Create virtual environment
echo "🐍 Setting up Python environment..."
python3 -m venv ~/whisper-official
source ~/whisper-official/bin/activate

# Install dependencies
echo "📦 Installing Whisper and dependencies..."
pip install --upgrade pip
pip install openai-whisper sounddevice pynput numpy

# Create the dictation script
echo "📝 Creating dictation script..."
cat > ~/whisper-dictate/dictate.py << 'DICTATE_SCRIPT'
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

        print("\nAvailable audio devices:")
        print(sd.query_devices())
        print(f"\nUsing default input device: {sd.query_devices(kind='input')['name']}\n")

    def audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"Audio status: {status}")
        if self.recording:
            self.audio_data.append(indata.copy())

    def trim_silence(self, audio):
        amplitude = np.abs(audio)
        above_threshold = amplitude > SILENCE_THRESHOLD

        if not np.any(above_threshold):
            return audio

        non_silent_indices = np.where(above_threshold)[0]
        start_idx = non_silent_indices[0]
        end_idx = non_silent_indices[-1]

        buffer_samples = int(SILENCE_TRIM_MS * SAMPLE_RATE / 1000)
        start_idx = max(0, start_idx - buffer_samples)
        end_idx = min(len(audio), end_idx + buffer_samples)

        trimmed = audio[start_idx:end_idx]
        original_duration = len(audio) / SAMPLE_RATE
        trimmed_duration = len(trimmed) / SAMPLE_RATE
        print(f"Trimmed silence: {original_duration:.2f}s → {trimmed_duration:.2f}s")

        return trimmed

    def start_recording(self):
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

        audio = np.concatenate(self.audio_data, axis=0).flatten()
        audio_level = np.abs(audio).mean()
        print(f"Audio level: {audio_level:.6f}")

        if audio_level < 0.0001:
            print("⚠️  Audio level too low - check your microphone permissions or input device")
            return

        audio = self.trim_silence(audio)
        print("⏳ Transcribing...")

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

        if LANGUAGE is None and "language" in result:
            print(f"🌐 Detected language: {result['language']}")

        if text:
            print(f"📝 Transcribed: {text}")
            self.type_text(text)
        else:
            print("No speech detected")

    def type_text(self, text):
        time.sleep(0.1)
        process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
        process.communicate(text.encode('utf-8'))
        with self.keyboard_controller.pressed(keyboard.Key.cmd):
            self.keyboard_controller.tap('v')
        print("✅ Text pasted!")

    def on_press(self, key):
        if key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            self.ctrl_pressed = True
        elif key == keyboard.Key.space:
            self.space_pressed = True

        if self.ctrl_pressed and self.space_pressed and not self.recording:
            self.start_recording()

    def on_release(self, key):
        if key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
            self.ctrl_pressed = False
        elif key == keyboard.Key.space:
            self.space_pressed = False

        if self.recording and (not self.ctrl_pressed or not self.space_pressed):
            self.stop_recording()

    def cleanup(self, *args):
        print("\nShutting down...")
        self.recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        if self.listener:
            self.listener.stop()
        sys.exit(0)

    def run(self):
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
DICTATE_SCRIPT

# Create launcher script
echo "📝 Creating launcher script..."
cat > ~/bin/run_whisper_dictate.sh << 'LAUNCHER_SCRIPT'
#!/bin/zsh
# Launch Whisper dictation service as a daemon (prevents duplicates)
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# Check if already running
if pgrep -f "whisper-dictate/dictate.py" > /dev/null; then
    echo "Whisper dictate already running"
    exit 0
fi

cd "$HOME/whisper-dictate"
"$HOME/whisper-official/bin/python3" "$HOME/whisper-dictate/dictate.py" >> "$HOME/Library/Logs/whisper_dictate.log" 2>&1 &
disown
LAUNCHER_SCRIPT

chmod +x ~/bin/run_whisper_dictate.sh

# Pre-download the model
echo "📥 Downloading Whisper model (this may take a few minutes)..."
~/whisper-official/bin/python3 -c "import whisper; whisper.load_model('medium')"

echo ""
echo "=========================================="
echo "✅ Installation Complete!"
echo "=========================================="
echo ""
echo "To start Whisper Dictate:"
echo "  ~/bin/run_whisper_dictate.sh"
echo ""
echo "To auto-start on login:"
echo "  1. Open System Settings → General → Login Items"
echo "  2. Click '+' under 'Open at Login'"
echo "  3. Press Cmd+Shift+G and enter: ~/bin"
echo "  4. Select 'run_whisper_dictate.sh'"
echo ""
echo "Usage: Hold Ctrl+Space to record, release to transcribe"
echo ""
echo "⚠️  IMPORTANT: Grant these permissions in System Settings → Privacy & Security:"
echo "  - Accessibility: Enable for Terminal (or your terminal app)"
echo "  - Microphone: Enable for Terminal (or your terminal app)"
echo ""
echo "Starting Whisper Dictate now..."
~/bin/run_whisper_dictate.sh
echo "Done! Try holding Ctrl+Space and speaking."
