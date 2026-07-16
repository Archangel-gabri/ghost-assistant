"""
overlay_window.py — Unified Ghost window at screen bottom.
Single glass-morphism bar: controls + answer + history.
"""

import logging
import time
import os
from pathlib import Path
from collections import deque
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QColor, QPen, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QApplication,
    QPushButton, QLabel, QTextEdit, QComboBox, QCheckBox,
    QFileDialog, QFrame
)
from utils import MODELS_BY_PROVIDER, Provider

logger = logging.getLogger("overlay")
PROJECT_ROOT = Path(__file__).resolve().parent

# Window geometry
WIN_HEIGHT = 180
WIN_WIDTH_RATIO = 0.72
WIN_MIN_WIDTH = 300
WIN_MIN_HEIGHT = 120
WIN_PADDING_BOTTOM = 12

# Styling
RADIUS = 16
BG = QColor(20, 20, 26, 220)
BORDER = QColor(255, 255, 255, 18)
ACCENT = "#64b4ff"
FONT = "font-family: Inter, sans-serif;"

# Colors
BTN_BG_PRIMARY = "#1a5c2a"
BTN_BG_PRIMARY_HOVER = "#2d8e3e"
BTN_BG_DANGER = "#5c1a1a"
BTN_BG_DANGER_HOVER = "#8e2d2d"
BTN_BG_SECONDARY = "#333"
BTN_BG_SECONDARY_HOVER = "#444"
TEXT_MUTED = "#888"
TEXT_NORMAL = "#ddd"

# Edge detection for resize
EDGE_DETECT_MARGIN = 8


