"""A cancellable, persistent STT child for the POSIX desktop runtime.

The child owns model loading and native inference. The parent exchanges bounded
JSON frames, never unpickles child data, and can terminate a blocked backend.
"""

import json
import logging
import multiprocessing
import os
import select
import struct
import threading
import time


MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
POLL_SECONDS = 0.05
CLEANUP_SECONDS = 0.3
START_HANDOFF_SECONDS = 0.5


class STTCancelled(Exception):
    """The owning capture session was stopped."""

    def __init__(self):
        super().__init__("STT cancelled")


class STTError(Exception):
    """A safe failure category, never a provider exception or transcript."""

    def __init__(self, message, *, cleanup_incomplete=False):
        super().__init__(message)
        self.cleanup_incomplete = cleanup_incomplete


def _worker_main(connection, backend, model_size):
    """Spawn target: import and cache the backend only after the first job."""
    logging.disable(logging.CRITICAL)
    # The protocol has a separate Connection. Suppress Python and native model
    # diagnostics, which can include the transcript or raw provider exceptions.
    null_descriptor = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null_descriptor, 1)
        os.dup2(null_descriptor, 2)
    finally:
        if null_descriptor > 2:
            os.close(null_descriptor)
    model = None
    try:
        while True:
            try:
                request = json.loads(connection.recv_bytes(MAX_REQUEST_BYTES))
            except (EOFError, OSError, ValueError):
                return
            request_id = request.get("id") if isinstance(request, dict) else None
            response = {"id": request_id, "status": "error"}
            try:
                if type(request_id) is not int or request_id < 1:
                    raise ValueError
                if not isinstance(request.get("wav_path"), str):
                    raise ValueError
                if request.get("language") is not None and not isinstance(request["language"], str):
                    raise ValueError
                if model is None:
                    from stt_fast import create_stt

                    options = {} if backend in ("moonshine", "distil-whisper") else {"model_size": model_size}
                    model = create_stt(backend, **options)
                text = model.transcribe(request["wav_path"], language=request.get("language"))
                if not isinstance(text, str) or len(text) > MAX_RESPONSE_BYTES:
                    raise ValueError
                response = {"id": request_id, "status": "success", "text": text}
                payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise ValueError
            except Exception:
                # Backend exceptions can contain credentials, paths or audio text.
                payload = json.dumps({"id": request_id, "status": "error"}).encode("utf-8")
            try:
                connection.send_bytes(payload)
            except (OSError, EOFError):
                return
    finally:
        connection.close()


