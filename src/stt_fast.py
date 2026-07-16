"""
stt_fast.py — Быстрая транскрибация с выбором бэкенда.

Бэкенды:
  "groq"           - облако, whisper-large-v3-turbo, ~0.5с, точный RU (нужен GROQ_API_KEY)  ⭐
  "faster-whisper" - локально, RU+EN, ~2с на CPU (offline, без ключа)
  "streaming"      - локальный faster-whisper, chunked
  "moonshine"      - English-only (не для русского)
  "distil-whisper" - English-only (.en модели)
  "auto"           - Groq если есть ключ, иначе faster-whisper

Рекомендация: "groq" (или "auto") + faster-whisper base как offline-фолбэк.
"""

import logging
import os
import time
from pathlib import Path
from typing import Optional, Literal
from abc import ABC, abstractmethod

logger = logging.getLogger("stt_fast")

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"


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
# 0. Groq cloud STT — whisper-large-v3-turbo (FASTEST, needs GROQ_API_KEY) ⭐
# ─────────────────────────────────────────────────────────────────────────────

class GroqSTT(STTBackend):
    """Groq cloud transcription. ~0.5s, accurate Russian. Zero extra deps
    (uses stdlib urllib for a multipart POST). Needs GROQ_API_KEY env var —
    free key at https://console.groq.com."""

    def __init__(self, model_size: Optional[str] = None, model: str = GROQ_MODEL):
        self.model = model
        self._key = os.environ.get("GROQ_API_KEY", "")

    def is_available(self) -> bool:
        return bool(os.environ.get("GROQ_API_KEY"))

    def transcribe(self, wav_path: str, language: Optional[str] = "ru") -> str:
        import urllib.request
        import json
        import uuid

        key = os.environ.get("GROQ_API_KEY", self._key)
        if not key:
            raise RuntimeError("GROQ_API_KEY not set")

        with open(wav_path, "rb") as f:
            audio = f.read()

        # multipart/form-data body (stdlib, no requests dependency)
        boundary = f"----ghost{uuid.uuid4().hex}"
        parts = []

        def field(name, value):
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            parts.append(f"{value}\r\n".encode())

        field("model", self.model)
        field("response_format", "json")
        field("temperature", "0")
        if language:
            field("language", language)
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
            b"Content-Type: audio/wav\r\n\r\n"
        )
        parts.append(audio)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(parts)

        req = urllib.request.Request(
            GROQ_URL, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        start = time.time()
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        text = (data.get("text") or "").strip()
        logger.info(f"Groq [{self.model}] ({time.time()-start:.2f}s): «{text}»")
        return text


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
        segments, info = self._model.transcribe(
            wav_path,
            beam_size=1,
            language=language,               # None = auto-detect (slower)
            without_timestamps=True,
            condition_on_previous_text=False,  # each question is independent
            vad_filter=True,                 # trim leading/trailing silence
            vad_parameters={"min_silence_duration_ms": 300},
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        elapsed = time.time() - start
        lang = getattr(info, "language", language)
        logger.info(f"Faster-Whisper [{lang}] ({elapsed:.2f}s): «{text}»")
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
        "groq": GroqSTT,                     # cloud, fastest (needs GROQ_API_KEY)
        "moonshine": MoonshineSTT,           # English-only
        "distil-whisper": DistilWhisperSTT,  # English-only (.en models)
        "faster-whisper": FasterWhisperSTT,  # multilingual (RU+EN) — local default
        "streaming": StreamingWhisperSTT,    # multilingual, chunked
    }

    if backend == "auto":
        # Prefer Groq cloud (fastest) if a key is present; else local
        # multilingual faster-whisper. moonshine/distil are English-only
        # and never auto-selected.
        if GroqSTT().is_available():
            logger.info("Auto-selected STT: groq (cloud, whisper-large-v3-turbo)")
            return GroqSTT()
        logger.info("Auto-selected STT: faster-whisper (local, no GROQ_API_KEY)")
        return FasterWhisperSTT(**kwargs)

    if backend not in backends:
        raise ValueError(f"Unknown STT backend: {backend}. Use {list(backends.keys())}")

    backend_cls = backends[backend]
    if not backend_cls().is_available():
        # Graceful degradation: cloud backend requested but unavailable → run
        # local instead of crashing (app keeps working; upgrades when key set).
        if backend == "groq":
            logger.warning("groq requested but GROQ_API_KEY missing → faster-whisper (local)")
            return FasterWhisperSTT(**kwargs)
        raise ImportError(
            f"{backend} not available. faster-whisper: pip install faster-whisper | "
            f"moonshine/distil-whisper: pip install transformers torch (English-only)"
        )

    if backend == "groq":
        return GroqSTT()
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
