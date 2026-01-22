# Whisper Dictate

A simple, free, and local voice dictation tool for macOS using OpenAI's Whisper model.

**Hold Ctrl+Space to record, release to transcribe and paste.**

## Features

- 🎤 Hold-to-record hotkey (Ctrl+Space)
- 🔒 100% local - your voice never leaves your machine
- 📋 Auto-pastes transcribed text at cursor position
- 🚀 Runs in background, starts on boot
- 🔇 Automatic silence trimming

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.9+
- ~1GB disk space for the Whisper model

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/gbp10/whisper-dictate/main/install.sh | bash
```

Or manually:

```bash
git clone https://github.com/gbp10/whisper-dictate.git
cd whisper-dictate
chmod +x install.sh
./install.sh
```

## Permissions Required

After installation, grant these permissions in **System Settings → Privacy & Security**:

- **Accessibility**: Enable for Terminal (or your terminal app)
- **Microphone**: Enable for Terminal (or your terminal app)

## Usage

1. Hold **Ctrl+Space** to start recording
2. Speak
3. Release **Ctrl+Space** to transcribe
4. Text is automatically pasted at your cursor

## Commands

```bash
# Start manually
~/bin/run_whisper_dictate.sh

# Check if running
ps aux | grep dictate

# Stop
pkill -f dictate.py

# View logs
tail -f ~/Library/Logs/whisper_dictate.log
```

## Auto-Start on Login

1. Open **System Settings → General → Login Items**
2. Click **+** under "Open at Login"
3. Press **Cmd+Shift+G** and enter: `~/bin`
4. Select `run_whisper_dictate.sh`

## Configuration

Edit `~/whisper-dictate/dictate.py` to change:

| Setting | Default | Options |
|---------|---------|---------|
| `MODEL_NAME` | `medium` | `tiny`, `base`, `small`, `medium`, `large` |
| `LANGUAGE` | `en` | `en`, `es`, `fr`, `de`, `it`, `None` (auto-detect) |
| `SILENCE_THRESHOLD` | `0.01` | Lower = more sensitive |

After editing, restart:

```bash
pkill -f dictate.py && ~/bin/run_whisper_dictate.sh
```

## Model Comparison

| Model | Size | Speed | Accuracy |
|-------|------|-------|----------|
| `tiny` | 39 MB | Fastest | Basic |
| `base` | 74 MB | Fast | Good |
| `small` | 244 MB | Medium | Better |
| `medium` | 769 MB | Slow | High |
| `large` | 1.5 GB | Slowest | Highest |

## Troubleshooting

**"Audio level too low"**
- Check microphone permissions in System Settings
- Make sure correct input device is selected

**"This process is not trusted"**
- Add Terminal to Accessibility in System Settings

**Multiple instances running**
- Run: `pkill -9 -f dictate.py && ~/bin/run_whisper_dictate.sh`

## License

MIT

## Credits

Built with [OpenAI Whisper](https://github.com/openai/whisper)
