"""Nonstream transport against real, bounded local children; no CLI or network."""

import os
import signal
import subprocess
import sys
import threading
import time
import tracemalloc
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
from orchestrator import LLMSession  # noqa: E402


MIB = 1024 * 1024
FAKE = r'''
import os, subprocess, sys, time
from pathlib import Path
scenario, provider = sys.argv[1:3]
if scenario == "reject_stdin":
    os.close(0)
    os.write(1, b"answer without the prompt")
    time.sleep(0.05)
    sys.exit(0)
if scenario == "stdin_backpressure":
    time.sleep(2)
    sys.exit(0)
if scenario == "duplex":
    # Fill both output pipes before accepting a prompt larger than pipe capacity.
    for _ in range(128):
        os.write(1, b"x" * 4096)
        os.write(2, b"private diagnostic" * 256)
if provider != "codex":
    prompt = sys.stdin.buffer.read()
if scenario == "stdout_limit":
    os.write(1, b"x" * (2 * 1024 * 1024))
elif scenario == "stdout_overflow":
    os.write(1, b"x" * (2 * 1024 * 1024 + 1))
elif scenario == "stderr_pressure":
    for _ in range(128):
        os.write(2, b"x" * 65536)
    os.write(1, b"answer")
elif scenario == "utf8":
    payload = "Привет 🌍".encode()
    for byte in payload:
        os.write(1, bytes([byte]))
        time.sleep(0.001)
elif scenario == "invalid_utf8":
    os.write(1, b"plausible\xf0\x9f")
elif scenario == "invalid_stderr":
    os.write(2, b"\xff\xf0\x9f")
    os.write(1, b"answer")
elif scenario == "partial_timeout":
    os.write(1, b"partial")
    time.sleep(2)
elif scenario == "closed_pipes_timeout":
    os.close(1)
    os.close(2)
    time.sleep(2)
elif scenario == "descendant":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    Path(sys.argv[3]).write_text(str(child.pid))
    os.write(1, b"partial")
elif scenario == "echo":
    os.write(1, str(len(prompt)).encode())
elif scenario not in ("duplex",):
    os.write(1, b"answer")
'''


@pytest.fixture
def transport(monkeypatch):
    processes = []
    clients = []
    actual_popen = subprocess.Popen
    configuration = {}

    def launch(args, **kwargs):
        provider = configuration["provider"]
        process = actual_popen(
            [sys.executable, "-u", "-c", FAKE, configuration["scenario"], provider,
             *configuration.get("extra", ())], **kwargs
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", launch)

    def create(provider, scenario, timeout=0.8, extra=()):
        configuration.update(provider=provider, scenario=scenario, extra=extra)
        client = LLMSession(provider=provider, command="fake-local-tool", response_timeout=timeout,
                            codex_provider_spec={"name": "openai"})
        clients.append(client)
        return client

    yield create, processes
    for client in clients:
        client.close()
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()


def assert_reaped(processes):
    for process in processes:
        assert process.returncode is not None
        with pytest.raises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)


@pytest.mark.parametrize("provider", ["claude", "codex", "generic"])
@pytest.mark.parametrize("scenario,expected", [
    ("stdout_limit", "x" * (2 * MIB)),
    ("stdout_overflow", None),
    ("utf8", "Привет 🌍"),
    ("invalid_utf8", None),
    ("invalid_stderr", "answer"),
], ids=["stdout-boundary", "stdout-overflow", "split-utf8", "invalid-utf8", "binary-stderr"])
def test_nonstream_output_contract(transport, provider, scenario, expected):
    create, processes = transport
    result = create(provider, scenario).ask("q")
    assert isinstance(result, str)
    if expected is None:
        assert result.status == "error"
        assert len(result) < 100
    else:
        assert result.status == "success"
        assert result == expected
    assert_reaped(processes)


@pytest.mark.parametrize("provider", ["claude", "codex", "generic"])
def test_stderr_is_drained_without_accumulating_the_entire_stream(transport, provider):
    create, processes = transport
    tracemalloc.start()
    try:
        result = create(provider, "stderr_pressure").ask("q")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result == "answer"
    assert result.status == "success"
    assert peak < 2 * MIB, f"stderr was retained: Python peak={peak}"
    assert_reaped(processes)


