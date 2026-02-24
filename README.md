# Whisper Dictate

A simple, free, and local voice dictation tool for macOS using OpenAI's Whisper model.

**Hold Ctrl+Space to record, release to transcribe and paste.**

## Features

- Hold-to-record hotkey (Ctrl+Space)
- 100% local - your voice never leaves your machine
- Auto-pastes transcribed text at cursor position
- Automatic audio device fallback
- Automatic silence trimming
- Watchdog monitors audio silence to auto-release mic if key-release is missed (15s)
- Robust audio stream cleanup on all exit paths
- Log rotation (max 1MB, keeps 3 backups)

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.9+
- ~1GB disk space for the Whisper model
- ffmpeg (installed automatically)

## Installation

**One-liner (recommended):**

```bash
curl -fsSL https://raw.githubusercontent.com/gbp10/whisper-dictate/main/install.sh | bash
```

**Or manually:**

```bash
git clone https://github.com/gbp10/whisper-dictate.git ~/whisper-dictate
cd ~/whisper-dictate && bash install.sh
```

The installer will:
- Install Homebrew and ffmpeg (if needed)
- Create a Python virtual environment at `~/whisper-official/`
- Install all dependencies (openai-whisper, sounddevice, pynput, numpy)
- Download the Whisper medium model (~769 MB)
- Build the `WhisperDictate.app` with your system's paths
- Create a launcher script at `~/bin/run_whisper_dictate.sh`

## Permissions (IMPORTANT)

After installation, you **must** grant these permissions in **System Settings > Privacy & Security**:

### 1. Accessibility (required for Ctrl+Space hotkey)

1. Open **System Settings > Privacy & Security > Accessibility**
2. Click the **+** button
3. Navigate to `~/whisper-dictate/` and add **WhisperDictate.app**
4. Make sure the toggle is **ON**

### 2. Microphone (required for recording)

1. Open **System Settings > Privacy & Security > Microphone**
2. Enable for **WhisperDictate** (it will appear after the first recording attempt)

> **Note:** You must launch via `WhisperDictate.app` (not `python dictate.py` directly) for permissions to work. The installer configures this automatically.

## Usage

1. Hold **Ctrl+Space** to start recording
2. Speak clearly
3. Release **Ctrl+Space** to transcribe
4. Text is automatically pasted at your cursor

## Managing the Service

```bash
# Start
~/bin/run_whisper_dictate.sh start

# Stop
~/bin/run_whisper_dictate.sh stop

# Restart
~/bin/run_whisper_dictate.sh restart

# Check status
~/bin/run_whisper_dictate.sh status

# View logs
~/bin/run_whisper_dictate.sh logs
```

To auto-start on login, add `WhisperDictate.app` in **System Settings > General > Login Items**.

## Configuration

Edit `~/whisper-dictate/dictate.py` to change settings:

| Setting | Default | Options |
|---------|---------|---------|
| `MODEL_NAME` | `medium` | `tiny`, `base`, `small`, `medium`, `large` |
| `LANGUAGE` | `en` | `en`, `es`, `fr`, `de`, `it`, `None` (auto-detect) |
| `SILENCE_THRESHOLD` | `0.01` | Lower = more sensitive |
| `WATCHDOG_SILENCE_SECONDS` | `15` | Force-stop after this many seconds of silence (catches missed key-release) |
| `LOG_MAX_BYTES` | `1MB` | Max log file size before rotation |

After editing, restart the service:

```bash
~/bin/run_whisper_dictate.sh restart
```

## Model Comparison

| Model | Size | Speed | Accuracy | RAM Usage |
|-------|------|-------|----------|-----------|
| `tiny` | 39 MB | Fastest | Basic | ~1 GB |
| `base` | 74 MB | Fast | Good | ~1 GB |
| `small` | 244 MB | Medium | Better | ~2 GB |
| `medium` | 769 MB | Slow | High | ~5 GB |
| `large` | 1.5 GB | Slowest | Highest | ~10 GB |

## File Locations

```
~/whisper-dictate/
├── dictate.py                  # Main script
├── install.sh                  # Installer
├── requirements.txt            # Python dependencies
├── WhisperDictate.app/         # macOS app bundle (built by installer)
├── dictate.log                 # Current log (rotates at 1MB)
└── README.md                   # This file

~/whisper-official/             # Python virtual environment
~/bin/run_whisper_dictate.sh    # Launcher script
```

## Troubleshooting

### "This process is not trusted"
- Add **WhisperDictate.app** (not Terminal or Python) to **Accessibility** in System Settings
- Re-run the installer if needed: `cd ~/whisper-dictate && bash install.sh`

### "Audio level too low"
- Check **Microphone** permission in System Settings
- Verify correct input device is selected in Sound settings

### Microphone stuck / locked
- This is caused by macOS missing the key-release event, leaving the audio stream open
- The watchdog monitors audio activity and auto-stops after 15 seconds of silence
- To manually recover: `pkill -9 -f dictate.py` then restart

### Hotkey not working
- Ensure Accessibility permission is granted to **WhisperDictate.app**
- Check if another app is using Ctrl+Space
- Make sure you launched via `open WhisperDictate.app` or the launcher script

### Multiple instances running
```bash
pkill -9 -f dictate.py
~/bin/run_whisper_dictate.sh start
```

### View detailed logs
```bash
tail -f ~/whisper-dictate/dictate.log
```

## License

MIT

## Credits

Built with [OpenAI Whisper](https://github.com/openai/whisper)
