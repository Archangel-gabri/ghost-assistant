"""
worker.py — QThread that runs the audio → STT → screenshot → Claude pipeline.

Emits Qt signals for the main thread to handle UI updates.
"""

import logging
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

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
        # QThread.finished means run() returned, not that resource cleanup worked.
        self.cleanup_complete = False
        # Audio callbacks can arrive from different threads. One answer owns the
        # streaming UI at a time, and cleanup waits for that callback to unwind.
        self._question_lock = threading.Lock()

    # ------------------------------------------------------------------
    # thread lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main thread function — starts audio capture and event loop."""
        # A worker is single-use. Stop may arrive before QThread enters run();
        # clearing the event here would erase that cancellation.
        try:
            if self._stop_event.is_set():
                return
            ss_cfg = self._cfg.get("screenshot", {})
            if ss_cfg.get("enabled", True):
                self._grabber = ScreenGrabber(
                    output_path=str(PROJECT_ROOT / ss_cfg.get("path", "temp_screen.png")),
                    monitor_idx=ss_cfg.get("monitor", 0),
                    hash_threshold=ss_cfg.get("hash_threshold", 5),
                )
            if self._stop_event.is_set():
                return

            be_cfg = self._cfg.get("backend", {})
            self._session = LLMSession(
                provider=be_cfg.get("provider", "claude"),
                model=be_cfg.get("model", "sonnet"),
                workdir=str(be_cfg.get("workdir", str(PROJECT_ROOT))),
                startup_timeout=be_cfg.get("startup_timeout", 15),
                response_timeout=be_cfg.get("response_timeout", 60),
                command=be_cfg.get("command"),
                codex_provider_spec=be_cfg.get("codex_provider_spec"),
            )
            if self._stop_event.is_set():
                return
            started = self._session.start()
            if self._stop_event.is_set():
                return
            if not started and self._session.mode == "configuration-error":
                self.error_occurred.emit(self._session.startup_error or "LLM configuration error")
                return

            # Audio owns its callback thread; no hidden model warmup runs in
            # parallel with questions or survives the session cancellation.
            self._audio = AudioCapture(
                on_question=self._on_question,
                on_error=self._on_audio_error,
                stop_event=self._stop_event,
                whisper_model=be_cfg.get("whisper_model", "base"),
                stt_backend=be_cfg.get("stt_backend", "faster-whisper"),
                language=be_cfg.get("language", "ru"),
                output_dir=str(PROJECT_ROOT),
            )
            if self._stop_event.is_set():
                return
            if not self._audio.start():
                if not self._stop_event.is_set():
                    self.error_occurred.emit("Audio capture failed — no loopback device")
                return
            if not self._stop_event.is_set():
                self.status_changed.emit("listening")
            self._stop_event.wait()
        except Exception as error:
            logger.error("Worker startup failed: %s", type(error).__name__)
            if not self._stop_event.is_set():
                self.error_occurred.emit(f"[error: {type(error).__name__}]")
        finally:
            self._stop_event.set()
            cleanup_complete = True
            # Cancel/reap the LLM before AudioCapture.stop() can wait for an
            # audio callback currently blocked in the model request.
            for resource, method in ((self._session, "close"), (self._audio, "stop")):
                if resource is not None:
                    try:
                        getattr(resource, method)()
                    except Exception as error:
                        cleanup_complete = False
                        logger.error("Worker cleanup failed: %s", type(error).__name__)
            with self._question_lock:
                if self._grabber is not None:
                    try:
                        self._grabber.close()
                    except Exception as error:
                        cleanup_complete = False
                        logger.error("Screen cleanup failed: %s", type(error).__name__)
            self.cleanup_complete = cleanup_complete
            if cleanup_complete:
                logger.info("Worker stopped")
            else:
                logger.error("Worker cleanup incomplete; resources retained")

    def stop(self) -> None:
        """Request stop without blocking the GUI on audio, processes or QThread."""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # pipeline
    # ------------------------------------------------------------------

    def _on_question(self, question_text: str) -> None:
        """Called from audio thread when STT produces text."""
        if self._stop_event.is_set():
            return
        with self._question_lock:
            if self._stop_event.is_set():
                return
            self._process_question(question_text)

    def _emit_chunk(self, chunk: str) -> None:
        if not self._stop_event.is_set():
            self.answer_chunk.emit(chunk)

    def _on_audio_error(self, message: str) -> None:
        if not self._stop_event.is_set():
            self.error_occurred.emit(message)

    def _process_question(self, question_text: str) -> None:
        self.question_detected.emit(question_text)

        # screenshot (configurable, parallel if enabled)
        use_screenshot = self._cfg.get("screenshot", {}).get("enabled", True)
        ss_path = None
        if use_screenshot and self._grabber:
            try:
                ss_path = self._grabber.grab_force()
            except Exception as error:
                logger.warning("Screenshot failed: %s", type(error).__name__)

        if self._stop_event.is_set():
            return

        # ask LLM with streaming
        self.status_changed.emit("processing")
        try:
            answer = self._session.ask_stream(
                question_text,
                on_chunk=self._emit_chunk,
                screenshot_path=str(ss_path) if ss_path else None,
            )
        except Exception as error:
            logger.error("LLM request failed: %s", type(error).__name__)
            if not self._stop_event.is_set():
                self.error_occurred.emit(f"[error: {type(error).__name__}]")
            return

        if self._stop_event.is_set():
            return
        # str-compatible results carry their outcome per request. A successful
        # literal '[timeout]' remains an answer, not a guessed failure marker.
        status = getattr(answer, "status", "success")
        if status == "mock":
            self.error_occurred.emit(f"[{self._session.provider} offline]")
        elif status != "success":
            self.error_occurred.emit(str(answer))
        elif not answer:
            self.error_occurred.emit(f"[{self._session.provider} failed: no output]")
        else:
            self.answer_ready.emit(str(answer))
            self.status_changed.emit("listening")
