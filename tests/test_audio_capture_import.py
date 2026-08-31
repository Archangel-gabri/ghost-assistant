"""Import-safety regressions for the optional native audio backend."""

import os
import subprocess
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parent.parent / "src"


def test_audio_capture_import_does_not_load_sounddevice():
    """Importing configuration code must not initialize native PortAudio."""
    script = """
import sys
import types


class BlockSounddeviceImport:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "sounddevice":
            raise AssertionError("audio_capture imported sounddevice eagerly")
        return None


numpy = types.ModuleType("numpy")
numpy.ndarray = object
sys.modules["numpy"] = numpy
sys.meta_path.insert(0, BlockSounddeviceImport())
import audio_capture  # noqa: F401

assert "sounddevice" not in sys.modules
"""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(SRC)

    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=SRC.parent,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )

    assert result.returncode == 0, result.stderr
