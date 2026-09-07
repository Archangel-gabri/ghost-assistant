"""Check child-worker backend configuration without loading an inference model."""

import sys
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import stt_fast  # noqa: E402


@pytest.mark.parametrize("name,backend_type", [
    ("moonshine", stt_fast.MoonshineSTT),
    ("distil-whisper", stt_fast.DistilWhisperSTT),
])
def test_shared_capture_model_size_does_not_break_fixed_model_backend(monkeypatch, name, backend_type):
    monkeypatch.setattr(backend_type, "is_available", lambda self: True)

    backend = stt_fast.create_stt(name, model_size="base")

    assert isinstance(backend, backend_type)
    assert backend._model is None
