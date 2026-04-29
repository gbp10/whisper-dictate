# Whisper Dictate

A simple, free, local voice dictation tool for macOS using OpenAI's Whisper model.

**Press Ctrl+Space to start recording, press Ctrl+Space again to stop and paste.** (Toggle mode.)

## Features

- Toggle hotkey (Ctrl+Space) — press once to start, press again to stop
- 100% local — your voice never leaves your machine
- **Streaming output (VAD)**: phrases appear at your cursor as you pause speaking,
  not all-at-once at the end. Disable with `STREAMING_MODE = False` for legacy mode.
- Auto-pastes transcribed text at cursor position
- Automatic audio device selection with fallback
- Automatic silence trimming
- Async transcription on a worker thread (keyboard listener never blocks)
- macOS notification on recording start (subtle "is it on?" feedback)
- Whisper hallucination filter (drops "Thanks for watching", etc.)
- Single-instance lock (won't double-launch)
- **Self-heal on stuck mic**: if PortAudio's stream-close ever hangs,
  the process exits with code 75 and launchd respawns it automatically
- Log rotation (1MB max, 3 backups)

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.9+
- ~1GB disk space for the Whisper `medium` model
- ffmpeg (installed automatically by `install.sh`)
- Homebrew (installed automatically if missing)

## Installation

**One-liner:**

```bash
curl -fsSL https://raw.githubusercontent.com/gbp10/whisper-dictate/main/install.sh | bash
```

**Manual:**

```bash
git clone https://github.com/gbp10/whisper-dictate.git ~/whisper-dictate
cd ~/whisper-dictate && bash install.sh
```

The installer will:
- Install Homebrew and ffmpeg (if missing)
- Create a Python virtual environment at `~/whisper-official/`
- Install dependencies from `requirements.txt`
- Download the Whisper `medium` model (~769 MB)
- Build `WhisperDictate.app` with paths interpolated for your system
- Create the launcher at `~/bin/run_whisper_dictate.sh`
- Install and load a launchd agent at `~/Library/LaunchAgents/com.whisperdictate.plist`
  with `KeepAlive` so the service self-heals after stuck-mic recovery

## Permissions (IMPORTANT)

After installation, grant these in **System Settings > Privacy & Security**:

### 1. Accessibility (required for Ctrl+Space hotkey and paste)

1. Open **System Settings > Privacy & Security > Accessibility**
2. Click **+** and add `~/whisper-dictate/WhisperDictate.app`
3. Toggle it **ON**

### 2. Microphone (required for recording)

1. Open **System Settings > Privacy & Security > Microphone**
2. Toggle **WhisperDictate** ON (it appears after the first recording attempt)

> Launch via `WhisperDictate.app` (which the installer's launchd plist does automatically), **not** `python dictate.py` directly. Permissions are scoped to the app bundle.

## Usage

1. Press **Ctrl+Space** — recording starts (Tink sound + macOS notification)
2. Speak in natural phrases with brief pauses between them
3. **Each phrase appears at your cursor when you pause** (default streaming mode)
4. Press **Ctrl+Space** again — recording stops (Pop sound), any final phrase is transcribed and pasted

The 1-second debounce prevents key-repeat from accidentally toggling twice.

> **Tip:** if you want all the text to arrive at once at the end (legacy behavior — useful when you don't want incremental edits to scatter), set `STREAMING_MODE = False` in `dictate.py` and restart the service.

## Service Management

```bash
# Start (loads launchd agent — also auto-starts on login)
~/bin/run_whisper_dictate.sh start

# Stop (unloads launchd agent — won't auto-restart)
~/bin/run_whisper_dictate.sh stop

# Restart
~/bin/run_whisper_dictate.sh restart

# Check status (queries launchd directly)
~/bin/run_whisper_dictate.sh status

# Tail the log
~/bin/run_whisper_dictate.sh logs
```

> **About System Settings > General > Login Items & Extensions:** modern macOS shows launchd agents alongside user-added Login Items in the same list (look for the green "exec" icon). After installation, you'll see a `WhisperDictate` entry there — **that IS the launchd agent we just installed**. Leave it ON; that's what makes auto-start work.
>
> **Only remove an entry if you have TWO `WhisperDictate` rows** — one from a previous manual add via "Add Item", and one from the launchd agent. In that case, remove the manually-added one, because both running together would race on the audio stream (the second instance hits the single-instance lock and exits, but it's noisy in the log).

## Configuration

Edit `~/whisper-dictate/dictate.py`, then `~/bin/run_whisper_dictate.sh restart`:

| Setting | Default | Notes |
|---|---|---|
| `MODEL_NAME` | `medium` | `tiny`, `base`, `small`, `medium`, `large` |
| `LANGUAGE` | `en` | Set to `None` for auto-detect, or `"es"`, `"fr"`, etc. |
| `SILENCE_THRESHOLD` | `0.001` | Lower = more aggressive silence trim |
| `MIN_RECORDING_SECONDS` | `0.5` | Reject taps shorter than this (anti-hallucination) |
| `WATCHDOG_MAX_RECORDING_SECONDS` | `300` | Hard max recording duration (5 min); auto-stops if you forget to toggle |
| `LOG_MAX_BYTES` | `1048576` (1 MB) | Rotate at this size |
| `LOG_BACKUP_COUNT` | `3` | Keep this many rotated logs |
| `TOGGLE_DEBOUNCE_SECONDS` | `1.0` | Min time between toggles (prevents key-repeat double-fires) |
| `STREAMING_MODE` | `True` | Stream phrases as you pause; set False for legacy "transcribe-on-stop" |
| `SEGMENT_PAUSE_SECONDS` | `0.7` | Silence duration that closes a streaming segment |
| `SEGMENT_SPEECH_THRESHOLD` | `0.005` | Per-chunk amplitude treated as "speech" by VAD |
| `MIN_SEGMENT_SPEECH_SECONDS` | `0.3` | Min speech accumulated before VAD can close a segment |
| `MAX_SEGMENT_SECONDS` | `30` | Force-close even without pause if a segment exceeds this |

## Model Comparison

| Model | Size | Speed | Accuracy | RAM |
|---|---|---|---|---|
| `tiny` | 39 MB | Fastest | Basic | ~1 GB |
| `base` | 74 MB | Fast | Good | ~1 GB |
| `small` | 244 MB | Medium | Better | ~2 GB |
| `medium` | 769 MB | Slow | High | ~5 GB |
| `large` | 1.5 GB | Slowest | Highest | ~10 GB |

## File Locations

```
~/whisper-dictate/
├── dictate.py                      # Main script
├── install.sh                      # Installer
├── requirements.txt                # Pinned Python deps
├── WhisperDictate.app/             # macOS app bundle (rewritten by installer)
├── dictate.log                     # Current log (rotates at 1MB; gitignored)
├── dictate.stdout.log              # launchd-captured stdout
├── dictate.stderr.log              # launchd-captured stderr
├── .dictate.pid                    # Single-instance lockfile (gitignored)
└── README.md

~/whisper-official/                 # Python venv
~/bin/run_whisper_dictate.sh        # Service control
~/Library/LaunchAgents/com.whisperdictate.plist  # launchd agent
```

## How streaming works

Whisper itself is a batch model — every transcription call processes a complete audio segment from start to end. To produce text incrementally, the audio callback runs lightweight Voice Activity Detection (VAD): each incoming audio chunk is classified as speech or silence based on its amplitude. When we've accumulated enough speech and then see a long-enough pause (`SEGMENT_PAUSE_SECONDS`), the segment is closed and queued for transcription. The worker thread transcribes each segment with the same `medium` Whisper model used in legacy mode, so accuracy is preserved.

Each non-first segment of a recording gets a leading space prepended on paste, so phrases don't run together (`"Hello"` + `"world"` becomes `"Hello world"`, not `"Helloworld"`).

Tradeoffs to be aware of:
- Whisper inserts terminal punctuation per segment, so long sentences split by pauses may end up as multiple sentences
- If you switch app focus mid-recording, mid-sentence text lands in the new app
- CPU stays warm during recording (vs spike-on-stop in legacy mode)

## How the self-heal works

PortAudio's `stream.stop()` / `stream.close()` can hang in pathological states (driver stall, abrupt device disconnect). Calling `sd._terminate()` to recover deadlocks against the in-flight close (we observed this in production, holding the mic for 23 minutes).

Instead, when the close-thread doesn't return within 3 seconds, `dictate.py` calls `os._exit(75)`. The kernel reaps the process and releases the mic. launchd's `KeepAlive: { SuccessfulExit: false }` then respawns the service. Total downtime: ~5–10 seconds (mostly Whisper model reload).

## Troubleshooting

### "This process is not trusted"
Add `WhisperDictate.app` (not Terminal or raw Python) to **Accessibility**. Re-run `bash install.sh` to refresh the bundle.

### Hotkey not working
- Confirm Accessibility is granted to **WhisperDictate.app**
- Check if another app has Ctrl+Space bound (Spotlight uses Cmd+Space, but extensions sometimes hijack Ctrl+Space)
- `~/bin/run_whisper_dictate.sh status` — confirms launchd has it loaded
- **After re-running `install.sh`**: macOS TCC may invalidate the Accessibility/Microphone grant when an unsigned app's binary hash changes. In System Settings > Privacy & Security, toggle the corresponding permission OFF then ON for WhisperDictate.

### Audio level too low
- Verify Microphone permission for **WhisperDictate**
- `~/bin/run_whisper_dictate.sh logs` — the log lists every input device at startup; confirm the right one is `default`

### Microphone stuck / locked
The self-heal should catch this automatically. If it ever doesn't:

```bash
~/bin/run_whisper_dictate.sh stop
sudo killall coreaudiod   # nuclear: respawns instantly, releases stale audio handles
~/bin/run_whisper_dictate.sh start
```

### Multiple instances
The single-instance lock (`~/whisper-dictate/.dictate.pid`) prevents this, but if you suspect a stale state:

```bash
~/bin/run_whisper_dictate.sh stop
pkill -9 -f dictate.py
rm -f ~/whisper-dictate/.dictate.pid
~/bin/run_whisper_dictate.sh start
```

### Detailed logs
```bash
tail -f ~/whisper-dictate/dictate.log
```

## License

MIT

## Credits

Built with [OpenAI Whisper](https://github.com/openai/whisper).
