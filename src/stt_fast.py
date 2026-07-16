"""
stt_fast.py — Быстрая транскрибация с выбором бэкенда.

Режимы (от быстрого к точному):
  1. "moonshine"       - микро-Whisper, 2-3x быстрее, ~95% accuracy
  2. "distil-whisper"  - дистиллированный Whisper, 6x быстрее
  3. "faster-whisper"  - текущий стандарт (базовая версия)
  4. "silero"          - Silero STT (если доступен)
  5. "ollama"          - локальный LLM с речью (экспериментально)

Рекомендация: "moonshine" + streaming
"""

import logging
import time
from pathlib import Path
from typing import Optional, Literal
from abc import ABC, abstractmethod

logger = logging.getLogger("stt_fast")


# ─────────────────────────────────────────────────────────────────────────────
# Base STT interface
# ─────────────────────────────────────────────────────────────────────────────

class STTBackend(ABC):
    """Abstract base for STT backends."""

    @abstractmethod
    def transcribe(self, wav_path: str, language: Optional[str] = None) -> str:
        """Transcribe WAV file to text."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if backend is installed/available."""
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 1. Moonshine — микро-Whisper (FASTEST + good quality)
# ─────────────────────────────────────────────────────────────────────────────

class MoonshineSTT(STTBackend):
    """Moonshine micro-Whisper: 2-3x faster, 95% accuracy, 75M params."""

    def __init__(self):
        self._model = None

    def is_available(self) -> bool:
        try:
            import transformers
            return True
        except ImportError:
            return False

    def _load(self):
        if self._model is not None:
            return
        try:
            from transformers import pipeline
            logger.info("Loading Moonshine STT (75M micro-Whisper)...")
            self._model = pipeline("automatic-speech-recognition", model="usefast/moonshine-600m")
            logger.info("✓ Moonshine ready")
        except Exception as e:
            logger.error(f"Moonshine load failed: {e}")
            raise

    def transcribe(self, wav_path: str, language: Optional[str] = None) -> str:
        self._load()
        start = time.time()
        result = self._model(wav_path, chunk_length_s=30, stride_length_s=5)
        text = result.get("text", "").strip()
        elapsed = time.time() - start
        logger.info(f"Moonshine ({elapsed:.2f}s): «{text}»")
        return text


# ─────────────────────────────────────────────────────────────────────────────
# 2. Distil-Whisper — дистиллированный (6x faster)
# ─────────────────────────────────────────────────────────────────────────────

class DistilWhisperSTT(STTBackend):
    """Distil-Whisper: 6x faster than base, 59M params, high accuracy."""

    def __init__(self):
        self._model = None

    def is_available(self) -> bool:
        try:
            import transformers
            return True
        except ImportError:
            return False

    def _load(self):
        if self._model is not None:
            return
        try:
            from transformers import pipeline
            logger.info("Loading Distil-Whisper (59M, 6x faster)...")
            self._model = pipeline(
                "automatic-speech-recognition",
                model="distil-whisper/distil-medium.en",
                device=self._get_device(),
            )
            logger.info("✓ Distil-Whisper ready")
        except Exception as e:
            logger.error(f"Distil-Whisper load failed: {e}")
            raise

    @staticmethod
    def _get_device() -> int:
        """Auto-detect GPU device."""
        try:
            import torch
            return 0 if torch.cuda.is_available() else -1
        except:
            return -1

    def transcribe(self, wav_path: str, language: Optional[str] = None) -> str:
        self._load()
        start = time.time()
        result = self._model(wav_path)
        text = result.get("text", "").strip()
        elapsed = time.time() - start
        logger.info(f"Distil-Whisper ({elapsed:.2f}s): «{text}»")
        return text


# ─────────────────────────────────────────────────────────────────────────────
# 3. Faster-Whisper (уже оптимизирован в audio_capture.py)
# ─────────────────────────────────────────────────────────────────────────────

class FasterWhisperSTT(STTBackend):
    """Faster-Whisper: CTransformers backend, optimized."""

    def __init__(self, model_size: str = "tiny"):
        self.model_size = model_size
        self._model = None

    def is_available(self) -> bool:
        try:
            from faster_whisper import WhisperModel
            return True
        except ImportError:
            return False

    def _load(self):
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
            device = self._get_device()
            compute_type = "float16" if device == "cuda" else "int8"
            logger.info(f"Loading faster-whisper/{self.model_size} ({device}/{compute_type})...")
            self._model = WhisperModel(
                self.model_size,
                device=device,
                compute_type=compute_type,
            )
            logger.info("✓ Faster-Whisper ready")
        except Exception as e:
            logger.error(f"Faster-Whisper load failed: {e}")
            raise

    @staticmethod
    def _get_device() -> str:
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except:
            return "cpu"

    def transcribe(self, wav_path: str, language: Optional[str] = None) -> str:
        self._load()
        start = time.time()
        segments, _ = self._model.transcribe(
            wav_path,
            beam_size=1,
            language=language,
            without_timestamps=True,
        )
        text = " ".join(seg.text.strip() for seg in segments)
        elapsed = time.time() - start
        logger.info(f"Faster-Whisper ({elapsed:.2f}s): «{text}»")
        return text


