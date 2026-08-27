"""Config initialization helper — removes duplication in main.py."""

from pathlib import Path
from audio_capture import AudioCapture
from screen_monitor import ScreenGrabber
from orchestrator import LLMSession


class ConfigBuilder:
    """Fluent config builder with sensible defaults."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.project_root = Path(__file__).resolve().parent

    def get_screenshot_config(self) -> dict:
        """Extract screenshot config with defaults."""
        ss_cfg = self.cfg.get("screenshot", {})
        return {
            "path": ss_cfg.get("path", "temp_screen.png"),
            "monitor": ss_cfg.get("monitor", 0),
            "hash_threshold": ss_cfg.get("hash_threshold", 5),
        }

    def get_backend_config(self) -> dict:
        """Extract backend config with defaults."""
        be_cfg = self.cfg.get("backend", {})
        return {
            "provider": be_cfg.get("provider", "claude"),
            "model": be_cfg.get("model", "sonnet"),
            "workdir": be_cfg.get("workdir", str(self.project_root)),
            "startup_timeout": be_cfg.get("startup_timeout", 15),
            "response_timeout": be_cfg.get("response_timeout", 60),
        }

    def create_screen_grabber(self) -> ScreenGrabber:
        """Create ScreenGrabber with config."""
        ss = self.get_screenshot_config()
        return ScreenGrabber(
            output_path=str(self.project_root / ss["path"]),
            monitor_idx=ss["monitor"],
            hash_threshold=ss["hash_threshold"],
        )

    def create_llm_session(self) -> LLMSession:
        """Create LLMSession with config."""
        be = self.get_backend_config()
        return LLMSession(
            provider=be["provider"],
            model=be["model"],
            workdir=be["workdir"],
            startup_timeout=be["startup_timeout"],
            response_timeout=be["response_timeout"],
        )

    def create_audio_capture(
        self, on_question, whisper_model: str = "base"
    ) -> AudioCapture:
        """Create AudioCapture with config."""
        return AudioCapture(
            on_question=on_question,
            whisper_model=whisper_model,
            output_dir=str(self.project_root),
        )
