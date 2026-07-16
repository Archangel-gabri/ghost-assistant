"""
worker.py — QThread that runs the audio → STT → screenshot → Claude pipeline.

Emits Qt signals for the main thread to handle UI updates.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal, Slot

from audio_capture import AudioCapture
from screen_monitor import ScreenGrabber
from orchestrator import LLMSession

logger = logging.getLogger("worker")

PROJECT_ROOT = Path(__file__).resolve().parent
MAX_PARALLEL_TASKS = 2  # Limit for capture + transcribe parallelism


class PipelineWorker(QThread):
    """Runs the full Ghost pipeline in a background thread."""

    # signals
    question_detected = Signal(str)
    answer_chunk = Signal(str)             # streaming: each line of response
    answer_ready = Signal(str)             # final full response
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self._cfg = config
        self._audio: Optional[AudioCapture] = None
        self._grabber: Optional[ScreenGrabber] = None
        self._session: Optional[LLMSession] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # thread lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main thread function — starts audio capture and event loop."""
        self._stop_event.clear()
        ss_cfg = self._cfg["screenshot"]

        # screen grabber
        self._grabber = ScreenGrabber(
            output_path=str(PROJECT_ROOT / ss_cfg["path"]),
            monitor_idx=ss_cfg["monitor"],
            hash_threshold=ss_cfg["hash_threshold"],
        )

        # LLM backend config
        be_cfg = self._cfg.get("backend", {})
        provider = be_cfg.get("provider", "claude")
        model = be_cfg.get("model", "sonnet")
        workdir = be_cfg.get("workdir", str(PROJECT_ROOT))
        timeout = be_cfg.get("response_timeout", 60)

        # LLM session
        self._session = LLMSession(
            provider=provider,
            model=model,
            workdir=str(workdir),
            startup_timeout=be_cfg.get("startup_timeout", 15),
            response_timeout=timeout,
        )
        ok = self._session.start()
        label = f"{provider}/{model}" if ok else f"{provider}/mock"
        self.status_changed.emit(f"listening ({label})")

        # audio capture — calls on_question from its internal thread
        self._audio = AudioCapture(
            on_question=self._on_question,
            whisper_model="base",
            output_dir=str(PROJECT_ROOT),
        )

        if not self._audio.start():
            self.error_occurred.emit("Audio capture failed — no loopback device")
            return

        self.status_changed.emit("listening")

        # keep thread alive until stop() is called
        while not self._stop_event.is_set():
            self._stop_event.wait(0.5)

        # cleanup
        self._audio.stop()
        self._grabber.close()
        self._session.close()
        logger.info("Worker stopped")

    def stop(self) -> None:
        """Request graceful stop."""
        self._stop_event.set()
        if self._audio:
            self._audio.stop()
        self.wait(timeout=3000)

    # ------------------------------------------------------------------
    # pipeline
    # ------------------------------------------------------------------

    def _on_question(self, question_text: str) -> None:
        """Called from audio thread when STT produces text."""
        if self._stop_event.is_set():
            return

        logger.info(f"→ Question: «{question_text}»")
        self.question_detected.emit(question_text)

        # screenshot (configurable, parallel if enabled)
        use_screenshot = self._cfg.get("screenshot", {}).get("enabled", True)
        ss_path = None
        if use_screenshot and self._grabber:
            try:
                ss_path = self._grabber.grab_force()
                logger.info(f"Screenshot: {ss_path}")
            except Exception as e:
                logger.warning(f"Screenshot failed: {e}")

        # ask LLM with streaming
        self.status_changed.emit("processing")
        try:
            answer = self._session.ask_stream(
                question_text,
                on_chunk=lambda chunk: self.answer_chunk.emit(chunk),
                screenshot_path=str(ss_path) if ss_path else None,
            )
            if not answer or answer.startswith("[MOCK"):
                answer = f"[{self._session.provider} offline] Q: {question_text}"
        except Exception as e:
            logger.error(f"LLM request failed: {e}")
            answer = f"[error: {type(e).__name__}]"

        logger.info(f"← Answer: «{answer[:100]}»")
        self.answer_ready.emit(answer)
        self.status_changed.emit("listening")