# ─────────────────────────────────────────────────────────────────────────────
# 4. Streaming STT — транскрибируем в реальном времени
# ─────────────────────────────────────────────────────────────────────────────

class StreamingWhisperSTT(STTBackend):
    """Whisper in streaming mode — transcribe as audio arrives."""

    def __init__(self, model_size: str = "tiny"):
        self.model_size = model_size
        self._model = None

    def is_available(self) -> bool:
        try:
            from faster_whisper import WhisperModel
            return True
        except ImportError:
            return False

    def _load(self):
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
            device = "cuda" if self._has_cuda() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            logger.info(f"Loading Streaming-Whisper/{self.model_size}...")
            self._model = WhisperModel(
                self.model_size,
                device=device,
                compute_type=compute_type,
            )
            logger.info("✓ Streaming-Whisper ready")
        except Exception as e:
            logger.error(f"Streaming-Whisper load failed: {e}")
            raise

    @staticmethod
    def _has_cuda() -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except:
            return False

    def transcribe(self, wav_path: str, language: Optional[str] = None) -> str:
        """Transcribe with chunking for faster incremental results."""
        self._load()
        start = time.time()

        # Open WAV and process in 30-second chunks for streaming feel
        import wave
        import numpy as np

        with wave.open(wav_path, 'rb') as wf:
            sr = wf.getframerate()
            chunk_frames = int(30 * sr)  # 30 sec chunks
            texts = []

            while True:
                frames = wf.readframes(chunk_frames)
                if not frames:
                    break

                # Process chunk
                audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                segments, _ = self._model.transcribe(
                    audio,
                    beam_size=1,
                    language=language,
                    without_timestamps=True,
                )
                chunk_text = " ".join(seg.text.strip() for seg in segments)
                if chunk_text:
                    texts.append(chunk_text)

        text = " ".join(texts)
        elapsed = time.time() - start
        logger.info(f"Streaming-Whisper ({elapsed:.2f}s, chunked): «{text}»")
        return text


# ─────────────────────────────────────────────────────────────────────────────
# STT Factory
# ─────────────────────────────────────────────────────────────────────────────

def create_stt(backend: str = "auto", **kwargs) -> STTBackend:
    """
    Create STT backend.

    Args:
        backend: "moonshine", "distil-whisper", "faster-whisper", "streaming", "auto"
        **kwargs: passed to backend constructor

    Returns:
        STTBackend instance

    Примеры:
        stt = create_stt("moonshine")
        stt = create_stt("faster-whisper", model_size="base")
        stt = create_stt("auto")  # auto-select fastest available
    """
    backends = {
        "moonshine": MoonshineSTT,
        "distil-whisper": DistilWhisperSTT,
        "faster-whisper": FasterWhisperSTT,
        "streaming": StreamingWhisperSTT,
    }

    if backend == "auto":
        # Auto-detect: moonshine > distil > faster
        for name in ["moonshine", "distil-whisper", "faster-whisper"]:
            backend_cls = backends[name]
            if backend_cls().is_available():
                logger.info(f"Auto-selected STT: {name}")
                return backend_cls(**kwargs)
        # Fallback to faster-whisper
        logger.warning("No fast STT available, using faster-whisper")
        return FasterWhisperSTT(**kwargs)

    if backend not in backends:
        raise ValueError(f"Unknown STT backend: {backend}. Use {list(backends.keys())}")

    backend_cls = backends[backend]
    if not backend_cls().is_available():
        raise ImportError(f"{backend} not available. Install: pip install transformers faster-whisper")

    return backend_cls(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python stt_fast.py <wav_file> [backend]")
        print("Backends: moonshine, distil-whisper, faster-whisper, streaming, auto")
        sys.exit(1)

    wav_file = sys.argv[1]
    backend_name = sys.argv[2] if len(sys.argv) > 2 else "auto"

    logging.basicConfig(level=logging.INFO)

    stt = create_stt(backend_name)
    print(f"\n{'='*60}")
    print(f"Transcribing: {wav_file} ({Path(wav_file).stat().st_size / 1024:.0f} KB)")
    print(f"Backend: {backend_name}")
    print(f"{'='*60}\n")

    text = stt.transcribe(wav_file)
    print(f"\nResult:\n{text}\n")
