"""Real child processes exercise pipes, cancellation and terminal outcomes."""

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
from orchestrator import LLMSession  # noqa: E402


CLI = Path(__file__).with_name("fake_llm_cli.py")


@pytest.fixture
def spawned(monkeypatch):
    processes = []
    popen = subprocess.Popen

    def record(*args, **kwargs):
        proc = popen(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", record)
    yield processes
    # External watchdog fixtures are bounded (<=3 s), and cleanup is restricted
    # to children actually launched by this test.
    for proc in processes:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=4)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()


def session(monkeypatch, scenario, timeout=0.4, extra=()):
    client = LLMSession(response_timeout=timeout)
    monkeypatch.setattr(client, "_claude_base_args", lambda: [sys.executable, "-u", str(CLI), scenario, *extra])
    return client


def assert_reaped(processes):
    for proc in processes:
        assert proc.returncode is not None
        with pytest.raises(ChildProcessError):
            os.waitpid(proc.pid, os.WNOHANG)


@pytest.mark.parametrize("scenario,expected", [("batched", "Привет мир"), ("final_no_newline", "answer"), ("large_stderr", "answer")])
def test_real_stream_success(monkeypatch, spawned, scenario, expected):
    chunks = []
    answer = session(monkeypatch, scenario).ask_stream("question", chunks.append)
    assert answer == expected
    assert "".join(chunks) == expected
    assert getattr(answer, "status", None) == "success"
    assert_reaped(spawned)


@pytest.mark.parametrize("scenario", ["incomplete", "timeout", "stdin_backpressure"])
def test_deadline_covers_reads_and_stdin(monkeypatch, spawned, scenario):
    client = session(monkeypatch, scenario, timeout=0.15)
    start = time.monotonic()
    answer = client.ask_stream("q" * (1024 * 1024), lambda chunk: None)
    elapsed = time.monotonic() - start
    assert answer == "[timeout]"
    assert getattr(answer, "status", None) == "timeout"
    assert elapsed < 1.0
    assert_reaped(spawned)


@pytest.mark.parametrize("scenario,expected", [
    ("exit_error", "[claude failed: exit 7]"),
    ("result_error", "[claude failed: protocol]"),
    ("delta_only", "[claude failed: protocol]"),
    ("malformed_tail", "[claude failed: protocol]"),
    ("oversized_line", "[claude failed: protocol]"),
    ("empty", "[claude failed: no output]"),
    ("stderr_only", "[claude failed: no output]"),
])
def test_incomplete_or_failed_stream_is_not_success(monkeypatch, spawned, scenario, expected):
    answer = session(monkeypatch, scenario).ask_stream("question", lambda chunk: None)
    assert answer == expected
    assert getattr(answer, "status", None) == "error"
    assert_reaped(spawned)


def test_close_cancels_and_prevents_further_spawn(monkeypatch, spawned):
    client = session(monkeypatch, "timeout", timeout=3)
    seen = threading.Event()
    answers = []
    thread = threading.Thread(target=lambda: answers.append(client.ask_stream("q", lambda c: seen.set())))
    thread.start()
    try:
        assert seen.wait(1)
        client.close()
        client.close()
        thread.join(0.8)
        assert not thread.is_alive()
        assert answers == ["[cancelled]"]
        count = len(spawned)
        assert client.ask("next") == "[cancelled]"
        assert client.ask_stream("next", lambda c: None) == "[cancelled]"
        assert len(spawned) == count
        assert_reaped(spawned)
    finally:
        thread.join(3)


def test_timeout_does_not_close_the_session(monkeypatch, spawned):
    client = session(monkeypatch, "timeout", timeout=0.15)
    assert client.ask_stream("q", lambda c: None) == "[timeout]"
    monkeypatch.setattr(client, "_claude_base_args", lambda: [sys.executable, "-u", str(CLI), "final_no_newline"])
    assert client.ask_stream("q", lambda c: None) == "answer"
    assert_reaped(spawned)


def test_closed_stdin_cannot_succeed_with_unsent_prompt(monkeypatch, spawned):
    client = session(monkeypatch, "reject_stdin")
    answer = client.ask_stream("q" * (1024 * 1024), lambda c: None)
    assert answer == "[claude failed: protocol]"
    assert answer.status == "error"
    assert_reaped(spawned)


