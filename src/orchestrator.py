"""
orchestrator.py — LLM backend manager (Claude CLI or Codex CLI).

Supports:
  - Claude Code CLI (--print mode): models fable, opus, sonnet, haiku
  - Codex CLI (exec mode): models gpt-5.6-sol, o3, gpt-5.5, etc.
  - Mock fallback if no CLI is available
"""

import logging
import json
import os
import selectors
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Callable
from cli_contract import CLIContractError, codex_isolation_args, read_screenshot
from utils import strip_ansi

logger = logging.getLogger("orchestrator")
APP_ROOT = Path(__file__).resolve().parent
MAX_PROMPT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
STDERR_TAIL_BYTES = 8192


class LLMResult(str):
    """String-compatible answer with a per-request terminal status, not shared state."""

    def __new__(cls, text: str, status: str = "success"):
        result = super().__new__(cls, text)
        result.status = status
        return result


class _RequestStopped(Exception):
    pass


class _ProtocolError(Exception):
    pass


class LLMSession:
    """Unified interface for Claude CLI and Codex CLI."""

    def __init__(self, provider: str = "claude", model: str = "sonnet",
                 workdir: Optional[str] = None,
                 startup_timeout: float = 15.0, response_timeout: float = 60.0,
                 command: Optional[str] = None, codex_provider_spec: Optional[dict] = None):
        self.provider = provider       # "claude", "codex" or "generic"
        self.model = model             # "sonnet", "fable", "opus", "haiku", "gpt-5.6-sol", etc.
        self.workdir = Path(workdir).expanduser().resolve() if workdir else APP_ROOT
        self.startup_timeout = startup_timeout
        self.response_timeout = response_timeout
        self.command = command         # generic tools: shell template with {model}
        self.codex_provider_spec = codex_provider_spec
        self._mock_mode = False
        self._mode = "unknown"
        self.startup_error: Optional[str] = None
        self._closed = threading.Event()
        self._process_lock = threading.Lock()
        self._active_processes = set()

    # ------------------------------------------------------------------
    # startup
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Check that the CLI binary is available. Returns True if ready."""
        if self._closed.is_set():
            return False
        self.startup_error = None
        if self.provider == "codex":
            try:
                codex_isolation_args(self.workdir, self.codex_provider_spec, check_environment=True)
            except CLIContractError as error:
                self._mode = "configuration-error"
                self.startup_error = f"[codex {error.code}: {error}]"
                return False
        if self.provider == "claude":
            if self._find_binary("claude"):
                self._mode = "print"
                return True
        elif self.provider == "codex":
            if self._find_binary("codex"):
                self._mode = "exec"
                return True
        elif self.provider == "generic" and self.command:
            binary = self.command.split()[0]
            if self._find_binary(binary):
                self._mode = "generic"
                return True

        logger.warning(f"{self.provider} CLI not found, entering mock mode")
        self._mock_mode = True
        self._mode = "mock"
        return False

    @property
    def mode(self) -> str:
        return self._mode

    def _find_binary(self, name: str) -> bool:
        """Check if binary is callable."""
        try:
            subprocess.run([name, "--version"], capture_output=True, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            import os
            for p in [f"/usr/local/bin/{name}", os.path.expanduser(f"~/.local/bin/{name}")]:
                try:
                    subprocess.run([p, "--version"], capture_output=True, timeout=5)
                    return True
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass
        return False

    # ------------------------------------------------------------------
    # ask
    # ------------------------------------------------------------------

    def ask(self, question: str, screenshot_path: Optional[str] = None) -> str:
        """Send question to LLM, return answer."""
        if self._closed.is_set():
            return LLMResult("[cancelled]", "cancelled")
        if self._mock_mode:
            return self._mock_response(question)

        return self._request_question(question, screenshot_path)

    def _request_question(self, question, screenshot_path=None, on_chunk=None):
        try:
            if self.provider == "codex":
                # Missing custom-provider metadata must never select OpenAI silently.
                codex_isolation_args(self.workdir, self.codex_provider_spec, check_environment=True)
            if screenshot_path:
                if self.provider not in {"claude", "codex"}:
                    raise CLIContractError("screenshot_unsupported", "This generic CLI has no declared image transport.")
                screenshot = read_screenshot(screenshot_path)
                with screenshot.staged() as image:
                    return self._dispatch_question(question, image, on_chunk)
            return self._dispatch_question(question, None, on_chunk)
        except CLIContractError as error:
            return LLMResult(f"[{self.provider} {error.code}: {error}]", "error")
        except OSError:
            return LLMResult(f"[{self.provider} screenshot_invalid: Cannot stage the selected image.]", "error")

    def _dispatch_question(self, question, image, on_chunk):
        prompt = self._build_prompt(question, str(image) if image else None)
        if self.provider == "claude":
            if image:
                return self._ask_claude(prompt, on_chunk, screenshot_dir=image.parent)
            return self._ask_claude(prompt, on_chunk)
        if self.provider == "codex":
            return self._ask_codex(prompt, screenshot_path=image)
        if self.provider == "generic" and self.command:
            return self._ask_generic(prompt)
        return self._mock_response(question)

    # ------------------------------------------------------------------
    # Generic tool (user-defined command template in tools.yaml)
    # ------------------------------------------------------------------

    def _ask_generic(self, prompt: str) -> str:
        """Run a user-defined CLI: {model} substituted, prompt on stdin."""
        import shlex
        try:
            cmd = shlex.split(self.command.replace("{model}", self.model or ""))
        except ValueError:
            return LLMResult("[generic failed: command]", "error")
        if not cmd:
            return LLMResult("[generic failed: command]", "error")
        return self._run_simple("generic", cmd, prompt)

    def _spawn(self, args, **kwargs):
        # Serialize only check/spawn/register, never callbacks, IO or wait().
        with self._process_lock:
            if self._closed.is_set():
                return None
            proc = subprocess.Popen(args, cwd=str(self.workdir),
                                    start_new_session=(os.name == "posix"), **kwargs)
            self._active_processes.add(proc)
            return proc

    @staticmethod
    def _kill_and_reap(proc) -> bool:
        """Stop the owned process group and reap the direct child (not escaped daemons)."""
        try:
            if os.name == "posix" and getattr(proc, "pid", None):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.wait(timeout=1)
            return proc.returncode is not None
        except Exception as exc:
            logger.error("CLI cleanup failed: %s", type(exc).__name__)
            return False

    def _release_process(self, proc) -> bool:
        reaped = True
        if proc is not None:
            if proc.returncode is None or (os.name == "posix" and getattr(proc, "pid", None)):
                # EOF and exit 0 concern only the direct child. Its owned group
                # can still contain descendants that closed all inherited pipes.
                reaped = self._kill_and_reap(proc)
            elif hasattr(proc, "wait"):
                try:
                    proc.wait(timeout=1)
                except Exception:
                    reaped = False
            for name in ("stdin", "stdout", "stderr"):
                pipe = getattr(proc, name, None)
                if pipe is not None:
                    pipe.close()
            if reaped:
                with self._process_lock:
                    self._active_processes.discard(proc)
        return reaped

    @staticmethod
    def _encode_prompt(prompt):
        # Check characters first so an oversized input cannot allocate an
        # unbounded encoded copy. Multibyte input also has to fit the byte cap.
        if len(prompt) > MAX_PROMPT_BYTES:
            raise _ProtocolError()
        encoded = prompt.encode("utf-8")
        if len(encoded) > MAX_PROMPT_BYTES:
            raise _ProtocolError()
        return encoded

    def _run_simple(self, provider, args, prompt=None, *, stdin_prompt=True):
        proc = None
        result = LLMResult(f"[{provider} failed: not completed]", "error")
        try:
            encoded = self._encode_prompt(prompt) if prompt is not None else None
            pending = encoded if stdin_prompt else None
            proc = self._spawn(args, stdin=subprocess.PIPE if pending is not None else None,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=False, bufsize=0)
            if proc is None:
                result = LLMResult("[cancelled]", "cancelled")
            else:
                stdout = self._read_simple(proc, pending)
                result = self._completed_result(provider, proc, stdout, "")
        except _RequestStopped as exc:
            result = LLMResult(f"[{exc}]", str(exc))
        except (subprocess.TimeoutExpired, TimeoutError):
            # Do not replace the cause with the negative exit code from cleanup.
            result = LLMResult("[timeout]", "timeout")
        except _ProtocolError:
            result = LLMResult(f"[{provider} failed: protocol]", "error")
        except FileNotFoundError:
            result = LLMResult(f"[{provider} cli not found]", "error")
        except Exception as exc:
            logger.error("%s request failed: %s", provider, type(exc).__name__)
            result = LLMResult(f"[error: {type(exc).__name__}]", "error")
        finally:
            # The direct child may have exited while descendants still own pipes.
            if proc is not None and result.status != "success":
                self._kill_and_reap(proc)
            reaped = self._release_process(proc)
        if not reaped:
            return LLMResult(f"[{provider} failed: not completed]", "error")
        return LLMResult("[cancelled]", "cancelled") if self._closed.is_set() else result

    def _read_simple(self, proc, prompt):
        """Drain binary pipes concurrently, with bounded memory and one deadline."""
        deadline = time.monotonic() + self.response_timeout
        pending = memoryview(prompt) if prompt is not None else None
        answer = bytearray()
        diagnostic_tail = bytearray()

        def check_deadline():
            if self._closed.is_set():
                raise _RequestStopped("cancelled")
            if time.monotonic() >= deadline:
                raise _RequestStopped("timeout")

        with selectors.DefaultSelector() as selector:
            pipes = [(proc.stdout, "output", selectors.EVENT_READ),
                     (proc.stderr, "diagnostic", selectors.EVENT_READ)]
            if pending is not None:
                pipes.append((proc.stdin, "input", selectors.EVENT_WRITE))
            for pipe, role, events in pipes:
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, events, role)
            while selector.get_map():
                check_deadline()
                for key, _ in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                    check_deadline()
                    pipe, role = key.fileobj, key.data
                    if role == "input":
                        try:
                            count = os.write(pipe.fileno(), pending[:65536]) if pending else 0
                            pending = pending[count:]
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            raise _ProtocolError() from None
                        if not pending:
                            selector.unregister(pipe)
                            pipe.close()
                        continue
                    try:
                        data = os.read(pipe.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not data:
                        selector.unregister(pipe)
                    elif role == "diagnostic":
                        diagnostic_tail.extend(data)
                        del diagnostic_tail[:-STDERR_TAIL_BYTES]
                    else:
                        if len(answer) + len(data) > MAX_OUTPUT_BYTES:
                            raise _ProtocolError()
                        answer.extend(data)
            check_deadline()
            proc.wait(timeout=max(0, deadline - time.monotonic()))
            check_deadline()
        try:
            return answer.decode("utf-8")
        except UnicodeError:
            raise _ProtocolError() from None

    # ------------------------------------------------------------------
    # Claude
    # ------------------------------------------------------------------

    def _claude_base_args(self) -> list:
        # Spoken text and screenshots are untrusted input. Request read-only
        # and config limits for the explicit cwd plus any staged image directory.
        # Native enforcement and managed policy need separate verification.
        args = [
            "claude",
            "--print",
            "--safe-mode",
            "--restricted",
            "--tools",
            "Read",
            "--allowedTools",
            "Read",
            "--permission-prompts",
            "none",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        if self.model:
            args += ["--model", self.model]
        return args

    def _ask_claude(self, prompt: str,
                    on_chunk: Optional[Callable[[str], None]] = None,
                    screenshot_dir: Optional[Path] = None) -> str:
        if on_chunk:
            return self._ask_claude_stream(prompt, on_chunk, screenshot_dir)
        args = self._claude_base_args()
        if screenshot_dir:
            args += ["--add-dir", str(screenshot_dir)]
        return self._run_simple("claude", args, prompt)

    def _ask_claude_stream(self, prompt: str, on_chunk: Callable[[str], None],
                           screenshot_dir: Optional[Path] = None) -> str:
        """Real token-by-token streaming via stream-json. Emits only the answer
        text (filters out the model's thinking and all system/hook events)."""
        args = self._claude_base_args() + [
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        ]
        if screenshot_dir:
            args += ["--add-dir", str(screenshot_dir)]
        proc = None
        result = LLMResult("[claude failed: not completed]", "error")
        try:
            proc = self._spawn(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=False, bufsize=0,
            )
            result = (LLMResult("[cancelled]", "cancelled") if proc is None
                      else self._read_stream(proc, prompt, on_chunk))
        except _RequestStopped as exc:
            result = LLMResult(f"[{exc}]", str(exc))
        except (subprocess.TimeoutExpired, TimeoutError):
            result = LLMResult("[timeout]", "timeout")
        except _ProtocolError:
            result = LLMResult("[claude failed: protocol]", "error")
        except FileNotFoundError:
            result = LLMResult("[claude cli not found]", "error")
        except Exception as exc:
            logger.error("Claude stream failed: %s", type(exc).__name__)
            result = LLMResult(f"[error: {type(exc).__name__}]", "error")
        finally:
            # A failed request also stops descendants holding inherited pipes.
            if proc is not None and result.status != "success":
                self._kill_and_reap(proc)
            reaped = self._release_process(proc)
        if not reaped:
            return LLMResult("[claude failed: not completed]", "error")
        return LLMResult("[cancelled]", "cancelled") if self._closed.is_set() else result

    def _read_stream(self, proc, prompt, on_chunk):
        """Bounded POSIX pipe loop: readiness never precedes blocking readline/write."""
        deadline = time.monotonic() + self.response_timeout
        pending = memoryview(prompt.encode("utf-8"))
        lines = bytearray()
        chunks = []
        answer_bytes = 0
        terminal = False
        final_text = None
        diagnostic_tail = bytearray()

        def check_deadline():
            if self._closed.is_set():
                raise _RequestStopped("cancelled")
            if time.monotonic() >= deadline:
                raise _RequestStopped("timeout")

        def emit(text):
            nonlocal answer_bytes
            if not isinstance(text, str):
                raise _ProtocolError()
            answer_bytes += len(text.encode("utf-8"))
            if answer_bytes > 2 * 1024 * 1024:
                raise _ProtocolError()
            if text:
                check_deadline()
                on_chunk(text)
                chunks.append(text)

        def parse(line):
            nonlocal terminal, final_text
            if not line.strip():
                return
            try:
                evt = json.loads(line)
                if not isinstance(evt, dict):
                    raise ValueError()
                if evt.get("type") == "result":
                    if terminal or evt.get("is_error") or str(evt.get("subtype", "")).startswith("error"):
                        raise _ProtocolError()
                    terminal = True
                    final_text = evt.get("result", "")
                    if not isinstance(final_text, str) or len(final_text.encode("utf-8")) > 2 * 1024 * 1024:
                        raise _ProtocolError()
                    if not chunks:
                        emit(final_text)
                elif evt.get("type") == "stream_event":
                    event = evt.get("event", {})
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            if terminal:
                                raise _ProtocolError()
                            emit(delta.get("text", ""))
            except (ValueError, TypeError, AttributeError, UnicodeError) as exc:
                raise _ProtocolError() from exc

        with selectors.DefaultSelector() as selector:
            for pipe, role, events in ((proc.stdin, "input", selectors.EVENT_WRITE),
                                       (proc.stdout, "output", selectors.EVENT_READ),
                                       (proc.stderr, "diagnostic", selectors.EVENT_READ)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, events, role)
            while selector.get_map():
                check_deadline()
                for key, _ in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                    check_deadline()
                    pipe, role = key.fileobj, key.data
                    if role == "input":
                        try:
                            count = os.write(pipe.fileno(), pending[:65536]) if pending else 0
                            pending = pending[count:]
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            raise _ProtocolError() from None
                        if not pending:
                            selector.unregister(pipe)
                            pipe.close()
                        continue
                    try:
                        data = os.read(pipe.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not data:
                        selector.unregister(pipe)
                        if role == "output" and lines:
                            parse(bytes(lines))
                            lines.clear()
                        continue
                    if role == "diagnostic":
                        diagnostic_tail.extend(data)
                        del diagnostic_tail[:-8192]
                        continue
                    lines.extend(data)
                    while b"\n" in lines:
                        line, _, rest = lines.partition(b"\n")
                        lines[:] = rest
                        if len(line) > 1024 * 1024:
                            raise _ProtocolError()
                        parse(bytes(line))
                    if len(lines) > 1024 * 1024:
                        raise _ProtocolError()
            check_deadline()
            proc.wait(timeout=max(0, deadline - time.monotonic()))
        if proc.returncode == 0 and chunks and not terminal:
            raise _ProtocolError()
        return self._completed_result("claude", proc, final_text if terminal else "".join(chunks), "")

    def ask_stream(self, question: str, on_chunk: Callable[[str], None],
                   screenshot_path: Optional[str] = None) -> str:
        """Ask with streaming. on_chunk(text) called for each new line of response."""
        if self._closed.is_set():
            return LLMResult("[cancelled]", "cancelled")
        if self._mock_mode:
            return self._mock_response(question)
        return self._request_question(question, screenshot_path, on_chunk)

    def _ask_codex(self, prompt: str, screenshot_path: Optional[Path] = None) -> str:
        args = ["codex", "exec", "--sandbox", "read-only", "--ephemeral"]
        try:
            args += codex_isolation_args(self.workdir, self.codex_provider_spec, check_environment=True)
        except CLIContractError as error:
            return LLMResult(f"[codex {error.code}: {error}]", "error")
        if self.model:
            args += ["--model", self.model]
        if screenshot_path:
            args += ["--image", str(screenshot_path), "--"]
        return self._run_simple("codex", args + [prompt], prompt, stdin_prompt=False)

    @staticmethod
    def _completed_result(provider: str, proc, stdout: str, stderr: str) -> str:
        """Return output only for a successful, usable CLI completion."""
        if proc.returncode is None:
            return LLMResult(f"[{provider} failed: not completed]", "error")
        if proc.returncode != 0:
            logger.error("%s CLI exited with code %s", provider, proc.returncode)
            return LLMResult(f"[{provider} failed: exit {proc.returncode}]", "error")

        output = strip_ansi(stdout or "").strip()
        if not output:
            logger.error("%s CLI exited without an answer", provider)
            return LLMResult(f"[{provider} failed: no output]", "error")
        return LLMResult(output)

    # ------------------------------------------------------------------
    # prompt
    # ------------------------------------------------------------------

    def _build_prompt(self, question: str, screenshot_path: Optional[str] = None) -> str:
        img_ref = ""
        if screenshot_path:
            img_ref = (f"\nRefer to the code or architecture visible in the screenshot: "
                       f"{screenshot_path}")
        return (
            f"Answer the following spoken question shortly and accurately.\n"
            f"Language: Russian (ответь на русском).\n"
            f"Keep it 1-2 sentences max.{img_ref}\n\n"
            f"Question: {question}"
        )

    # ------------------------------------------------------------------
    # mock
    # ------------------------------------------------------------------

    def _mock_response(self, question: str) -> str:
        return LLMResult(
            f"[MOCK — {self.provider} not connected]\n"
            f"Q: {question}\n"
            f"[Mock answer] Ответ-заглушка. Подключите Claude или Codex CLI.", "mock"
        )

    # ------------------------------------------------------------------
    # cleanup
    # ------------------------------------------------------------------

    def is_alive(self) -> bool:
        return not self._closed.is_set() and (self._mock_mode or self._mode not in {"unknown", "configuration-error"})

    def close(self) -> None:
        with self._process_lock:
            self._closed.set()
            processes = tuple(self._active_processes)
        incomplete = False
        for proc in processes:
            if self._kill_and_reap(proc):
                with self._process_lock:
                    self._active_processes.discard(proc)
            else:
                incomplete = True
        if incomplete:
            raise RuntimeError("CLI cleanup incomplete; owned processes remain")


# Backward compatibility alias
ClaudeSession = LLMSession
