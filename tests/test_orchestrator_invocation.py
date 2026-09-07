"""Safety and error-handling contracts for LLM CLI invocation."""

import os
import subprocess
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from orchestrator import LLMSession  # noqa: E402


class FakeProcess:
    """Completed-process stand-in with real pipe descriptors for the IO loop."""

    def __init__(self, stdout: str = "answer", stderr: str = "", returncode: int = 0):
        self.returncode = returncode
        stdin_reader, stdin_writer = os.pipe()
        self._stdin_reader = os.fdopen(stdin_reader, "rb", buffering=0)
        self.stdin = os.fdopen(stdin_writer, "wb", buffering=0)
        self.stdout = self._output_pipe(stdout)
        self.stderr = self._output_pipe(stderr)

    @staticmethod
    def _output_pipe(text):
        reader, writer = os.pipe()
        with os.fdopen(writer, "wb", buffering=0) as pipe:
            pipe.write(text.encode("utf-8"))
        return os.fdopen(reader, "rb", buffering=0)

    def wait(self, timeout=None):
        self._stdin_reader.close()
        return self.returncode

    def kill(self):
        pass  # The stand-in is already completed, like Popen.kill after wait().


def test_claude_invocation_is_restricted_and_never_bypasses_permissions(
    monkeypatch, tmp_path
):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = LLMSession(provider="claude", workdir=tmp_path)._ask_claude("question")

    assert result == "answer"
    args = captured["args"]
    assert "--dangerously-skip-permissions" not in args
    assert "--restricted" in args
    assert args[args.index("--tools") + 1] == "Read"
    assert args[args.index("--allowedTools") + 1] == "Read"
    assert args[args.index("--permission-prompts") + 1] == "none"
    assert "--strict-mcp-config" in args
    assert "--no-session-persistence" in args


def test_codex_invocation_is_read_only_and_ephemeral(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = LLMSession(provider="codex", workdir=tmp_path,
                        codex_provider_spec={"name": "openai"})._ask_codex("question")

    assert result == "answer"
    args = captured["args"]
    assert args[:2] == ["codex", "exec"]
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in args


def test_empty_workdir_uses_explicit_app_root_not_ambient_cwd(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.chdir(tmp_path)

    session = LLMSession(provider="codex", workdir="", codex_provider_spec={"name": "openai"})
    session._ask_codex("question")

    assert session.workdir == SRC.resolve()
    assert session.workdir != tmp_path
    assert captured["kwargs"]["cwd"] == str(SRC.resolve())


def test_nonzero_claude_exit_does_not_return_stdout_as_success(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(
            stdout="plausible-looking answer",
            stderr="authentication failed",
            returncode=7,
        ),
    )

    result = LLMSession(provider="claude", workdir=tmp_path)._ask_claude("question")

    assert result == "[claude failed: exit 7]"
    assert "plausible-looking answer" not in result


def test_nonzero_codex_exit_does_not_return_stdout_as_success(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(
            stdout="plausible-looking answer",
            stderr="request failed",
            returncode=9,
        ),
    )

    result = LLMSession(provider="codex", workdir=tmp_path,
                        codex_provider_spec={"name": "openai"})._ask_codex("question")

    assert result == "[codex failed: exit 9]"
    assert "plausible-looking answer" not in result


def test_stderr_only_claude_result_does_not_look_successful(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(
            stdout="",
            stderr="authentication failed",
            returncode=0,
        ),
    )

    result = LLMSession(provider="claude", workdir=tmp_path)._ask_claude("question")

    assert result == "[claude failed: no output]"
    assert "authentication failed" not in result