def test_terminal_text_is_authoritative_without_duplicate_chunks(monkeypatch, spawned):
    chunks = []
    answer = session(monkeypatch, "corrected_final").ask_stream("q", chunks.append)
    assert answer == "correct final"
    assert answer.status == "success"
    assert chunks == ["incorrect partial"]
    assert_reaped(spawned)


def test_callback_exception_reaps_process(monkeypatch, spawned):
    def broken(_):
        raise RuntimeError("private callback diagnostic")

    answer = session(monkeypatch, "timeout").ask_stream("q", broken)
    assert answer == "[error: RuntimeError]"
    assert getattr(answer, "status", None) == "error"
    assert_reaped(spawned)


def test_no_running_process_is_accepted_as_complete():
    answer = LLMSession._completed_result("claude", SimpleNamespace(returncode=None), "partial", "")
    assert answer == "[claude failed: not completed]"


def test_mock_result_remains_a_string_with_explicit_status():
    client = LLMSession(provider="missing")
    answer = client.ask("q")
    assert isinstance(answer, str)
    assert answer.status == "mock"


def test_successful_literal_error_marker_is_not_misclassified():
    answer = LLMSession._completed_result("claude", SimpleNamespace(returncode=0), "[timeout]", "")
    assert answer == "[timeout]"
    assert answer.status == "success"


@pytest.mark.skipif(os.name != "posix", reason="process groups are a POSIX contract")
def test_timeout_stops_descendant_holding_pipes(monkeypatch, spawned, tmp_path):
    pid_file = tmp_path / "child.pid"
    client = session(monkeypatch, "descendant", timeout=0.2, extra=(str(pid_file),))
    try:
        start = time.monotonic()
        assert client.ask_stream("q", lambda c: None) == "[timeout]"
        assert time.monotonic() - start < 1.0
        assert_reaped(spawned)
        pid = int(pid_file.read_text())
        stat = Path(f"/proc/{pid}/stat")
        deadline = time.monotonic() + 0.5
        while stat.exists() and stat.read_text().split(")", 1)[1].split()[0] != "Z":
            assert time.monotonic() < deadline, "owned descendant remains running"
            time.sleep(0.01)
    finally:
        # Only the new session group created by our recorded fake CLI.
        for proc in spawned:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_close_cancels_all_concurrent_requests(monkeypatch, spawned):
    client = session(monkeypatch, "timeout", timeout=3)
    entered = [threading.Event(), threading.Event()]
    answers = []
    threads = [threading.Thread(target=lambda index=i: answers.append(
        client.ask_stream("q", lambda c: entered[index].set()))) for i in range(2)]
    for thread in threads:
        thread.start()
    try:
        assert all(event.wait(1) for event in entered)
        client.close()
        for thread in threads:
            thread.join(0.8)
            assert not thread.is_alive()
        assert answers == ["[cancelled]", "[cancelled]"]
        assert_reaped(spawned)
    finally:
        for thread in threads:
            thread.join(3)


def test_close_cancels_nonstream_communicate(spawned):
    client = LLMSession(provider="generic", command=shlex.join(
        [sys.executable, "-u", str(CLI), "plain_timeout"]), response_timeout=3)
    answers = []
    thread = threading.Thread(target=lambda: answers.append(client.ask("q")))
    thread.start()
    try:
        deadline = time.monotonic() + 1
        while not spawned:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        client.close()
        thread.join(0.8)
        assert not thread.is_alive()
        assert answers == ["[cancelled]"]
        assert_reaped(spawned)
    finally:
        thread.join(3)


@pytest.mark.parametrize("scenario,expected", [("plain", "answer"), ("plain_error", "[generic failed: exit 7]"), ("plain_timeout", "[timeout]"), ("empty", "[generic failed: no output]")])
def test_generic_uses_the_same_terminal_contract(spawned, scenario, expected):
    command = shlex.join([sys.executable, "-u", str(CLI), scenario])
    client = LLMSession(provider="generic", command=command, response_timeout=0.15)
    answer = client.ask("q")
    assert answer == expected
    assert getattr(answer, "status", None) == ("success" if scenario == "plain" else "timeout" if scenario == "plain_timeout" else "error")
    assert_reaped(spawned)


@pytest.mark.parametrize("command", ["'", "   "])
def test_bad_generic_template_is_a_terminal_error(command, spawned):
    answer = LLMSession(provider="generic", command=command).ask("q")
    assert answer == "[generic failed: command]"
    assert answer.status == "error"
    assert not spawned
