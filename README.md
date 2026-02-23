# Whisper Dictate

A simple, free, and local voice dictation tool for macOS using OpenAI's Whisper model.

**Hold Ctrl+Space to record, release to transcribe and paste.**

## Features

- Hold-to-record hotkey (Ctrl+Space)
- 100% local - your voice never leaves your machine
- Auto-pastes transcribed text at cursor position
- Runs in background via launchd, starts on boot
- Automatic silence trimming
- Watchdog timer prevents mic from getting stuck (auto-stops after 5 min)
- Robust audio stream cleanup on all exit paths
- Log rotation (max 1MB, keeps 3 backups)

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.9+
- ~1GB disk space for the Whisper model
- ffmpeg (installed automatically)

## Quick Start

```bash
# Start manually
~/bin/run_whisper_dictate.sh start

# Check status
~/bin/run_whisper_dictate.sh status

# View logs
~/bin/run_whisper_dictate.sh logs

# Stop
~/bin/run_whisper_dictate.sh stop

# Restart
~/bin/run_whisper_dictate.sh restart
```

## Permissions Required (IMPORTANT)

After installation, you MUST grant these permissions in **System Settings -> Privacy & Security**:

### 1. Accessibility (Required for Hotkey)

The Python app needs Accessibility permission to monitor keyboard events (Ctrl+Space).

1. Open **System Settings -> Privacy & Security -> Accessibility**
2. Click the **+** button
3. Navigate to: `/opt/homebrew/Cellar/python@3.14/3.14.0_1/Frameworks/Python.framework/Versions/3.14/Resources/Python.app`
   - Or simply add **Terminal.app** (easier)
4. Enable the checkbox

### 2. Microphone (Required for Recording)

1. Open **System Settings -> Privacy & Security -> Microphone**
2. Enable for **Terminal.app** (or your terminal)

## Usage

1. Hold **Ctrl+Space** to start recording
2. Speak clearly
3. Release **Ctrl+Space** to transcribe
4. Text is automatically pasted at your cursor

## Service Management

The service runs as a launchd agent that starts automatically on login.

```bash
# Manual control
~/bin/run_whisper_dictate.sh start|stop|restart|status|logs

# Or use launchctl directly
launchctl load ~/Library/LaunchAgents/com.gerardob.whisperdictate.plist
launchctl unload ~/Library/LaunchAgents/com.gerardob.whisperdictate.plist
```

## Configuration

Edit `~/whisper-dictate/dictate.py` to change settings:

| Setting | Default | Options |
|---------|---------|---------|
| `MODEL_NAME` | `medium` | `tiny`, `base`, `small`, `medium`, `large` |
| `LANGUAGE` | `en` | `en`, `es`, `fr`, `de`, `it`, `None` (auto-detect) |
| `SILENCE_THRESHOLD` | `0.01` | Lower = more sensitive |
| `MAX_RECORDING_SECONDS` | `300` | Auto-stop safety timeout (seconds) |
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
├── dictate.py              # Main script
├── dictate.log             # Current log (rotates at 1MB)
├── dictate.log.1           # Backup log 1
├── dictate.log.2           # Backup log 2
├── dictate.log.3           # Backup log 3
├── .dictate.pid            # PID file for launcher
└── README.md               # This file

~/whisper-official/          # Python virtual environment
~/bin/run_whisper_dictate.sh # Launcher script
~/Library/LaunchAgents/com.gerardob.whisperdictate.plist  # launchd config
```

## Troubleshooting

### "This process is not trusted"
- Add Python.app or Terminal.app to **Accessibility** in System Settings

### "Audio level too low"
- Check **Microphone** permission in System Settings
- Verify correct input device is selected in Sound settings

### Microphone stuck / locked
- This is caused by macOS missing the key-release event, leaving the audio stream open
- The watchdog timer will auto-stop recording after 5 minutes as a safety net
- To manually recover: `pkill -9 -f dictate.py` then restart

### Hotkey not working
- Ensure Accessibility permission is granted
- Check if another app is using Ctrl+Space
- If launched from terminal, use `open WhisperDictate.app` instead of running `dictate.py` directly

### Multiple instances running
```bash
pkill -9 -f dictate.py
~/bin/run_whisper_dictate.sh start
```

### View detailed logs
```bash
tail -f ~/whisper-dictate/dictate.log
```

### Force restart
```bash
launchctl unload ~/Library/LaunchAgents/com.gerardob.whisperdictate.plist
pkill -9 -f dictate.py
launchctl load ~/Library/LaunchAgents/com.gerardob.whisperdictate.plist
```

## License

MIT

## Credits

Built with [OpenAI Whisper](https://github.com/openai/whisper)
