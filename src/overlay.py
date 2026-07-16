"""
overlay.py — always-visible status bar + answer popup for Ghost — voice + screen assistant.

Shows a small translucent bar at screen bottom that displays:
  - Idle:     "🟢 Listening for speaker questions..."
  - Answer:   Claude's response (1-2 sentences), fades back to idle after 15s

Features:
  - Borderless, semi-transparent, always-on-top
  - Click-through (mouse passes through to apps below)
  - Always visible — user sees it in their peripheral vision
  - Force X11 backend for reliability on Wayland

Uses tkinter (stdlib) — zero extra dependencies.
"""

import logging
import os
import threading
import time
import tkinter as tk
from typing import Optional

logger = logging.getLogger("overlay")

# Force X11 backend through XWayland — more reliable for overlays
if "WAYLAND_DISPLAY" in os.environ and "DISPLAY" in os.environ:
    os.environ.setdefault("GDK_BACKEND", "x11")

FONT_FAMILY = "monospace"
FONT_SIZE = 16
FONT_SIZE_SMALL = 13
WINDOW_OPACITY = 0.80
WINDOW_HEIGHT = 54
WINDOW_WIDTH_RATIO = 0.92
ANSWER_FADE_TIMEOUT = 15.0
BG_COLOR = "#0d0d0d"
FG_COLOR = "#e8e8e8"
ACCENT_COLOR = "#64b4ff"
DIM_COLOR = "#777777"
PADDING = 16

IDLE_TEXT = "🟢 Listening for speaker questions..."
IDLE_SUB = ""


class OverlayWindow:
    """
    Always-visible translucent bar at screen bottom.
    Switches between idle state and answer display.
    Thread-safe: show()/hide() may be called from any thread.
    """

    def __init__(self, fade_timeout: float = ANSWER_FADE_TIMEOUT,
                 opacity: float = WINDOW_OPACITY):
        self._fade_timeout = fade_timeout
        self._opacity = opacity
        self._root: Optional[tk.Tk] = None
        self._label: Optional[tk.Label] = None
        self._sub: Optional[tk.Label] = None
        self._fade_timer: Optional[threading.Timer] = None
        self._ready = threading.Event()
        self._running = False

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch overlay window. Non-blocking — returns after window is ready."""
        if self._running:
            return
        self._running = True
        t = threading.Thread(target=self._tk_main, daemon=True, name="overlay-tk")
        t.start()
        if not self._ready.wait(timeout=5):
            logger.error("Overlay: tk thread did not signal ready within 5s")
            return
        # show idle state
        self._root.after(300, self._show_idle)

    def stop(self) -> None:
        """Destroy overlay window."""
        self._running = False
        self._cancel_fade()
        if self._root:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass

    def show_answer(self, text: str) -> None:
        """Display an answer from Claude. Auto-fades back to idle."""
        if not self._root:
            return
        self._cancel_fade()
        self._root.after(0, self._set_text, text, f"via Claude • {time.strftime('%H:%M')} • auto-hide in {int(self._fade_timeout)}s")
        self._fade_timer = threading.Timer(self._fade_timeout, self._show_idle)
        self._fade_timer.daemon = True
        self._fade_timer.start()

    # ------------------------------------------------------------------
    # tkinter internals
    # ------------------------------------------------------------------

    def _tk_main(self) -> None:
        """Tkinter main loop — runs in dedicated thread."""
        self._root = tk.Tk()
        self._root.title("Ghost Assistant")
        self._root.configure(bg=BG_COLOR)

        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        ww = int(sw * WINDOW_WIDTH_RATIO)
        wh = WINDOW_HEIGHT
        x = (sw - ww) // 2
        y = sh - wh - 40
        self._root.geometry(f"{ww}x{wh}+{x}+{y}")
        self._root.resizable(False, False)

        # borderless, always-on-top
        self._root.overrideredirect(True)
        try:
            self._root.attributes("-topmost", True)
        except tk.TclError:
            pass
        self._root.attributes("-alpha", self._opacity)

        # hide from taskbar
        try:
            self._root.attributes("-type", "splash")
        except tk.TclError:
            pass

        # keep on top (fight with compositor)
        def _keep_on_top(count=20):
            if count <= 0 or not self._running:
                return
            try:
                self._root.lift()
                self._root.attributes("-topmost", True)
            except Exception:
                pass
            self._root.after(800, _keep_on_top, count - 1)
        self._root.after(200, _keep_on_top)

        # main label
        self._label = tk.Label(
            self._root,
            text="",
            font=(FONT_FAMILY, FONT_SIZE),
            fg=FG_COLOR,
            bg=BG_COLOR,
            wraplength=ww - PADDING * 2,
            justify="center",
        )
        self._label.place(relx=0.5, rely=0.35, anchor="center")

        # subtitle label
        self._sub = tk.Label(
            self._root,
            text="",
            font=(FONT_FAMILY, FONT_SIZE_SMALL),
            fg=DIM_COLOR,
            bg=BG_COLOR,
            wraplength=ww - PADDING * 2,
            justify="center",
        )
        self._sub.place(relx=0.5, rely=0.78, anchor="center")

        # click-through
        for ev in ("<Button-1>", "<Button-2>", "<Button-3>", "<Motion>"):
            self._root.bind(ev, lambda e: "break")

        logger.info(f"Overlay: {ww}x{wh}+{x}+{y} on {sw}x{sh}")
        self._ready.set()
        self._root.mainloop()

    def _show_idle(self) -> None:
        """Switch to idle listening state."""
        self._set_text(IDLE_TEXT, IDLE_SUB)
        self._fade_timer = None

    def _set_text(self, text: str, sub_text: str = "") -> None:
        if not self._label or not self._sub:
            return
        self._label.configure(text=text)
        self._sub.configure(text=sub_text)
        try:
            self._root.deiconify()
            self._root.lift()
            self._root.attributes("-topmost", True)
        except Exception:
            pass

    def _cancel_fade(self) -> None:
        if self._fade_timer:
            self._fade_timer.cancel()
            self._fade_timer = None
