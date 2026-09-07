"""Synthetic PCM and owned fake resources only: never open an audio device or model."""

import io
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import audio_capture as audio_module  # noqa: E402


@pytest.fixture
def capture(monkeypatch, tmp_path):
    def no_device():
        raise AssertionError("test attempted to load the real audio backend")

    monkeypatch.setattr(audio_module, "_load_sounddevice", no_device)
    outputs, errors = [], []
    instance = audio_module.AudioCapture(outputs.append, output_dir=str(tmp_path))
    instance._error_callback = errors.append
    instance._running = True
    return SimpleNamespace(audio=instance, outputs=outputs, errors=errors, root=tmp_path)


def fill_buffer(capture):
    for index in range(3):
        capture._audio_buffer.append((index, np.ones(32, dtype=np.float32) * 0.1))


def test_stopped_capture_never_starts_stt_or_writes_wav(capture):
    calls = []
    capture.audio._stt = SimpleNamespace(
        transcribe=lambda *args, **kwargs: calls.append(args) or "late",
        close=lambda: None,
    )
    capture.audio.stop()
    fill_buffer(capture.audio)

    capture.audio._save_and_transcribe(1)

    assert calls == []
    assert capture.outputs == []
    assert list(capture.root.glob("*.wav")) == []


@pytest.mark.parametrize("outcome", ["success", "import-error", "model-error"])
def test_stop_during_stt_suppresses_both_text_and_errors(capture, outcome):
    entered, release = threading.Event(), threading.Event()

    def transcribe(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        if outcome == "import-error":
            raise ImportError("fake missing dependency")
        if outcome == "model-error":
            raise RuntimeError("fake model error")
        return "late transcription"

    capture.audio._stt = SimpleNamespace(transcribe=transcribe, close=release.set)
    fill_buffer(capture.audio)
    job = threading.Thread(target=lambda: capture.audio._save_and_transcribe(1))
    job.start()
    try:
        assert entered.wait(1)
        capture.audio.stop()
        release.set()
        job.join(timeout=2)
        assert not job.is_alive()
        assert capture.outputs == []
        assert capture.errors == []
        assert list(capture.root.glob("*.wav")) == []
    finally:
        release.set()
        job.join(timeout=2)


def test_active_stt_error_is_not_sent_as_a_spoken_question(capture, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError("private transcript and credentials must not appear")

    capture.audio._stt = SimpleNamespace(transcribe=fail, close=lambda: None)
    fill_buffer(capture.audio)

    capture.audio._save_and_transcribe(1)

    assert capture.outputs == []
    assert capture.errors == ["[STT error: RuntimeError]"]
    assert "private transcript and credentials" not in caplog.text
    assert list(capture.root.glob("*.wav")) == []


def test_stream_close_is_attempted_even_when_stop_fails(capture):
    closed = []

    def fail_stop():
        raise RuntimeError("fake stream stop failure")

    capture.audio._stream = SimpleNamespace(stop=fail_stop, close=lambda: closed.append(True))
    capture.audio.stop()

    assert closed == [True]
    assert capture.audio._stream is None


def test_parec_kill_is_followed_by_reap_and_pipe_close(capture):
    class Process:
        def __init__(self):
            self.returncode = None
            self.waits = 0
            self.killed = False
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()

        def terminate(self):
            pass

        def wait(self, timeout=None):
            self.waits += 1
            if not self.killed:
                raise subprocess.TimeoutExpired("fake parec", timeout)
            self.returncode = -9
            return self.returncode

        def kill(self):
            self.killed = True

        def poll(self):
            return self.returncode

    process = Process()
    capture.audio._parec_proc = process
    capture.audio.stop()

    assert process.killed
    assert process.waits == 2
    assert process.returncode == -9
    assert process.stdout.closed and process.stderr.closed
    assert capture.audio._parec_proc is None


def test_stop_does_not_join_current_reader_thread(capture):
    capture.audio._parec_thread = threading.current_thread()

    capture.audio.stop()

    assert not capture.audio.is_running


def test_missing_parec_is_a_failed_start(capture, monkeypatch):
    monkeypatch.setattr(capture.audio, "_resolve_source", lambda: ("parec", "fake.monitor"))

    def missing(*args, **kwargs):
        raise FileNotFoundError("fake missing parec")

    monkeypatch.setattr(audio_module.subprocess, "Popen", missing)

    assert capture.audio.start() is False
    assert not capture.audio.is_running
    assert capture.audio._parec_proc is None


def test_capture_is_terminal_after_stop(capture, monkeypatch):
    capture.audio.stop()

    def forbidden():
        raise AssertionError("stopped capture attempted device discovery")

    monkeypatch.setattr(capture.audio, "_resolve_source", forbidden)
    assert capture.audio.start() is False


def test_each_transcription_owns_unique_wav_and_removes_it(capture):
    paths = []

    def transcribe(path, **kwargs):
        paths.append(path)
        assert Path(path).exists()
        return "transcription"

    capture.audio._stt = SimpleNamespace(transcribe=transcribe, close=lambda: None)
    for index in range(2):
        fill_buffer(capture.audio)
        capture.audio._save_and_transcribe(index)

    assert len(set(paths)) == 2
    assert all(Path(path).parent == capture.root for path in paths)
    assert capture.outputs == ["transcription", "transcription"]
    assert list(capture.root.glob("*.wav")) == []


def test_incomplete_child_cleanup_retains_only_owned_wav_until_safe_retry(capture):
    class IncompleteCleanup(RuntimeError):
        cleanup_incomplete = True

    paths = []

    def fail(path, **kwargs):
        paths.append(Path(path))
        raise IncompleteCleanup("STT cleanup incomplete")

    capture.audio._stt = SimpleNamespace(transcribe=fail, close=lambda: None)
    fill_buffer(capture.audio)
    capture.audio._save_and_transcribe(1)

    assert paths[0].exists(), "WAV may still be read by the child whose cleanup failed"
    assert capture.outputs == []
    capture.audio.stop()
    assert not paths[0].exists(), "successful close retry should release its owned WAV"


def test_stop_collects_pending_wav_registered_by_finishing_reader(capture):
    entered, release, closed = (threading.Event() for _ in range(3))

    class IncompleteCleanup(RuntimeError):
        cleanup_incomplete = True

    def fail(*args, **kwargs):
        raise IncompleteCleanup("STT cleanup incomplete")

    def error_callback(message):
        entered.set()
        assert release.wait(2)

    capture.audio._error_callback = error_callback
    capture.audio._stt = SimpleNamespace(transcribe=fail, close=closed.set)
    fill_buffer(capture.audio)
    reader = threading.Thread(target=capture.audio._save_and_transcribe, args=(1,))
    capture.audio._parec_thread = reader
    reader.start()
    stop_errors = []

    def stop():
        try:
            capture.audio.stop()
        except Exception as error:
            stop_errors.append(error)

    stopper = threading.Thread(target=stop)
    try:
        assert entered.wait(1)
        stopper.start()
        assert closed.wait(1)
        release.set()
        stopper.join(timeout=3)
        assert not stopper.is_alive()
        assert stop_errors == []
        assert list(capture.root.glob("*.wav")) == []
        assert capture.audio._pending_wavs == set()
    finally:
        release.set()
        reader.join(timeout=2)
        if stopper.ident is not None:
            stopper.join(timeout=3)


def test_failed_wav_unlink_retains_ownership_for_retry(capture, monkeypatch):
    original = Path.unlink
    attempted = []

    def transient_failure(path, **kwargs):
        attempted.append(path)
        if len(attempted) == 1:
            raise PermissionError("PRIVATE audio path should not be logged")
        return original(path, **kwargs)

    monkeypatch.setattr(Path, "unlink", transient_failure)
    capture.audio._stt = SimpleNamespace(transcribe=lambda *args, **kwargs: "answer", close=lambda: None)
    fill_buffer(capture.audio)
    capture.audio._save_and_transcribe(1)

    assert attempted[0].exists()
    assert attempted[0] in capture.audio._pending_wavs
    assert capture.errors == ["[audio cleanup incomplete]"]
    capture.audio.stop()
    assert not attempted[0].exists()
    assert capture.audio._pending_wavs == set()


def test_capture_startup_exception_reports_failure_before_terminal_stop(capture, monkeypatch):
    def fail():
        raise RuntimeError("PRIVATE device error")

    monkeypatch.setattr(capture.audio, "_start_capture", fail)
    assert capture.audio.start() is False
    assert capture.audio._stop_event.is_set()
    assert capture.errors == ["[audio startup error: RuntimeError]"]


def test_exceptional_parec_terminate_still_attempts_kill_and_reap(capture):
    process = SimpleNamespace(returncode=None, stdout=io.BytesIO(), stderr=io.BytesIO())

    def fail():
        raise RuntimeError("fake terminate failure")

    def kill():
        process.returncode = -9

    process.terminate = fail
    process.kill = kill
    process.poll = lambda: process.returncode
    process.wait = lambda timeout=None: process.returncode
    capture.audio._parec_proc = process

    capture.audio.stop()

    assert process.returncode == -9
    assert process.stdout.closed and process.stderr.closed
    assert capture.audio._parec_proc is None


def test_parec_pipe_failure_does_not_skip_other_cleanup_and_can_retry(capture):
    class Pipe(io.BytesIO):
        def close(self):
            if not self.closed and not getattr(self, "attempted", False):
                self.attempted = True
                raise OSError("fake pipe close failure")
            super().close()

    process = SimpleNamespace(
        stdout=Pipe(), stderr=io.BytesIO(), poll=lambda: 0, wait=lambda timeout=None: 0,
    )
    capture.audio._parec_proc = process

    with pytest.raises(RuntimeError, match="^audio cleanup incomplete$"):
        capture.audio.stop()

    assert process.stderr.closed
    assert capture.audio._parec_proc is process
    capture.audio.stop()
    assert process.stdout.closed
    assert capture.audio._parec_proc is None
