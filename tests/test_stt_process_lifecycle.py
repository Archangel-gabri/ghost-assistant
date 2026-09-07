"""Real spawned fake backends verify STT lifetime without models or devices."""

import functools
import importlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import threading
import time
import types

import pytest


SRC = Path(__file__).resolve().parent.parent / "src"


def fake_backend_worker(connection, backend, model_size, *, entered=None):
    """Run the production child entrypoint with a backend local to this child."""
    import stt_process

    loads = 0

    class FakeBackend:
        def __init__(self):
            self.calls = 0

        def transcribe(self, wav_path, language=None):
            self.calls += 1
            if entered is not None and backend != "ignore-term":
                entered.set()
            if backend == "hang":
                while True:
                    time.sleep(1)
            if backend == "ignore-term":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                if entered is not None:
                    entered.set()
                while True:
                    time.sleep(1)
            if backend == "error":
                raise RuntimeError("PRIVATE transcript /private/audio.wav provider-error")
            if backend == "death":
                os._exit(17)
            if backend == "huge":
                return "PRIVATE" * 100000
            if backend == "wrong-type":
                return {"PRIVATE": "transcript"}
            if backend == "noisy":
                import logging
                logging.critical("PRIVATE backend logger")
                print("PRIVATE backend stdout", flush=True)
                os.write(2, b"PRIVATE native stderr\n")
            return f"{os.getpid()}:{loads}:{self.calls}:{language}:{wav_path}"

    def create_stt(*args, **kwargs):
        nonlocal loads
        loads += 1
        if backend == "load-error":
            raise RuntimeError("PRIVATE load error /private/model")
        return FakeBackend()

    # This substitution lives only inside the disposable spawned process.
    sys.modules["stt_fast"] = types.SimpleNamespace(create_stt=create_stt)
    stt_process._worker_main(connection, backend, model_size)


def malformed_worker(connection, backend, model_size, *, entered=None):
    request = json.loads(connection.recv_bytes())
    if backend == "wrong-id":
        response = {"id": request["id"] + 1, "status": "success", "text": "PRIVATE"}
        connection.send_bytes(json.dumps(response).encode())
    elif backend == "wrong-id-type":
        response = {"id": str(request["id"]), "status": "success", "text": "PRIVATE"}
        connection.send_bytes(json.dumps(response).encode())
    elif backend == "stale-id":
        response = {"id": request["id"], "status": "success", "text": "first"}
        connection.send_bytes(json.dumps(response).encode())
        connection.recv_bytes()
        connection.send_bytes(json.dumps(response).encode())
    elif backend == "huge-frame":
        connection.send_bytes(b"PRIVATE" * 100000)
    elif backend == "raw-error":
        connection.send_bytes(json.dumps({"id": request["id"], "status": "error",
                                         "text": "PRIVATE transcript /private/file"}).encode())
    elif backend == "bad-json":
        connection.send_bytes(b"PRIVATE invalid JSON")
    elif backend in ("partial-frame", "partial-header"):
        data = struct.pack("!i", 100) + b"{" if backend == "partial-frame" else b"\x00\x00"
        os.write(connection.fileno(), data)
        if entered is not None:
            entered.set()
        while True:
            time.sleep(1)
    connection.close()


class StartContext:
    """Keep real spawn semantics while controlling the start handoff in tests."""

    def __init__(self, entered=None, release=None, fail=False):
        self.context = multiprocessing.get_context("spawn")
        self.entered = entered
        self.release = release
        self.fail = fail
        self.created = 0
        self.started_pid = None
        self.ignore_cleanup = False

    def Pipe(self, **kwargs):
        return self.context.Pipe(**kwargs)

    def Process(self, **kwargs):
        self.created += 1
        process = self.context.Process(**kwargs)
        return StartProcessProxy(process, self)


class StartProcessProxy:
    """Do not put an unpicklable test closure on the real spawn Process."""

    def __init__(self, process, context):
        self.process = process
        self.context = context

    def __getattr__(self, name):
        return getattr(self.process, name)

    def start(self):
        if self.context.entered is not None:
            self.context.entered.set()
        if self.context.release is not None:
            assert self.context.release.wait(5)
        if self.context.fail:
            raise RuntimeError("PRIVATE spawn exception /private/path")
        self.process.start()
        self.context.started_pid = self.process.pid

    def terminate(self):
        if not self.context.ignore_cleanup:
            self.process.terminate()

    def kill(self):
        if not self.context.ignore_cleanup:
            self.process.kill()


