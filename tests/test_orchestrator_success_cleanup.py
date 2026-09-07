"""Successful local requests must not leave descendants in their owned group."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
from orchestrator import LLMSession  # noqa: E402


PARENT = r'''
import json, os, subprocess, sys
from pathlib import Path

mode, pid_file = sys.argv[1:3]
sys.stdin.buffer.read()
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(3)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
Path(pid_file).write_text(json.dumps({"pid": child.pid, "pgid": os.getpgid(child.pid)}))
if mode == "stream":
    print(json.dumps({"type": "result", "result": "final answer", "is_error": False}), flush=True)
else:
    print("final answer", flush=True)
'''


def descendant_stopped(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return True
    return stat.rsplit(")", 1)[1].split()[0] == "Z"


@pytest.mark.skipif(sys.platform != "linux", reason="checks owned POSIX groups through /proc")
@pytest.mark.parametrize("mode", ["simple", "stream"])
def test_success_reaps_parent_and_stops_same_group_descendant(monkeypatch, tmp_path, mode):
    """The descendant closes all inherited pipes, so successful EOF cannot hide it."""
    pid_file = tmp_path / "owned-descendant.json"
    processes = []
    actual_popen = subprocess.Popen

    def launch(_args, **kwargs):
        process = actual_popen(
            [sys.executable, "-u", "-c", PARENT, mode, str(pid_file)], **kwargs
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", launch)
    client = LLMSession(provider="claude" if mode == "stream" else "generic",
                        response_timeout=1)
    try:
        if mode == "stream":
            result = client._ask_claude("question", on_chunk=lambda _text: None)
        else:
            result = client._run_simple("generic", ["fake-local-cli"], "question")

        assert result.status == "success"
        assert result == "final answer"
        assert len(processes) == 1
        parent = processes[0]
        descendant = json.loads(pid_file.read_text())
        assert descendant["pgid"] == parent.pid, "fixture child escaped the owned group"
        assert parent.returncode == 0
        with pytest.raises(ChildProcessError):
            os.waitpid(parent.pid, os.WNOHANG)

        deadline = time.monotonic() + 0.5
        while not descendant_stopped(descendant["pid"]) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert descendant_stopped(descendant["pid"]), (
            "successful parent was reaped, but its same-group descendant is still running"
        )
    finally:
        client.close()
        # Never leave even a RED fixture running. The child has its own 3s bound,
        # and each target group was created by this test's start_new_session Popen.
        for process in processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=1)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()


def test_failed_close_retains_owner_and_repeat_close_retries_without_new_spawn(monkeypatch):
    client = LLMSession()
    process = object()
    client._active_processes.add(process)
    outcomes = iter([False, True])
    calls = []

    def cleanup(target):
        calls.append(target)
        return next(outcomes)

    monkeypatch.setattr(client, "_kill_and_reap", cleanup)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("closed session spawned"))
    with pytest.raises(RuntimeError, match="cleanup"):
        client.close()
    assert process in client._active_processes
    assert client.ask("question").status == "cancelled"
    client.close()
    assert process not in client._active_processes
    assert calls == [process, process]
    client.close()
    assert calls == [process, process]


def test_failed_request_cleanup_keeps_process_owned_until_close_succeeds(monkeypatch):
    from types import SimpleNamespace

    class Process(SimpleNamespace):
        __hash__ = object.__hash__

    process = Process(returncode=None)
    client = LLMSession()
    client._active_processes.add(process)
    monkeypatch.setattr(client, "_kill_and_reap", lambda proc: False)
    assert client._release_process(process) is False
    assert process in client._active_processes
    monkeypatch.setattr(client, "_kill_and_reap", lambda proc: True)
    client.close()
    assert process not in client._active_processes
