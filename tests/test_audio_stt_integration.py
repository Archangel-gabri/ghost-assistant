"""AudioCapture to real spawn STT, with synthetic WAVs and child-local fakes."""

import functools
import multiprocessing
import os
from pathlib import Path
import signal
import sys
import threading
import time
import types
import wave

import numpy as np
import pytest


SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import audio_capture as audio_module  # noqa: E402
import stt_process as stt_module  # noqa: E402


def synthetic_child(connection, backend, model_size, *, entered, mode):
    class Backend:
        def transcribe(self, wav_path, language=None):
            with wave.open(wav_path, "rb") as wav:
                assert wav.getnchannels() == 1
                assert wav.getframerate() == 16000
                assert wav.getnframes() == 96
            if mode == "ignore-term":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            entered.set()
            if mode != "success":
                while True:
                    time.sleep(1)
            return f"synthetic answer {os.getpid()}"

    sys.modules["stt_fast"] = types.SimpleNamespace(create_stt=lambda *args, **kwargs: Backend())
    stt_module._worker_main(connection, backend, model_size)


def fill_audio(capture):
    for index in range(3):
        capture._audio_buffer.append((index, np.ones(32, dtype=np.float32) * 0.1))


@pytest.fixture
def integration(monkeypatch, tmp_path):
    clients, captures = [], []
    mode = types.SimpleNamespace(value="success")
    entered = multiprocessing.get_context("spawn").Event()
    original = stt_module.STTProcess

    def factory(**kwargs):
        client = original(
            **kwargs,
            _target=functools.partial(synthetic_child, entered=entered, mode=mode.value),
        )
        clients.append(client)
        return client

    def no_device():
        raise AssertionError("integration must not open an audio device")

    monkeypatch.setattr(stt_module, "STTProcess", factory)
    monkeypatch.setattr(audio_module, "_load_sounddevice", no_device)

    def make_capture():
        outputs, errors = [], []
        capture = audio_module.AudioCapture(
            outputs.append, on_error=errors.append, output_dir=str(tmp_path),
        )
        capture._running = True
        captures.append(capture)
        return capture, outputs, errors

    yield types.SimpleNamespace(
        make_capture=make_capture, mode=mode, entered=entered,
        clients=clients, root=tmp_path,
    )
    for capture in captures:
        capture.stop()
    for client in clients:
        client.close()


@pytest.mark.parametrize("mode", ["hang", "ignore-term"])
def test_stop_reaps_real_stt_child_before_releasing_owned_wav(integration, mode):
    integration.mode.value = mode
    capture, outputs, errors = integration.make_capture()
    fill_audio(capture)
    job = threading.Thread(target=capture._save_and_transcribe, args=(1,))
    # Models the actual parec callback owner without opening the audio source.
    capture._parec_thread = job
    job.start()
    try:
        assert integration.entered.wait(5), "fake child did not read its synthetic WAV"
        pid = integration.clients[0]._process.pid
        wavs = list(integration.root.glob("ghost-question-*.wav"))
        assert len(wavs) == 1 and wavs[0].exists()
        before = time.monotonic()
        capture.stop()
        assert time.monotonic() - before < 2
        job.join(timeout=2)
        assert not job.is_alive()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert not wavs[0].exists()
        assert outputs == [] and errors == []
        assert integration.clients[0]._process is None
    finally:
        capture.stop()
        job.join(timeout=3)


def test_new_capture_sessions_do_not_reuse_stopped_child_or_leave_wavs(integration):
    seen = set()
    for _ in range(3):
        capture, outputs, errors = integration.make_capture()
        for index in range(2):
            fill_audio(capture)
            capture._save_and_transcribe(index)
        pid = integration.clients[-1]._process.pid
        assert outputs == [f"synthetic answer {pid}"] * 2
        assert errors == []
        assert pid not in seen
        seen.add(pid)
        capture.stop()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert list(integration.root.glob("*.wav")) == []
