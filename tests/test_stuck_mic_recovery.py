#!/usr/bin/env python3
"""
Synthetic stuck-mic recovery test.

Simulates a PortAudio stream-close hang (the deadlock that bit production
on 2026-04-29) and verifies that the recovery path (os._exit(75)) fires
within the expected timeout. Runs the real WhisperDictate code in a
subprocess so that os._exit doesn't kill the test runner itself.

Run with the project's venv Python (preferred — has whisper installed):
    ~/whisper-official/bin/python3 tests/test_stuck_mic_recovery.py

Or with the system Python (only works if whisper, sounddevice, pynput,
and numpy are importable globally):
    python3 tests/test_stuck_mic_recovery.py

Exit code 0 = pass; non-zero = fail.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent

# Constants the recovery path should respect:
EXPECTED_EXIT_CODE = 75   # EX_TEMPFAIL — what _force_release_stream uses on hang
EXPECTED_MAX_SECONDS = 8  # 3s join timeout + a few seconds slack for thread scheduling


def child_mode():
    """Runs in subprocess. Builds a WhisperDictate, attaches a stream that
    hangs forever on close, and triggers stop_recording. The cleanup path
    should call os._exit(75) within ~3 seconds (the join timeout)."""
    # Redirect HOME so the test doesn't pollute the user's real log file
    # or interact with a real running instance's PID file.
    fake_home = tempfile.mkdtemp(prefix="wd_test_home_")
    os.environ["HOME"] = fake_home

    sys.path.insert(0, str(REPO_DIR))

    # Stub whisper.load_model — we don't need the 769MB model for this test
    import whisper
    class _StubModel:
        def transcribe(self, *a, **k):
            return {"text": ""}
    whisper.load_model = lambda *a, **k: _StubModel()

    # Stub query_devices — don't need real audio enumeration
    import sounddevice as sd
    fake_dev = {"name": "fake-default", "max_input_channels": 1}

    def fake_query(kind=None):
        if kind == "input":
            return fake_dev
        return [fake_dev]

    sd.query_devices = fake_query

    # The stream that hangs on stop/close — this is the simulated PortAudio bug
    class HangingStream:
        active = True

        def stop(self):
            time.sleep(120)  # Hang far longer than the 3s join timeout

        def close(self):
            time.sleep(120)

    import dictate

    wd = dictate.WhisperDictate()
    wd.stream = HangingStream()
    wd.recording = True
    wd.audio_data = []
    wd._recording_start_time = time.time()

    print("child: stop_recording with hanging stream — expect os._exit(75) in ~3s",
          flush=True)
    wd.stop_recording()

    # If we reach this sleep's end, the recovery path failed to fire.
    # 10s is well beyond the expected 3s (join timeout) + scheduling slack.
    time.sleep(10)
    print("child: FAIL — process did not self-exit after 10s", file=sys.stderr, flush=True)
    sys.exit(1)


def parent_mode():
    """Spawns child in a subprocess, asserts on exit code and elapsed time."""
    venv_py = Path.home() / "whisper-official" / "bin" / "python3"
    py = str(venv_py) if venv_py.exists() else sys.executable
    print(f"Using Python: {py}")
    print(f"Test file: {__file__}")

    env = os.environ.copy()
    env["WD_TEST_CHILD"] = "1"

    print(f"Running stuck-mic recovery test "
          f"(expect exit {EXPECTED_EXIT_CODE} within {EXPECTED_MAX_SECONDS}s)...")

    start = time.monotonic()
    try:
        result = subprocess.run(
            [py, str(Path(__file__).resolve())],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as e:
        print("FAIL: subprocess hit 30s wall-clock timeout — recovery did not fire")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
        sys.exit(1)

    elapsed = time.monotonic() - start

    print("--- child stdout ---")
    print(result.stdout.rstrip() or "(empty)")
    print("--- child stderr ---")
    print(result.stderr.rstrip() or "(empty)")
    print(f"--- result ---")
    print(f"Exit code: {result.returncode}")
    print(f"Elapsed:   {elapsed:.2f}s")

    if result.returncode != EXPECTED_EXIT_CODE:
        print(f"FAIL: expected exit code {EXPECTED_EXIT_CODE}, got {result.returncode}")
        sys.exit(1)
    if elapsed > EXPECTED_MAX_SECONDS:
        print(f"FAIL: recovery took {elapsed:.2f}s "
              f"(expected ≤{EXPECTED_MAX_SECONDS}s)")
        sys.exit(1)

    print(f"PASS: recovery fired os._exit({EXPECTED_EXIT_CODE}) in {elapsed:.2f}s")
    sys.exit(0)


if __name__ == "__main__":
    if os.environ.get("WD_TEST_CHILD") == "1":
        child_mode()
    else:
        parent_mode()