@pytest.mark.parametrize("provider", ["claude", "codex", "generic"])
@pytest.mark.parametrize("question", ["q" * (2 * MIB), "я" * (MIB + 1)], ids=["ascii-over", "utf8-over"])
def test_oversized_prompt_is_rejected_before_spawn(transport, provider, question):
    create, processes = transport
    result = create(provider, "answer").ask(question)
    assert result.status == "error"
    assert not processes


@pytest.mark.parametrize("provider", ["claude", "generic"])
@pytest.mark.parametrize("prompt", ["q" * (2 * MIB), "я" * MIB], ids=["ascii-limit", "utf8-limit"])
def test_exact_prompt_byte_limit_is_fully_delivered(transport, provider, prompt):
    create, processes = transport
    client = create(provider, "echo")
    # Raw provider entry point avoids adding the user-facing prompt envelope.
    ask = client._ask_claude if provider == "claude" else client._ask_generic
    result = ask(prompt)
    assert result == str(2 * MIB)
    assert result.status == "success"
    assert_reaped(processes)


@pytest.mark.parametrize("provider", ["claude", "generic"])
def test_stdin_backpressure_and_duplex_output_remain_bounded(transport, provider):
    create, processes = transport
    result = create(provider, "duplex").ask("q" * MIB)
    assert result.status == "success"
    assert result == "x" * (128 * 4096)
    result = create(provider, "stdin_backpressure", timeout=0.15).ask("q" * MIB)
    assert result.status == "timeout"
    assert_reaped(processes)


@pytest.mark.parametrize("provider", ["claude", "generic"])
def test_early_stdin_close_cannot_accept_an_unsent_prompt(transport, provider):
    create, processes = transport
    result = create(provider, "reject_stdin").ask("q" * MIB)
    assert result.status == "error"
    assert "answer without" not in result
    assert_reaped(processes)


@pytest.mark.parametrize("provider", ["claude", "codex", "generic"])
@pytest.mark.parametrize("scenario", ["partial_timeout", "closed_pipes_timeout"])
def test_nonstream_deadline_includes_wait_after_eof(transport, provider, scenario):
    create, processes = transport
    start = time.monotonic()
    result = create(provider, scenario, timeout=0.15).ask("q")
    assert result.status == "timeout"
    assert result == "[timeout]"
    assert time.monotonic() - start < 1
    assert_reaped(processes)


@pytest.mark.parametrize("provider", ["claude", "codex", "generic"])
def test_nonstream_close_cancels_a_running_request(transport, provider):
    create, processes = transport
    client = create(provider, "partial_timeout", timeout=2)
    results = []
    thread = threading.Thread(target=lambda: results.append(client.ask("q")))
    thread.start()
    try:
        deadline = time.monotonic() + 1
        while not processes:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        client.close()
        thread.join(0.8)
        assert not thread.is_alive()
        assert results and results[0].status == "cancelled"
        assert_reaped(processes)
    finally:
        client.close()
        thread.join(3)


@pytest.mark.parametrize("provider", ["claude", "codex", "generic"])
def test_nonstream_timeout_stops_descendant_holding_pipes(transport, provider, tmp_path):
    create, processes = transport
    pid_file = tmp_path / "owned-child.pid"
    client = create(provider, "descendant", timeout=0.2, extra=(str(pid_file),))
    start = time.monotonic()
    result = client.ask("q")
    assert result.status == "timeout"
    assert time.monotonic() - start < 1
    assert_reaped(processes)
    pid = int(pid_file.read_text())
    stat = Path(f"/proc/{pid}/stat")
    deadline = time.monotonic() + 0.5
    while stat.exists():
        try:
            state = stat.read_text().split(")", 1)[1].split()[0]
        except FileNotFoundError:
            break
        if state == "Z":
            break
        assert time.monotonic() < deadline, "owned descendant remains running"
        time.sleep(0.01)