@pytest.fixture
def api(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    return importlib.import_module("stt_process")


@pytest.fixture
def sessions(api):
    clients = []

    def make(backend="normal", **kwargs):
        kwargs.setdefault("_target", fake_backend_worker)
        client = api.STTProcess(backend=backend, **kwargs)
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


def start_call(client, path="audio.wav"):
    outcomes = []

    def call():
        try:
            outcomes.append(client.transcribe(path, language="ru"))
        except Exception as error:
            outcomes.append(error)

    thread = threading.Thread(target=call)
    thread.start()
    return thread, outcomes


def assert_reaped(pid):
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_import_does_not_load_backend_or_start_network_or_process(api):
    script = """
import sys
import socket
import subprocess
class BlockModels:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'stt_fast', 'torch', 'transformers', 'faster_whisper'}:
            raise AssertionError('eager model import')
sys.meta_path.insert(0, BlockModels())
def forbidden(*args, **kwargs):
    raise AssertionError('eager network or subprocess access')
socket.create_connection = forbidden
subprocess.Popen = forbidden
import stt_process
client = stt_process.STTProcess()
assert client._process is None
client.close()
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run([sys.executable, "-B", "-c", script], env=env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_process_is_lazy_persistent_and_backend_is_cached(sessions):
    client = sessions()
    assert client._process is None
    first = client.transcribe("first.wav", language="ru").split(":")
    second = client.transcribe("second.wav", language="en").split(":")
    assert first[0] == second[0]
    assert first[1:3] == ["1", "1"]
    assert second[1:3] == ["1", "2"]
    assert first[3:] == ["ru", "first.wav"]
    assert second[3:] == ["en", "second.wav"]
    pid = int(first[0])
    client.close()
    assert_reaped(pid)


@pytest.mark.parametrize("backend", ["hang", "ignore-term"])
def test_close_terminates_blocked_backend_and_is_terminal(api, sessions, backend):
    entered = multiprocessing.get_context("spawn").Event()
    client = sessions(backend, _target=functools.partial(fake_backend_worker, entered=entered))
    thread, outcomes = start_call(client)
    try:
        assert entered.wait(5)
        pid = client._process.pid
        started = time.monotonic()
        client.close()
        assert time.monotonic() - started < 2
        thread.join(2)
        assert not thread.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], api.STTCancelled)
        assert_reaped(pid)
        client.close()
        with pytest.raises(api.STTCancelled):
            client.transcribe("next.wav")
        assert client._process is None
    finally:
        client.close()
        thread.join(2)


def test_shared_stop_event_cancels_without_explicit_close(api, sessions):
    entered = multiprocessing.get_context("spawn").Event()
    stop_event = threading.Event()
    client = sessions("hang", stop_event=stop_event,
                      _target=functools.partial(fake_backend_worker, entered=entered))
    thread, outcomes = start_call(client)
    try:
        assert entered.wait(5)
        pid = client._process.pid
        stop_event.set()
        thread.join(2)
        assert not thread.is_alive()
        assert isinstance(outcomes[0], api.STTCancelled)
        assert_reaped(pid)
    finally:
        client.close()
        thread.join(2)


@pytest.mark.parametrize("cancel_method", ["event", "close"])
@pytest.mark.parametrize("backend", ["partial-frame", "partial-header"])
def test_partial_frame_does_not_block_cancellation(api, sessions, cancel_method, backend):
    entered = multiprocessing.get_context("spawn").Event()
    stop_event = threading.Event()
    client = sessions(backend, stop_event=stop_event,
                      _target=functools.partial(malformed_worker, entered=entered))
    thread, outcomes = start_call(client)
    try:
        assert entered.wait(5)
        pid = client._process.pid
        if cancel_method == "event":
            stop_event.set()
        else:
            client.close()
        thread.join(2)
        assert not thread.is_alive()
        assert isinstance(outcomes[0], api.STTCancelled)
        assert_reaped(pid)
    finally:
        client.close()
        thread.join(2)


@pytest.mark.parametrize("backend", ["error", "load-error", "death", "huge", "wrong-type"])
def test_backend_failures_have_safe_bounded_outcomes(api, sessions, backend):
    client = sessions(backend)
    with pytest.raises(api.STTError) as error:
        client.transcribe("/private/audio.wav")
    assert "PRIVATE" not in str(error.value)
    assert "/private" not in str(error.value)
    assert len(str(error.value)) < 100
    assert error.value.cleanup_incomplete is False
    assert client._process is None


@pytest.mark.parametrize("backend", ["wrong-id", "wrong-id-type", "huge-frame", "raw-error", "bad-json"])
def test_invalid_child_protocol_is_rejected_safely(api, sessions, backend):
    client = sessions(backend, _target=malformed_worker)
    with pytest.raises(api.STTError) as error:
        client.transcribe("/private/audio.wav")
    assert "PRIVATE" not in str(error.value)
    assert "/private" not in str(error.value)
    assert len(str(error.value)) < 100
    assert client._process is None


def test_stale_result_cannot_complete_next_request(api, sessions):
    client = sessions("stale-id", _target=malformed_worker)
    assert client.transcribe("first.wav") == "first"
    with pytest.raises(api.STTError):
        client.transcribe("second.wav")


def test_pre_cancelled_session_never_spawns(api, sessions):
    stop_event = threading.Event()
    stop_event.set()
    context = StartContext()
    client = sessions(stop_event=stop_event, _context=context)
    with pytest.raises(api.STTCancelled):
        client.transcribe("audio.wav")
    assert context.created == 0


def test_spawn_failure_is_sanitized_and_closable(api, sessions):
    client = sessions(_context=StartContext(fail=True))
    with pytest.raises(api.STTError) as error:
        client.transcribe("audio.wav")
    assert "PRIVATE" not in str(error.value)
    assert "/private" not in str(error.value)
    client.close()
    assert client._process is None


def test_backend_output_does_not_escape_child_stdio(sessions, capfd):
    client = sessions("noisy")
    client.transcribe("audio.wav")
    captured = capfd.readouterr()
    assert "PRIVATE" not in captured.out + captured.err


def test_oversized_request_is_rejected_before_spawn(api, sessions):
    context = StartContext()
    client = sessions(_context=context)
    with pytest.raises(api.STTError, match="STT request exceeded size limit"):
        client.transcribe("x" * (api.MAX_REQUEST_BYTES + 1))
    assert context.created == 0


def test_request_timeout_reaps_child(api, sessions):
    client = sessions("hang", _timeout=0.4)
    started = time.monotonic()
    with pytest.raises(api.STTError, match="STT request timed out"):
        client.transcribe("audio.wav")
    assert time.monotonic() - started < 2
    assert client._process is None


def test_failed_termination_preserves_ownership_and_marks_input_in_use(api, sessions):
    entered = multiprocessing.get_context("spawn").Event()
    context = StartContext()
    client = sessions("hang", _context=context,
                      _target=functools.partial(fake_backend_worker, entered=entered))
    thread, _ = start_call(client)
    try:
        assert entered.wait(5)
        pid = client._process.pid
        context.ignore_cleanup = True
        with pytest.raises(api.STTError, match="STT cleanup incomplete") as error:
            client.close()
        assert error.value.cleanup_incomplete is True
        assert client._process is not None
        os.kill(pid, 0)
    finally:
        context.ignore_cleanup = False
        client.close()
        thread.join(3)
    assert not thread.is_alive()
    assert_reaped(pid)


def test_concurrent_requests_are_serialized_on_one_child(sessions):
    client = sessions()
    threads_and_outcomes = [start_call(client, f"job-{i}.wav") for i in range(4)]
    for thread, _ in threads_and_outcomes:
        thread.join(5)
        assert not thread.is_alive()
    answers = [outcomes[0].split(":") for _, outcomes in threads_and_outcomes]
    assert len({answer[0] for answer in answers}) == 1
    assert sorted(answer[2] for answer in answers) == ["1", "2", "3", "4"]


def test_close_during_spawn_is_bounded_and_late_child_never_gets_job(api, sessions):
    entered, release = threading.Event(), threading.Event()
    child_entered = multiprocessing.get_context("spawn").Event()
    context = StartContext(entered, release)
    client = sessions(_context=context,
                      _target=functools.partial(fake_backend_worker, entered=child_entered))
    thread, outcomes = start_call(client)
    try:
        assert entered.wait(2)
        started = time.monotonic()
        with pytest.raises(api.STTError, match="STT cleanup incomplete") as error:
            client.close()
        assert error.value.cleanup_incomplete is True
        assert time.monotonic() - started < 2
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        assert isinstance(outcomes[0], api.STTCancelled)
        assert not child_entered.is_set()
        assert context.started_pid is not None
        assert_reaped(context.started_pid)
        assert client._process is None
    finally:
        release.set()
        thread.join(3)
        client.close()
