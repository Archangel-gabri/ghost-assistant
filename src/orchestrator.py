"""
orchestrator.py — LLM backend manager (Claude CLI or Codex CLI).

Supports:
  - Claude Code CLI (--print mode): models fable, opus, sonnet, haiku
  - Codex CLI (exec mode): models gpt-5.6-sol, o3, gpt-5.5, etc.
  - Mock fallback if no CLI is available
"""

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional, Callable
from utils import strip_ansi

logger = logging.getLogger("orchestrator")


class LLMSession:
    """Unified interface for Claude CLI and Codex CLI."""

    def __init__(self, provider: str = "claude", model: str = "sonnet",
                 workdir: Optional[str] = None,
                 startup_timeout: float = 15.0, response_timeout: float = 60.0,
                 command: Optional[str] = None):
        self.provider = provider       # "claude", "codex" or "generic"
        self.model = model             # "sonnet", "fable", "opus", "haiku", "gpt-5.6-sol", etc.
        self.workdir = Path(workdir) if workdir else Path.cwd()
        self.startup_timeout = startup_timeout
        self.response_timeout = response_timeout
        self.command = command         # generic tools: shell template with {model}
        self._mock_mode = False
        self._mode = "unknown"

    # ------------------------------------------------------------------
    # startup
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Check that the CLI binary is available. Returns True if ready."""
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
        if self._mock_mode:
            return self._mock_response(question)

        prompt = self._build_prompt(question, screenshot_path)

        if self.provider == "claude":
            return self._ask_claude(prompt)
        elif self.provider == "codex":
            return self._ask_codex(prompt)
        elif self.provider == "generic" and self.command:
            return self._ask_generic(prompt)
        else:
            return self._mock_response(question)

    # ------------------------------------------------------------------
    # Generic tool (user-defined command template in tools.yaml)
    # ------------------------------------------------------------------

    def _ask_generic(self, prompt: str) -> str:
        """Run a user-defined CLI: {model} substituted, prompt on stdin."""
        import shlex
        cmd = shlex.split(self.command.replace("{model}", self.model or ""))
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=str(self.workdir), text=True,
            )
            stdout, _ = proc.communicate(input=prompt, timeout=self.response_timeout)
            return strip_ansi(stdout).strip()
        except subprocess.TimeoutExpired:
            logger.error(f"generic tool timed out after {self.response_timeout}s")
            try:
                proc.kill()
            except Exception:
                pass
            return "[timeout]"
        except FileNotFoundError:
            return f"[{cmd[0]} not found]"
        except Exception as e:
            logger.error(f"generic tool failed: {type(e).__name__}: {e}")
            return f"[error: {type(e).__name__}]"

    # ------------------------------------------------------------------
    # Claude
    # ------------------------------------------------------------------

    def _claude_base_args(self) -> list:
        # --dangerously-skip-permissions lets --print mode READ the screenshot
        # file autonomously (otherwise it stops to ask permission and never
        # answers visual "what's on line N" questions).
        args = ["claude", "--print", "--dangerously-skip-permissions"]
        if self.model and self.model != "sonnet":
            args += ["--model", self.model]
        return args

    def _ask_claude(self, prompt: str,
                    on_chunk: Optional[Callable[[str], None]] = None) -> str:
        if on_chunk:
            return self._ask_claude_stream(prompt, on_chunk)
        args = self._claude_base_args()
        try:
            proc = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=str(self.workdir), text=True,
            )
            stdout, _ = proc.communicate(input=prompt, timeout=self.response_timeout)
            return strip_ansi(stdout).strip()
        except subprocess.TimeoutExpired:
            logger.error(f"Claude request timed out after {self.response_timeout}s")
            try:
                proc.kill()
            except Exception:
                pass
            return "[timeout]"
        except FileNotFoundError:
            logger.error("Claude CLI not found in PATH")
            return "[claude cli not found]"
        except Exception as e:
            logger.error(f"Claude request failed: {type(e).__name__}: {e}")
            return f"[error: {type(e).__name__}]"

    def _ask_claude_stream(self, prompt: str, on_chunk: Callable[[str], None]) -> str:
        """Real token-by-token streaming via stream-json. Emits only the answer
        text (filters out the model's thinking and all system/hook events)."""
        import json
        import select

        args = self._claude_base_args() + [
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        ]
        proc = None
        try:
            proc = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=str(self.workdir), text=True, bufsize=1,
            )
            proc.stdin.write(prompt)
            proc.stdin.close()

            buf = []
            deadline = time.time() + self.response_timeout
            while True:
                if time.time() > deadline:
                    logger.error("Claude stream timed out")
                    proc.kill()
                    break
                ready, _, _ = select.select([proc.stdout], [], [], 0.3)
                if not ready:
                    if proc.poll() is not None:
                        break
                    continue
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    continue
                line = line.strip()
                if not line or line[0] != "{":
                    continue
                try:
                    evt = json.loads(line)
                except Exception:
                    continue
                etype = evt.get("type")
                if etype == "stream_event":
                    ev = evt.get("event", {})
                    if ev.get("type") == "content_block_delta":
                        delta = ev.get("delta", {})
                        if delta.get("type") == "text_delta":   # answer only, not thinking
                            txt = delta.get("text", "")
                            if txt:
                                on_chunk(txt)
                                buf.append(txt)
                elif etype == "result":
                    # final fallback: full text if no deltas were captured
                    if not buf and evt.get("result"):
                        on_chunk(evt["result"])
                        buf.append(evt["result"])
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
            return "".join(buf).strip()
        except FileNotFoundError:
            logger.error("Claude CLI not found in PATH")
            return "[claude cli not found]"
        except Exception as e:
            logger.error(f"Claude stream failed: {type(e).__name__}: {e}")
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            return f"[error: {type(e).__name__}]"

    def ask_stream(self, question: str, on_chunk: Callable[[str], None],
                   screenshot_path: Optional[str] = None) -> str:
        """Ask with streaming. on_chunk(text) called for each new line of response."""
        if self._mock_mode:
            return self._mock_response(question)
        prompt = self._build_prompt(question, screenshot_path)
        if self.provider == "claude":
            return self._ask_claude(prompt, on_chunk=on_chunk)
        elif self.provider == "codex":
            return self._ask_codex(prompt)
        return self._mock_response(question)

    def _ask_codex(self, prompt: str) -> str:
        args = ["codex", "exec"]
        if self.model:
            args += ["--model", self.model]
        try:
            proc = subprocess.Popen(
                args + [prompt],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.workdir),
                text=True,
            )
            stdout, stderr = proc.communicate(timeout=self.response_timeout)
            return strip_ansi(stdout).strip()
        except subprocess.TimeoutExpired:
            logger.error(f"Codex request timed out after {self.response_timeout}s")
            try:
                proc.kill()
            except Exception:
                pass
            return "[timeout]"
        except FileNotFoundError:
            logger.error("Codex CLI not found in PATH")
            return "[codex cli not found]"
        except Exception as e:
            logger.error(f"Codex request failed: {type(e).__name__}: {e}")
            return f"[error: {type(e).__name__}]"

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
        return (
            f"[MOCK — {self.provider} not connected]\n"
            f"Q: {question}\n"
            f"[Mock answer] Ответ-заглушка. Подключите Claude или Codex CLI."
        )

    # ------------------------------------------------------------------
    # cleanup
    # ------------------------------------------------------------------

    def is_alive(self) -> bool:
        return self._mock_mode or self._mode != "unknown"

    def close(self) -> None:
        pass  # stateless — each ask() spawns a new process


# Backward compatibility alias
ClaudeSession = LLMSession
