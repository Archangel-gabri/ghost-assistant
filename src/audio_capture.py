"""
audio_capture.py — system audio loopback capture + VAD + STT.

Pipeline:
  PipeWire/PulseAudio monitor source
  → sounddevice InputStream (16kHz mono)
  → energy-based VAD (RMS threshold, seamless switching to Silero when available)
  → silence gap > 1.0s triggers save to temp_question.wav
  → faster-whisper (tiny) transcribes to text
  → result emitted via callback

Dependencies:
  - sounddevice, numpy (always)
  - faster-whisper (optional, install: pip install faster-whisper)
  - torch + silero-vad (optional, for model-based VAD)
"""

import collections
import importlib
import logging
import subprocess
import threading
import time
import wave
from pathlib import Path
from types import ModuleType
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger("audio_capture")


def _load_sounddevice() -> ModuleType:
    """Load the native audio backend only when an audio path needs it."""
    return importlib.import_module("sounddevice")


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000          # whisper models expect 16kHz
CHANNELS = 1                  # mono
BLOCK_DURATION = 32           # ms per audio block (512 samples @ 16kHz)
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_DURATION / 1000)  # 512 samples

# VAD thresholds
SILENCE_TIMEOUT = 1.0         # seconds of silence to trigger processing
SPEECH_TIMEOUT = 15.0         # max seconds of continuous speech before forcing a cut
ENERGY_THRESHOLD = 0.01       # RMS threshold for energy-based VAD (calibrate per mic)

# output
TEMP_QUESTION_WAV = "temp_question.wav"


# ---------------------------------------------------------------------------
# energy-based VAD (zero-dependency, always available)
# ---------------------------------------------------------------------------

class EnergyVAD:
    """Simple RMS-energy voice activity detector."""

    def __init__(self, threshold: float = ENERGY_THRESHOLD, sample_rate: int = SAMPLE_RATE):
        self.threshold = threshold
        self.sample_rate = sample_rate

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        if audio_chunk.size == 0:
            return False
        rms = float(np.sqrt(np.mean(audio_chunk.astype(np.float64) ** 2)))
        return rms > self.threshold


# ---------------------------------------------------------------------------
# Silero VAD wrapper (loads on first use)
# ---------------------------------------------------------------------------

class SileroVAD:
    """Silero VAD via torch.hub. Loads model on demand."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._model = None
        self._utils = None

    def _load(self):
        if self._model is not None:
            return
        import torch
        model, utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            onnx=False,
            trust_repo=True,
        )
        self._model = model
        self._utils = utils

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Returns True if speech detected in chunk."""
        self._load()
        import torch
        tensor = torch.from_numpy(audio_chunk.astype(np.float32))
        if tensor.dim() == 1:
            tensor = tensor.unsqueeze(0)
        speech_prob = self._model(tensor, self.sample_rate).item()
        return speech_prob > 0.5


def _make_vad(prefer_silero: bool = False) -> object:
    """Factory: return best available VAD instance.
    
    Energy-based VAD is the default — zero dependencies, works with any
    block size. Silero VAD requires torch + torchaudio and exact 512-sample
    blocks at 16kHz. Enable with prefer_silero=True if needed.
    """
    if prefer_silero:
        try:
            import torch  # noqa: F401
            vad = SileroVAD(SAMPLE_RATE)
            vad._load()  # test load
            logger.info("VAD: Silero loaded")
            return vad
        except Exception as e:
            logger.warning(f"Silero VAD unavailable ({e}), falling back to energy-based")
    logger.info("VAD: energy-based (RMS threshold)")
    return EnergyVAD(ENERGY_THRESHOLD, SAMPLE_RATE)


# ---------------------------------------------------------------------------
# STT via faster-whisper
# ---------------------------------------------------------------------------