class OverlayWindow(QWidget):
    """Unified bottom bar with glass effect."""

    def __init__(self, config: dict = None, parent=None):
        super().__init__(parent)
        self._cfg = config or {}
        self._worker = None
        self._history = deque(maxlen=6)
        self._tray = None
        self._quit_close = False

        self.setWindowTitle("Ghost")
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._build_ui()
        self._position()
        self._setup_tray()

    def _position(self):
        s = QApplication.primaryScreen()
        if s:
            g = s.availableGeometry()
            w = int(g.width() * WIN_WIDTH_RATIO)
            h = WIN_HEIGHT
            x = g.x() + (g.width() - w)//2
            y = g.y() + g.height() - h - WIN_PADDING_BOTTOM
            self.setMinimumSize(WIN_MIN_WIDTH, WIN_MIN_HEIGHT)
            self.resize(w, h)
            self.move(x, y)
            logger.info(f"Overlay: {w}x{h}+{x}+{y}")

    # ── UI ──

    def _build_ui(self):
        # Outer layout with padding so content doesn't hit rounded corners
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 10)
        outer.setSpacing(3)

        # ── top row: project + controls ──
        top = QHBoxLayout()
        top.setSpacing(6)

        # project path
        wd = self._cfg.get("backend", {}).get("workdir", "")
        self._folder_lbl = QLabel(wd)
        self._folder_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px; {FONT}")
        self._folder_lbl.setMaximumWidth(180)
        top.addWidget(self._folder_lbl)

        fb = QPushButton("📁")
        fb.setMaximumWidth(30)
        fb.setStyleSheet(self._btn_style(BTN_BG_SECONDARY, BTN_BG_SECONDARY_HOVER))
        fb.clicked.connect(self._pick_folder)
        top.addWidget(fb)

        top.addStretch()

        # provider
        self._prov = QComboBox()
        self._prov.addItems(["claude", "codex"])
        self._prov.setCurrentText(self._cfg.get("backend", {}).get("provider", "claude"))
        self._prov.currentTextChanged.connect(self._on_provider)
        self._prov.setStyleSheet(self._combo_style())
        top.addWidget(self._prov)

        # model
        self._model = QComboBox(minimumWidth=110)
        self._model.setMinimumWidth(110)
        self._model.setStyleSheet(self._combo_style())
        top.addWidget(self._model)
        self._update_models("claude")

        # screenshot checkbox
        self._scr = QCheckBox("📸")
        self._scr.setChecked(True)
        self._scr.setToolTip("Screen capture for code demos")
        self._scr.setStyleSheet(f"color: #999; font-size: 10px; {FONT}")
        top.addWidget(self._scr)

        # start / stop
        self._start = QPushButton("▶")
        self._start.setMaximumWidth(36)
        self._start.setStyleSheet(self._btn_style(BTN_BG_PRIMARY, BTN_BG_PRIMARY_HOVER))
        self._start.clicked.connect(self._on_start)
        top.addWidget(self._start)

        self._stop = QPushButton("■")
        self._stop.setMaximumWidth(36)
        self._stop.setEnabled(False)
        self._stop.setStyleSheet(self._btn_style(BTN_BG_DANGER, BTN_BG_DANGER_HOVER))
        self._stop.clicked.connect(self._on_stop)
        top.addWidget(self._stop)

        outer.addLayout(top)

        # ── answer history (scrollable) ──
        self._answer_area = QTextEdit()
        self._answer_area.setReadOnly(True)
        self._answer_area.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(0,0,0,0.2); border: 1px solid rgba(255,255,255,0.05);
                border-radius: 8px; padding: 6px; font-size: 13px; color: #ddd; {FONT}
            }}
            QScrollBar:vertical {{
                background: transparent; width: 6px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255,255,255,0.1); border-radius: 3px; min-height: 20px;
            }}
        """)
        self._answer_area.setHtml('<p style="color:#64b4ff;text-align:center;">🟢 Ready — press ▶ to start</p>')
        outer.addWidget(self._answer_area, 1)  # stretch

        # ── status ──
        self._status_lbl = QLabel("")
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setStyleSheet(f"color: {ACCENT}; font-size: 11px; {FONT}")
        outer.addWidget(self._status_lbl)

    # ── styles ──

    def _btn_style(self, bg: str, bg_hover: str) -> str:
        """Generate button stylesheet."""
        return f"""
            QPushButton {{
                background: {bg}; border: 1px solid rgba(255,255,255,0.08);
                border-radius: 7px; padding: 4px 8px; color: {TEXT_NORMAL}; font-size: 13px;
                font-weight: bold; {FONT}
            }}
            QPushButton:hover {{ background: {bg_hover}; }}
        """

    def _combo_style(self) -> str:
        """Generate combobox stylesheet."""
        return f"""
            QComboBox {{
                background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);
                border-radius: 6px; padding: 3px 6px; color: {TEXT_NORMAL}; font-size: 11px; {FONT}
            }}
            QComboBox:hover {{ border-color: rgba(255,255,255,0.18); }}
            QComboBox::drop-down {{ border: none; width: 14px; }}
            QComboBox QAbstractItemView {{
                background: #1c1c22; color: {TEXT_NORMAL}; border-radius: 6px;
                selection-background-color: #4488cc; outline: none;
            }}
        """

    # ── helpers ──

    def _pick_folder(self):
        start = self._folder_lbl.text() or os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, "Project folder", start)
        if d:
            self._folder_lbl.setText(d)
            self._cfg.setdefault("backend", {})["workdir"] = d

    def _update_models(self, provider_name: str) -> None:
        """Update model dropdown based on selected provider."""
        self._model.clear()
        provider = Provider(provider_name) if provider_name in ["claude", "codex"] else Provider.CLAUDE
        models = MODELS_BY_PROVIDER[provider]
        self._model.addItems([m.value for m in models])

    def _on_provider(self, provider_name: str) -> None:
        self._update_models(provider_name)

    # ── tray ──

    def _setup_tray(self):
        from tray_icon import TrayIcon
        self._tray = TrayIcon(QApplication.instance(), self._show, self._quit)
        self._tray.setup()

    def _show(self):
        self.show()
        self.raise_()

    def _quit(self):
        self._quit_close = True
        if self._worker: self._worker.stop()
        if self._tray: self._tray.dispose()
        self.close()

    def closeEvent(self, e):
        if self._quit_close: e.accept(); return
        self.hide(); e.ignore()

    # ── start / stop ──

    def _on_start(self):
        from worker import PipelineWorker
        self._start.setEnabled(False)

        p = self._prov.currentText()
        m = self._model.currentText()
        d = self._folder_lbl.text()
        self._cfg.setdefault("backend", {})["provider"] = p
        self._cfg["backend"]["model"] = m
        self._cfg["backend"]["workdir"] = d
        self._cfg.setdefault("screenshot", {})["enabled"] = self._scr.isChecked()

        self._status_lbl.setText(f"⚫ Starting {p}/{m}...")

        self._worker = PipelineWorker(self._cfg)
        self._worker.question_detected.connect(self._on_question)
        self._worker.answer_chunk.connect(self._on_answer_chunk)
        self._worker.answer_ready.connect(self._on_answer)
        self._worker.status_changed.connect(self._on_status)
        self._worker.error_occurred.connect(lambda e: self._status_lbl.setText(f"🔴 {e}"))
        self._worker.finished.connect(lambda: (
            self._start.setEnabled(True),
            self._stop.setEnabled(False)
        ))
        self._worker.start()
        self._stop.setEnabled(True)

    def _on_stop(self):
        if self._worker: self._worker.stop(); self._worker = None
        self._start.setEnabled(True); self._stop.setEnabled(False)
        self._status_lbl.setText("⚫ Stopped")
        self._append_answer("🟢 Ready — press ▶ to start", color="#64b4ff")

    # ── pipeline ──

    def _on_question(self, text):
        self._status_lbl.setText("🟡 Processing...")
        self._append_answer(f'Q: {text}', color="#888")

    def _on_answer(self, text):
        self._append_answer(text)
        self._status_lbl.setText(f"🟢 Answered • {time.strftime('%H:%M')}")
        self._history.append(text)
        self.raise_()

    def _on_answer_chunk(self, text):
        """Streaming chunk — append to current answer."""
        cursor = self._answer_area.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertHtml(f'{text}<br>')
        self._answer_area.setTextCursor(cursor)
        self._answer_area.ensureCursorVisible()

    def _append_answer(self, text, color="#ddd"):
        cursor = self._answer_area.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertHtml(f'<p style="color:{color};margin:2px 0;">{text}</p>')
        self._answer_area.setTextCursor(cursor)
        self._answer_area.ensureCursorVisible()

    def _on_status(self, s):
        if s == "listening":
            self._status_lbl.setText("🟢 Listening...")

    # ── paint glass background ──

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(BG)
        p.setPen(QPen(BORDER, 1))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), RADIUS, RADIUS)

    # ── backward compat ──

    def show_answer(self, text):
        self._on_answer(text)

    def force_hide(self):
        self._quit_close = True
        if self._tray: self._tray.dispose()
        self.close()

    # ── drag & resize window ──
    _drag_pos = None
    _resize_edge = None

    def _get_edge(self, pos) -> Optional[str]:
        """Detect which edge of window is being hovered for resize."""
        r = self.rect()
        x, y, w, h = pos.x(), pos.y(), r.width(), r.height()
        edge = ""
        if y < EDGE_DETECT_MARGIN: edge += "n"
        if y > h - EDGE_DETECT_MARGIN: edge += "s"
        if x < EDGE_DETECT_MARGIN: edge += "w"
        if x > w - EDGE_DETECT_MARGIN: edge += "e"
        return edge or None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._resize_edge = self._get_edge(e.pos())
            if self._resize_edge:
                self._drag_pos = e.globalPosition().toPoint()
            else:
                self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and self._resize_edge:
            new_pos = e.globalPosition().toPoint()
            delta = new_pos - self._drag_pos
            g = self.geometry(); edge = self._resize_edge
            if 'n' in edge: g.setTop(g.top() + delta.y())
            if 's' in edge: g.setBottom(g.bottom() + delta.y())
            if 'w' in edge: g.setLeft(g.left() + delta.x())
            if 'e' in edge: g.setRight(g.right() + delta.x())
            if g.width() < WIN_MIN_WIDTH: g.setWidth(WIN_MIN_WIDTH)
            if g.height() < WIN_MIN_HEIGHT: g.setHeight(WIN_MIN_HEIGHT)
            self.setGeometry(g)
            self._drag_pos = new_pos
        elif self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        else:
            edge = self._get_edge(e.pos())
            cursors = {
                'n': Qt.SizeVerCursor, 's': Qt.SizeVerCursor,
                'e': Qt.SizeHorCursor, 'w': Qt.SizeHorCursor,
                'ne': Qt.SizeBDiagCursor, 'sw': Qt.SizeBDiagCursor,
                'nw': Qt.SizeFDiagCursor, 'se': Qt.SizeFDiagCursor,
            }
            self.setCursor(cursors.get(edge, Qt.ArrowCursor))

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        self._resize_edge = None
