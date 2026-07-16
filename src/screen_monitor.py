"""
screen_monitor.py — screen capture with change detection.
Grabs primary monitor, saves PNG, skips identical frames via perceptual hash.
"""

import hashlib
import time
from pathlib import Path
from typing import Optional

import imagehash
import mss
from PIL import Image


class ScreenGrabber:
    """Captures screenshots and detects changes via average-hash comparison."""

    THUMB_SIZE = (128, 128)

    def __init__(self, output_path: str = "temp_screen.png", monitor_idx: int = 0,
                 hash_threshold: int = 5):
        self.output_path = Path(output_path)
        self.monitor_idx = monitor_idx
        self.hash_threshold = hash_threshold
        self._last_hash: Optional[imagehash.ImageHash] = None
        self._sct = mss.mss()

    def _capture_image(self) -> Image.Image:
        """Capture raw image from monitor."""
        monitor = self._sct.monitors[self.monitor_idx]
        raw = self._sct.grab(monitor)
        return Image.frombytes("RGB", (raw.width, raw.height), raw.bgra, "raw", "BGRX")

    def _compute_hash(self, img: Image.Image) -> imagehash.ImageHash:
        """Compute perceptual hash for change detection."""
        thumb = img.copy()
        thumb.thumbnail(self.THUMB_SIZE, Image.LANCZOS)
        return imagehash.average_hash(thumb)

    def grab(self) -> Optional[Path]:
        """Capture screen. Returns path if changed, None if identical to last frame."""
        img = self._capture_image()
        current_hash = self._compute_hash(img)

        if self._last_hash is not None:
            diff = abs(current_hash - self._last_hash)
            if diff <= self.hash_threshold:
                return None  # no meaningful change

        self._last_hash = current_hash
        img.save(self.output_path, "PNG", optimize=True)
        return self.output_path

    def grab_force(self) -> Path:
        """Grab and save regardless of change detection."""
        img = self._capture_image()
        img.save(self.output_path, "PNG", optimize=True)
        self._last_hash = self._compute_hash(img)
        return self.output_path

    def close(self) -> None:
        self._sct.close()