class WhisperSTT:
    """Speech-to-text using faster-whisper. Lazy-loads model on first use."""

    _shared_model = None  # Class-level cache
    _model_lock = threading.Lock()

    def __init__(self, model_size: str = "tiny", device: str = "auto",
                 compute_type: str = "auto"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load(self) -> None:
        """Load model once, cache at class level for reuse."""
        if self._model is not None:
            return

        with self._model_lock:
            if self._model is not None:
                return

            try:
                from faster_whisper import WhisperModel
            except ImportError:
                logger.error("faster-whisper not installed")
                raise

            device = self.device
            compute_type = self.compute_type
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"
            if compute_type == "auto":
                compute_type = "float16" if device == "cuda" else "int8"

            logger.info(f"Loading faster-whisper/{self.model_size} ({device}/{compute_type}) ...")
            self._model = WhisperModel(
                self.model_size,
                device=device,
                compute_type=compute_type,
            )
            logger.info("Whisper ready")

    def transcribe(self, wav_path: str, language: str = None) -> str:
        """Transcribe WAV file. Blocking call."""
        self._load()
        start = time.time()
        segments, _ = self._model.transcribe(
            wav_path,
            beam_size=1,
            language=language,
            without_timestamps=True,  # Speed up
        )
        text = " ".join(seg.text.strip() for seg in segments)
        elapsed = time.time() - start
        logger.info(f"STT ({elapsed:.1f}s): «{text}»")
        return text


# ---------------------------------------------------------------------------
# audio capture orchestrator
# ---------------------------------------------------------------------------

class AudioCapture:
    """
    Captures system loopback audio, runs VAD, triggers STT on silence gap.

    Usage:
        cap = AudioCapture(on_question=lambda text: print(f"Q: {text}"))
        cap.start()
        # ... audio flows ...
        cap.stop()
    """

    def __init__(self,
                 on_question: Callable[[str], None],
                 device_name: Optional[str] = None,
                 prefer_silero: bool = False,
                 whisper_model: str = "base",
                 stt_backend: str = "faster-whisper",
                 language: Optional[str] = "ru",
                 output_dir: Optional[str] = None):
        """
        Args:
            on_question: callback(text) when a question is transcribed.
            device_name: PulseAudio source name or index. None → auto-detect monitor.
            prefer_silero: try loading Silero VAD before falling back to energy.
            whisper_model: "tiny", "base", "small", "large-v3" (faster-whisper size).
            stt_backend: "faster-whisper" (RU+EN), "streaming", "auto".
                         NOTE: "moonshine"/"distil-whisper" are English-only.
            language: force STT language ("ru", "en", ...); None = auto-detect (slower).
            output_dir: where to save temp_question.wav (default: cwd).
        """
        self._callback = on_question
        self._device_name = device_name
        self._output_dir = Path(output_dir) if output_dir else Path.cwd()
        self._vad = _make_vad(prefer_silero)
        self._stt = None  # lazy load via stt_fast
        self._stt_backend = stt_backend
        self._whisper_model = whisper_model
        self._language = language

        # state
        self._stream: Optional[object] = None
        self._parec_proc: Optional[subprocess.Popen] = None
        self._parec_thread: Optional[threading.Thread] = None
        self._backend: Optional[str] = None   # "sounddevice" or "parec"
        self._running = False

        # audio ringbuffer for lookback on silence trigger
        self._audio_buffer: collections.deque = collections.deque()
        self._max_buffer_blocks = int(SAMPLE_RATE * SPEECH_TIMEOUT / BLOCK_SIZE)

        # VAD state machine
        self._speech_active = False
        self._silence_start: Optional[float] = None
        self._speech_start: Optional[float] = None

    # ---- public API ----

    def start(self) -> bool:
        """Start capturing. Auto-selects sounddevice or parec backend. Returns True on success."""
        resolved = self._resolve_source()
        if resolved is None:
            sd = _load_sounddevice()
            logger.error("No loopback audio source found")
            logger.error("SoundDevice devices:")
            for d in sd.query_devices():
                if d['max_input_channels'] > 0:
                    logger.error(f"  [{d['index']}] {d['name']}")
            logger.error("PulseAudio monitor sources:")
            AudioCapture._list_pa_sources()
            return False

        backend, source_id = resolved
        self._backend = backend
        self._running = True

        if backend == "sounddevice":
            sd = _load_sounddevice()
            dev_idx = source_id
            logger.info(f"Backend: sounddevice | Device [{dev_idx}]: "
                        f"{sd.query_devices()[dev_idx]['name']}")
            self._stream = sd.InputStream(
                device=dev_idx,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=np.float32,
                blocksize=BLOCK_SIZE,
                callback=self._audio_callback_sd,
            )
            self._stream.start()

        elif backend == "parec":
            source_name = source_id
            logger.info(f"Backend: parec | Source: {source_name}")
            self._start_parec_reader(source_name)

        logger.info("Audio capture started")
        return True

    def stop(self) -> None:
        """Stop capturing and clean up resources."""
        self._running = False

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if self._parec_proc:
            try:
                self._parec_proc.terminate()
                self._parec_proc.wait(timeout=2)
            except Exception:
                try:
                    self._parec_proc.kill()
                except Exception:
                    pass
            self._parec_proc = None

        if self._parec_thread and self._parec_thread.is_alive():
            self._parec_thread.join(timeout=2)
        self._parec_thread = None

        logger.info("Audio capture stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ---- internals ----

    def _resolve_source(self) -> Optional[tuple[str, object]]:
        """Return (backend_type, source_id) or None.

        backend_type: "sounddevice" or "parec"
        source_id:   int device index for sounddevice, str source name for parec
        """
        sd = _load_sounddevice()

        # -- explicit device requested --
        if self._device_name:
            # try sounddevice by name substring or index
            for d in sd.query_devices():
                if self._device_name in d['name'] and d['max_input_channels'] > 0:
                    return ("sounddevice", d['index'])
            try:
                idx = int(self._device_name)
                if 0 <= idx < len(sd.query_devices()):
                    return ("sounddevice", idx)
            except ValueError:
                pass

            # try PulseAudio source name (for parec)
            source_name = self._resolve_pa_monitor(self._device_name)
            if source_name:
                return ("parec", source_name)
            # last resort: pass the name directly to parec
            return ("parec", self._device_name)

        # -- auto-detect: sounddevice first --
        for d in sd.query_devices():
            name = d['name'].lower()
            if d['max_input_channels'] > 0 and 'monitor' in name:
                return ("sounddevice", d['index'])

        # -- auto-detect: PulseAudio monitor via pactl --
        pa_monitor = self._find_pa_monitor()
        if pa_monitor:
            return ("parec", pa_monitor)

        return None

    @staticmethod
    def _find_pa_monitor() -> Optional[str]:
        """Use pactl to find the default sink's monitor source."""
        try:
            import subprocess
            # get default sink name
            sink = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True, text=True, timeout=5
            ).stdout.strip()
            if sink:
                monitor_name = f"{sink}.monitor"
                # verify it exists
                result = subprocess.run(
                    ["pactl", "list", "sources", "short"],
                    capture_output=True, text=True, timeout=5
                )
                if monitor_name in result.stdout:
                    return monitor_name
        except Exception as e:
            logger.debug(f"pactl lookup failed: {e}")
        return None

    @staticmethod
    def _resolve_pa_monitor(hint: str) -> Optional[str]:
        """Try to match hint against pactl sources."""
        try:
            import subprocess
            result = subprocess.run(
                ["pactl", "list", "sources", "short"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                parts = line.split('\t')
                if len(parts) >= 2 and hint in parts[1]:
                    return parts[1]
        except Exception:
            pass
        return None

    @staticmethod
    def _list_pa_sources() -> None:
        """Log PulseAudio monitor sources."""
        try:
            result = subprocess.run(
                ["pactl", "list", "sources", "short"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if '.monitor' in line:
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        logger.error(f"  PA monitor: {parts[1]}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # unified audio callback — called from both backends
    # ------------------------------------------------------------------

    def _process_audio_chunk(self, chunk: np.ndarray, now: float) -> None:
        """VAD + buffer management for a single audio block."""
        speech = self._vad.is_speech(chunk)

        if speech:
            self._audio_buffer.append((now, chunk))
            while len(self._audio_buffer) > self._max_buffer_blocks:
                self._audio_buffer.popleft()

            if not self._speech_active:
                self._speech_active = True
                self._speech_start = now
                logger.debug("VAD: speech start")

            self._silence_start = None

            if self._speech_start and (now - self._speech_start) > SPEECH_TIMEOUT:
                logger.debug("VAD: speech timeout, forcing cut")
                self._save_and_transcribe(now)
                self._speech_active = False
                self._speech_start = None

        else:  # silence
            if self._speech_active:
                if self._silence_start is None:
                    self._silence_start = now
                elif (now - self._silence_start) >= SILENCE_TIMEOUT:
                    logger.debug(f"VAD: silence {now - self._silence_start:.1f}s → trigger")
                    self._save_and_transcribe(now)
                    self._speech_active = False
                    self._speech_start = None
                    self._silence_start = None

    # ------------------------------------------------------------------
    # backend: sounddevice InputStream
    # ------------------------------------------------------------------

    def _audio_callback_sd(self, indata: np.ndarray, frames: int,
                           _time_info, _status) -> None:
        """Called from sounddevice's internal thread for each audio block."""
        if not self._running:
            return
        chunk = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        self._process_audio_chunk(chunk, time.time())

    # ------------------------------------------------------------------
    # backend: parec subprocess (for PulseAudio loopback monitors)
    # ------------------------------------------------------------------

    def _start_parec_reader(self, source_name: str) -> None:
        """Spawn parec and read PCM from stdout in a thread."""
        try:
            self._parec_proc = subprocess.Popen(
                [
                    "parec",
                    "--format=s16le",
                    f"--rate={SAMPLE_RATE}",
                    f"--device={source_name}",
                    "--latency-msec=10",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error("parec not found. Install: sudo pacman -S pulseaudio-utils")
            return

        logger.info(f"parec started on source: {source_name}")

        bytes_per_block = BLOCK_SIZE * 4  # s16le stereo = 4 bytes/frame

        def _read_loop():
            while self._running and self._parec_proc.poll() is None:
                try:
                    raw = self._parec_proc.stdout.read(bytes_per_block)
                    if not raw or len(raw) < bytes_per_block:
                        time.sleep(0.01)
                        continue
                    # s16le stereo → float32 mono [-1, 1]
                    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    # monitor source is stereo interleaved — take left channel
                    if len(pcm) >= BLOCK_SIZE * 2:
                        pcm = pcm[::2]  # left channel only
                    elif len(pcm) > BLOCK_SIZE:
                        pcm = pcm[:BLOCK_SIZE]
                    self._process_audio_chunk(pcm, time.time())
                except Exception as e:
                    logger.debug(f"parec read error: {e}")
                    break

        self._parec_thread = threading.Thread(target=_read_loop, daemon=True, name="parec-reader")
        self._parec_thread.start()

    def _save_and_transcribe(self, trigger_time: float) -> None:
        """Flush audio buffer to WAV and run STT."""
        if len(self._audio_buffer) < 3:  # too short, skip
            logger.debug("Audio buffer too short, skipping")
            self._audio_buffer.clear()
            return

        # collect audio from buffer
        chunks = []
        for _ts, chunk in self._audio_buffer:
            chunks.append(chunk)
        audio = np.concatenate(chunks)
        self._audio_buffer.clear()

        # save WAV
        wav_path = self._output_dir / TEMP_QUESTION_WAV
        self._save_wav(str(wav_path), audio, SAMPLE_RATE)

        # transcribe with auto-selected or specified backend
        try:
            if self._stt is None:
                from stt_fast import create_stt
                self._stt = create_stt(self._stt_backend, model_size=self._whisper_model)
            text = self._stt.transcribe(str(wav_path), language=self._language)
            if text:
                self._callback(text)
        except ImportError as e:
            logger.warning(f"STT dependencies missing: {e}")
            logger.info("Install: pip install faster-whisper transformers torch")
            self._callback("[STT not available — install requirements]")
        except Exception as e:
            logger.error(f"STT error: {type(e).__name__}: {e}")
            self._callback(f"[STT error: {type(e).__name__}]")

    @staticmethod
    def _save_wav(path: str, audio: np.ndarray, sample_rate: int) -> None:
        """Save float32 numpy audio to 16-bit PCM WAV."""
        pcm = (audio * 32767).clip(-32768, 32767).astype(np.int16)
        with wave.open(path, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())
        logger.debug(f"Saved WAV: {path} ({len(pcm)} samples)")


# ---------------------------------------------------------------------------
# convenience: find and print loopback devices
# ---------------------------------------------------------------------------

def list_devices() -> None:
    """Print all input audio devices (sounddevice + PulseAudio monitor sources)."""
    sd = _load_sounddevice()
    print("SoundDevice input devices:")
    for d in sd.query_devices():
        if d['max_input_channels'] > 0:
            print(f"  [{d['index']:2d}] {d['name']}  "
                  f"(ch:{d['max_input_channels']}, "
                  f"sr:{d['default_samplerate']:.0f})")

    print("\nPulseAudio monitor sources (for loopback):")
    try:
        result = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True, text=True, timeout=5
        )
        found = False
        for line in result.stdout.splitlines():
            if '.monitor' in line:
                parts = line.split('\t')
                if len(parts) >= 2:
                    print(f"  {parts[1]}")
                    found = True
        if not found:
            print("  (none found — is speakers/headphones active?)")
    except Exception as e:
        print(f"  (pactl unavailable: {e})")