class STTProcess:
    """One lazily spawned backend, with one request in flight and terminal close.

    ``stop_event`` belongs to the capture session. It is checked while waiting
    for the serialization lock and while reading/writing each protocol frame.
    ``close`` reports incomplete cleanup if process startup itself fails to
    hand over ownership within a bounded interval; it never reports false reap.
    """

    def __init__(self, backend="auto", model_size="base", stop_event=None,
                 *, _target=None, _context=None, _timeout=120.0):
        self._backend = backend
        self._model_size = model_size
        self._stop_event = stop_event
        self._closed = threading.Event()
        self._context = _context or multiprocessing.get_context("spawn")
        self._target = _target or _worker_main
        self._timeout = _timeout
        self._process = None
        self._connection = None
        self._request_id = 0
        self._request_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._start_done = threading.Event()
        self._start_done.set()

    def _cancelled(self):
        return self._closed.is_set() or (self._stop_event is not None and self._stop_event.is_set())

    def _check_active(self, deadline=None):
        if self._cancelled():
            raise STTCancelled()
        if deadline is not None and time.monotonic() >= deadline:
            raise STTError("STT request timed out")

    def _ensure_process(self):
        with self._state_lock:
            self._check_active()
            if self._process is not None:
                return self._connection
            parent_connection = child_connection = None
            try:
                parent_connection, child_connection = self._context.Pipe(duplex=True)
                process = self._context.Process(
                    target=self._target,
                    args=(child_connection, self._backend, self._model_size),
                    name="ghost-stt",
                )
            except Exception:
                for connection in (parent_connection, child_connection):
                    if connection is not None:
                        connection.close()
                raise STTError("STT startup failed") from None
            self._process = process
            self._connection = parent_connection
            self._start_done.clear()
        try:
            self._check_active()
            process.start()
            os.set_blocking(parent_connection.fileno(), False)
        except STTCancelled:
            raise
        except Exception:
            raise STTError("STT startup failed") from None
        finally:
            child_connection.close()
            self._start_done.set()
        self._check_active()
        return parent_connection

    def _wait_ready(self, connection, deadline, *, writable=False):
        self._check_active(deadline)
        descriptor = connection.fileno()
        timeout = min(POLL_SECONDS, max(0, deadline - time.monotonic()))
        readable, writeable, _ = select.select(
            [] if writable else [descriptor], [descriptor] if writable else [], [], timeout,
        )
        return bool(writeable if writable else readable)

    def _send(self, connection, payload, deadline):
        frame = struct.pack("!i", len(payload)) + payload
        offset = 0
        while offset < len(frame):
            if not self._wait_ready(connection, deadline, writable=True):
                continue
            with self._io_lock:
                self._check_active(deadline)
                try:
                    written = os.write(connection.fileno(), frame[offset:])
                except BlockingIOError:
                    continue
            if not written:
                raise STTError("STT child connection closed")
            offset += written

    def _read_exact(self, connection, count, deadline):
        data = bytearray()
        while len(data) < count:
            if not self._wait_ready(connection, deadline):
                continue
            with self._io_lock:
                self._check_active(deadline)
                try:
                    chunk = os.read(connection.fileno(), min(count - len(data), MAX_RESPONSE_BYTES))
                except BlockingIOError:
                    continue
            if not chunk:
                raise STTError("STT child connection closed")
            data.extend(chunk)
        return bytes(data)

    def _receive(self, connection, request_id, deadline):
        # Connection.poll()+recv_bytes() can block on a partial frame. Read the
        # POSIX Connection framing incrementally so cancellation stays observable.
        length = struct.unpack("!i", self._read_exact(connection, 4, deadline))[0]
        if length <= 0 or length > MAX_RESPONSE_BYTES:
            raise STTError("STT response exceeded size limit")
        try:
            response = json.loads(self._read_exact(connection, length, deadline))
        except (ValueError, UnicodeError, RecursionError):
            raise STTError("STT protocol error") from None
        if not isinstance(response, dict) or type(response.get("id")) is not int:
            raise STTError("STT protocol error")
        if response["id"] != request_id:
            raise STTError("STT response does not match request")
        if response.get("status") == "error":
            raise STTError("STT backend failed")
        if response.get("status") != "success" or not isinstance(response.get("text"), str):
            raise STTError("STT protocol error")
        self._check_active(deadline)
        return response["text"]

    def transcribe(self, wav_path: str, language=None) -> str:
        while not self._request_lock.acquire(timeout=POLL_SECONDS):
            self._check_active()
        try:
            self._check_active()
            if not isinstance(wav_path, str) or (language is not None and not isinstance(language, str)):
                raise STTError("STT request is invalid")
            self._request_id += 1
            request = {"id": self._request_id, "wav_path": wav_path, "language": language}
            if len(wav_path) > MAX_REQUEST_BYTES or (language is not None and len(language) > 128):
                raise STTError("STT request exceeded size limit")
            payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
            if len(payload) > MAX_REQUEST_BYTES:
                raise STTError("STT request exceeded size limit")
            deadline = time.monotonic() + self._timeout
            connection = self._ensure_process()
            self._send(connection, payload, deadline)
            return self._receive(connection, self._request_id, deadline)
        except STTCancelled:
            self.close()
            raise
        except STTError:
            self._discard_worker()
            if self._cancelled():
                raise STTCancelled() from None
            raise
        except Exception:
            self._discard_worker()
            if self._cancelled():
                raise STTCancelled() from None
            raise STTError("STT communication failed") from None
        finally:
            self._request_lock.release()

    def _discard_worker(self):
        if not self._start_done.wait(START_HANDOFF_SECONDS):
            raise STTError("STT cleanup incomplete", cleanup_incomplete=True)
        if not self._cleanup_lock.acquire(timeout=START_HANDOFF_SECONDS + 2 * CLEANUP_SECONDS):
            raise STTError("STT cleanup incomplete", cleanup_incomplete=True)
        try:
            with self._state_lock:
                process, connection = self._process, self._connection
            if process is None:
                return
            try:
                if process.pid is not None:
                    if process.is_alive():
                        process.terminate()
                    process.join(CLEANUP_SECONDS)
                    if process.is_alive():
                        process.kill()
                        process.join(CLEANUP_SECONDS)
                    if process.is_alive() or process.exitcode is None:
                        raise STTError("STT cleanup incomplete", cleanup_incomplete=True)
                with self._io_lock:
                    if connection is not None:
                        connection.close()
                process.close()
            except STTError:
                raise
            except Exception:
                raise STTError("STT cleanup incomplete", cleanup_incomplete=True) from None
            with self._state_lock:
                self._process = None
                self._connection = None
        finally:
            self._cleanup_lock.release()

    def close(self):
        """Cancel and reap the owned child, or explicitly report incomplete cleanup."""
        self._closed.set()
        self._discard_worker()
