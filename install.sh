#!/bin/bash
#
# Whisper Dictate Installer for macOS
# Hold Ctrl+Space to record, release to transcribe and paste
#
# Usage:
#   git clone https://github.com/gbp10/whisper-dictate.git ~/whisper-dictate
#   cd ~/whisper-dictate && bash install.sh
#

set -e

INSTALL_DIR="$HOME/whisper-dictate"
VENV_DIR="$HOME/whisper-official"
BIN_DIR="$HOME/bin"
APP_DIR="$INSTALL_DIR/WhisperDictate.app"

echo "=========================================="
echo "  Whisper Dictate Installer"
echo "=========================================="
echo ""

# Check macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo "ERROR: This script only works on macOS"
    exit 1
fi

# Ensure we're running from the cloned repo
if [ ! -f "$INSTALL_DIR/dictate.py" ]; then
    echo "ERROR: dictate.py not found in $INSTALL_DIR"
    echo ""
    echo "Please clone the repo first:"
    echo "  git clone https://github.com/gbp10/whisper-dictate.git ~/whisper-dictate"
    echo "  cd ~/whisper-dictate && bash install.sh"
    exit 1
fi

# Check for Homebrew
if ! command -v brew &> /dev/null; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# Install ffmpeg (required by Whisper)
echo "Installing ffmpeg..."
brew install ffmpeg 2>/dev/null || true

# Create directories
echo "Creating directories..."
mkdir -p "$BIN_DIR"
mkdir -p "$HOME/Library/Logs"

# Create virtual environment
echo "Setting up Python environment..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# Install dependencies
echo "Installing Whisper and dependencies..."
pip install --upgrade pip
pip install openai-whisper sounddevice pynput numpy

# Build the WhisperDictate.app launcher with the current user's paths
echo "Configuring WhisperDictate.app for your system..."
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

cat > "$APP_DIR/Contents/MacOS/WhisperDictate" << LAUNCHER
#!/bin/bash
# WhisperDictate launcher - runs the Python script with the correct environment

export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"

cd "$INSTALL_DIR"
exec "$VENV_DIR/bin/python3" "$INSTALL_DIR/dictate.py"
LAUNCHER
chmod +x "$APP_DIR/Contents/MacOS/WhisperDictate"

cat > "$APP_DIR/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>WhisperDictate</string>
    <key>CFBundleIdentifier</key>
    <string>com.whisperdictate.app</string>
    <key>CFBundleName</key>
    <string>WhisperDictate</string>
    <key>CFBundleDisplayName</key>
    <string>Whisper Dictate</string>
    <key>CFBundleVersion</key>
    <string>1.1</string>
    <key>CFBundleShortVersionString</key>
    <string>1.1</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.15</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>Whisper Dictate needs microphone access to transcribe your speech.</string>
    <key>NSAppleEventsUsageDescription</key>
    <string>Whisper Dictate needs accessibility access to detect hotkeys and paste text.</string>
</dict>
</plist>
PLIST

# Create launcher shell script
echo "Creating launcher script..."
cat > "$BIN_DIR/run_whisper_dictate.sh" << LAUNCHER_SCRIPT
#!/bin/zsh
# Whisper Dictate launcher - start/stop/restart/status/logs

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

INSTALL_DIR="$INSTALL_DIR"
APP_DIR="$APP_DIR"

case "\${1:-start}" in
    start)
        if pgrep -f "whisper-dictate/dictate.py" > /dev/null; then
            echo "Whisper Dictate is already running (PID: \$(pgrep -f 'whisper-dictate/dictate.py'))"
            exit 0
        fi
        echo "Starting Whisper Dictate..."
        open "\$APP_DIR"
        echo "Started. Use Ctrl+Space to record."
        ;;
    stop)
        if pgrep -f "whisper-dictate/dictate.py" > /dev/null; then
            pkill -f "whisper-dictate/dictate.py"
            echo "Whisper Dictate stopped."
        else
            echo "Whisper Dictate is not running."
        fi
        ;;
    restart)
        "\$0" stop
        sleep 2
        "\$0" start
        ;;
    status)
        if pgrep -f "whisper-dictate/dictate.py" > /dev/null; then
            echo "Whisper Dictate is running (PID: \$(pgrep -f 'whisper-dictate/dictate.py'))"
        else
            echo "Whisper Dictate is not running."
        fi
        ;;
    logs)
        tail -f "\$INSTALL_DIR/dictate.log"
        ;;
    *)
        echo "Usage: \$0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
LAUNCHER_SCRIPT
chmod +x "$BIN_DIR/run_whisper_dictate.sh"

# Pre-download the model
echo "Downloading Whisper model (this may take a few minutes)..."
"$VENV_DIR/bin/python3" -c "import whisper; whisper.load_model('medium')"

echo ""
echo "=========================================="
echo "  Installation Complete!"
echo "=========================================="
echo ""
echo "IMPORTANT - Grant these permissions in System Settings > Privacy & Security:"
echo ""
echo "  1. ACCESSIBILITY (for Ctrl+Space hotkey):"
echo "     - Go to Privacy & Security > Accessibility"
echo "     - Click + and add: $APP_DIR"
echo "     - Make sure the toggle is ON"
echo ""
echo "  2. MICROPHONE (for voice recording):"
echo "     - Go to Privacy & Security > Microphone"
echo "     - Enable for WhisperDictate (will prompt on first use)"
echo ""
echo "To start:"
echo "  open $APP_DIR"
echo "  # or: ~/bin/run_whisper_dictate.sh start"
echo ""
echo "To auto-start on login:"
echo "  System Settings > General > Login Items > add WhisperDictate.app"
echo ""
echo "Usage: Hold Ctrl+Space to record, release to transcribe and paste."
echo ""
echo "Starting Whisper Dictate now..."
open "$APP_DIR"
echo "Done! Try holding Ctrl+Space and speaking."
