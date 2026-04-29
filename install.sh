#!/bin/bash
#
# Whisper Dictate Installer for macOS
# Hold Ctrl+Space to record, release to transcribe and paste
#
# Usage (one-liner):
#   curl -fsSL https://raw.githubusercontent.com/gbp10/whisper-dictate/main/install.sh | bash
#
# Or manually:
#   git clone https://github.com/gbp10/whisper-dictate.git ~/whisper-dictate
#   cd ~/whisper-dictate && bash install.sh
#

set -e

REPO_URL="https://github.com/gbp10/whisper-dictate.git"
INSTALL_DIR="$HOME/whisper-dictate"
VENV_DIR="$HOME/whisper-official"
BIN_DIR="$HOME/bin"
APP_DIR="$INSTALL_DIR/WhisperDictate.app"
LAUNCHD_LABEL="com.whisperdictate"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"

echo "=========================================="
echo "  Whisper Dictate Installer"
echo "=========================================="
echo ""

# Check macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo "ERROR: This script only works on macOS"
    exit 1
fi

# Clone or update the repo if dictate.py is not present
if [ ! -f "$INSTALL_DIR/dictate.py" ]; then
    echo "Downloading Whisper Dictate..."
    if [ -d "$INSTALL_DIR" ]; then
        echo "Directory $INSTALL_DIR exists but is incomplete. Removing and re-cloning..."
        rm -rf "$INSTALL_DIR"
    fi
    git clone "$REPO_URL" "$INSTALL_DIR"
else
    echo "Whisper Dictate repo found at $INSTALL_DIR"
    # Pull latest changes if it's a git repo
    if [ -d "$INSTALL_DIR/.git" ]; then
        echo "Updating to latest version..."
        git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || true
    fi
fi

# Check for Homebrew
if ! command -v brew &> /dev/null; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# Install ffmpeg (required by Whisper). Don't swallow errors — Whisper won't
# work without it, so a silent failure here would surface much later.
echo "Installing ffmpeg..."
if ! brew list ffmpeg >/dev/null 2>&1; then
    brew install ffmpeg
fi

# Create directories
echo "Creating directories..."
mkdir -p "$BIN_DIR"
mkdir -p "$(dirname "$LAUNCHD_PLIST")"

# Create virtual environment
echo "Setting up Python environment..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# Install dependencies from requirements.txt (single source of truth)
echo "Installing Whisper and dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install -r "$INSTALL_DIR/requirements.txt"

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

# Create launchd plist for KeepAlive supervision.
# This is what makes the service self-heal on stuck-mic deadlocks: when
# dictate.py detects a hung PortAudio close it calls os._exit(75), and
# launchd respawns it because SuccessfulExit is false.
echo "Configuring launchd supervision..."
cat > "$LAUNCHD_PLIST" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LAUNCHD_LABEL</string>
    <key>Program</key>
    <string>$APP_DIR/Contents/MacOS/WhisperDictate</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>$INSTALL_DIR/dictate.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$INSTALL_DIR/dictate.stderr.log</string>
</dict>
</plist>
PLIST

# Validate plist syntax — catches typos before launchctl rejects them silently
plutil -lint "$LAUNCHD_PLIST" >/dev/null

# Create launcher shell script — wraps launchctl for friendly start/stop/status
echo "Creating launcher script..."
cat > "$BIN_DIR/run_whisper_dictate.sh" << LAUNCHER_SCRIPT
#!/bin/zsh
# Whisper Dictate launcher — start/stop/restart/status/logs via launchctl.
# launchd's KeepAlive automatically respawns the service after self-restart
# (os._exit code 75) on stuck-mic recovery.

INSTALL_DIR="$INSTALL_DIR"
LAUNCHD_PLIST="$LAUNCHD_PLIST"
LAUNCHD_LABEL="$LAUNCHD_LABEL"

case "\${1:-start}" in
    start)
        if [ ! -f "\$LAUNCHD_PLIST" ]; then
            echo "ERROR: \$LAUNCHD_PLIST not found. Run install.sh first."
            exit 1
        fi
        launchctl load "\$LAUNCHD_PLIST" 2>/dev/null && \\
            echo "Whisper Dictate started under launchd. Use Ctrl+Space to record." || \\
            echo "Whisper Dictate is already loaded. Use 'restart' to apply changes."
        ;;
    stop)
        launchctl unload "\$LAUNCHD_PLIST" 2>/dev/null || true
        # Belt-and-suspenders: kill any stragglers running outside launchd
        pkill -f "whisper-dictate/dictate.py" 2>/dev/null || true
        echo "Whisper Dictate stopped."
        ;;
    restart)
        "\$0" stop
        sleep 2
        "\$0" start
        ;;
    status)
        if launchctl list "\$LAUNCHD_LABEL" >/dev/null 2>&1; then
            PID=\$(launchctl list "\$LAUNCHD_LABEL" 2>/dev/null | awk -F'=' '/"PID"/{gsub(/[^0-9]/,"",\$2); print \$2}')
            if [ -n "\$PID" ]; then
                echo "Whisper Dictate is running (PID: \$PID, supervised by launchd)."
            else
                echo "Whisper Dictate is loaded in launchd but not running (will respawn)."
            fi
        else
            echo "Whisper Dictate is not loaded in launchd. Run 'start' to load it."
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

# Stop any existing instance before launchctl takes over (avoids double-launch)
echo "Stopping any existing instances before handing off to launchd..."
pkill -f "whisper-dictate/dictate.py" 2>/dev/null || true
launchctl unload "$LAUNCHD_PLIST" 2>/dev/null || true
sleep 1

# Load launchd agent — this also starts the service via RunAtLoad=true
echo "Loading launchd agent ($LAUNCHD_LABEL)..."
launchctl load "$LAUNCHD_PLIST"

echo ""
echo "=========================================="
echo "  Installation Complete!"
echo "=========================================="
echo ""
echo "IMPORTANT — Grant these permissions in System Settings > Privacy & Security:"
echo ""
echo "  1. ACCESSIBILITY (for Ctrl+Space hotkey):"
echo "     - Go to Privacy & Security > Accessibility"
echo "     - Click + and add: $APP_DIR"
echo "     - Make sure the toggle is ON"
echo ""
echo "  2. MICROPHONE (for voice recording):"
echo "     - Go to Privacy & Security > Microphone"
echo "     - Enable for WhisperDictate (prompts on first recording)"
echo ""
echo "Service management:"
echo "  ~/bin/run_whisper_dictate.sh {start|stop|restart|status|logs}"
echo ""
echo "Auto-start on login: already configured via launchd (RunAtLoad=true)."
echo "If you previously added WhisperDictate to System Settings > Login Items,"
echo "REMOVE it now — otherwise you'll get duplicate processes."
echo ""
echo "Self-heal: if the audio stream ever hangs, the process exits with code 75"
echo "and launchd respawns it automatically (no more stuck mic forever)."
echo ""
echo "Usage: Press Ctrl+Space to start recording. Press again to stop and paste."
echo ""
echo "Done! Service is running. Try Ctrl+Space and speak."
