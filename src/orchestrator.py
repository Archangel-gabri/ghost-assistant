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
from utils import strip_ansi, Provider

logger = logging.getLogger("orchestrator")


class LLMSession:
    """Unified interface for Claude CLI and Codex CLI."""

    def __init__(self, provider: str = "claude", model: str = "sonnet",
                 workdir: Optional[str] = None,
                 startup_timeout: float = 15.0, response_timeout: float = 60.0):
        self.provider = provider       # "claude" or "codex"
        self.model = model             # "sonnet", "fable", "opus", "haiku", "gpt-5.6-sol", etc.
        self.workdir = Path(workdir) if workdir else Path.cwd()
        self.startup_timeout = startup_timeout
        self.response_timeout = response_timeout
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
        else:
            return self._mock_response(question)

    # ------------------------------------------------------------------
    # Claude
    # ------------------------------------------------------------------

    def _ask_claude(self, prompt: str,
                    on_chunk: Optional[Callable[[str], None]] = None) -> str:
        args = ["claude", "--print"]
        if self.model and self.model != "sonnet":
            args += ["--model", self.model]
        try:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.workdir),
                text=True,
            )
            if on_chunk:
                proc.stdin.write(prompt)
                proc.stdin.close()
                buf = []
                for line in proc.stdout:
                    clean = strip_ansi(line)
                    if clean.strip():
                        on_chunk(clean)
                        buf.append(clean)
                proc.wait(timeout=5)
                return "".join(buf).strip()
            else:
                stdout, stderr = proc.communicate(input=prompt, timeout=self.response_timeout)
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
