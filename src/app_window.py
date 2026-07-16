"""
app_window.py — Ghost — voice + screen assistant control panel.
Glass-morphism design, positioned at top-center of screen.
"""

import logging
import time
import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QIcon, QColor, QPalette, QPainter, QBrush, QPen
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QApplication,
    QPushButton, QLabel, QTextEdit, QComboBox, QCheckBox,
    QFileDialog, QGraphicsOpacityEffect, QFrame
)

from overlay_window import OverlayWindow
from tray_icon import TrayIcon
from worker import PipelineWorker

logger = logging.getLogger("app")
PROJECT_ROOT = Path(__file__).resolve().parent

# ── glass-morphism constants ──
WIN_W = 380
WIN_H = 540
GLASS_BG = "rgba(28, 28, 32, 0.85)"   # dark frosted glass
GLASS_BORDER = "rgba(255, 255, 255, 0.08)"
ACCENT = "#64b4ff"
ACCENT_DIM = "#4488cc"
TEXT_PRIMARY = "#f0f0f0"
TEXT_SECONDARY = "#999"
DARK_BG = "#1a1a1e"
BTN_BG = "rgba(255, 255, 255, 0.06)"
BTN_HOVER = "rgba(255, 255, 255, 0.12)"
BTN_START = "rgba(50, 180, 100, 0.25)"
BTN_START_HOVER = "rgba(50, 200, 110, 0.35)"
BTN_STOP = "rgba(200, 50, 50, 0.25)"
BTN_STOP_HOVER = "rgba(220, 60, 60, 0.35)"
RADIUS = 16

STYLE = f"""
/* ── window ── */
QMainWindow {{
    background: transparent;
}}
#centralWidget {{
    background: {GLASS_BG};
    border: 1px solid {GLASS_BORDER};
    border-radius: {RADIUS}px;
}}

/* ── labels ── */
QLabel {{
    color: {TEXT_PRIMARY};
    background: transparent;
    font-family: "SF Pro Display", "Inter", "Segoe UI", sans-serif;
}}
#titleLabel {{
    font-size: 17px;
    font-weight: 600;
    letter-spacing: 0.5px;
    color: {TEXT_PRIMARY};
}}
#statusLabel {{
    font-size: 13px;
    color: {ACCENT};
    padding: 2px 0;
}}
#sectionLabel {{
    font-size: 11px;
    font-weight: 600;
    color: {TEXT_SECONDARY};
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-top: 4px;
}}

/* ── buttons ── */
QPushButton {{
    background: {BTN_BG};
    border: 1px solid {GLASS_BORDER};
    border-radius: 10px;
    padding: 10px 24px;
    font-size: 13px;
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}
QPushButton:hover {{
    background: {BTN_HOVER};
    border-color: rgba(255,255,255,0.15);
}}
#folderBtn {{
    font-size: 14px;
    padding: 4px 8px;
    font-weight: bold;
}}
#startBtn {{
    background: {BTN_START};
    border-color: rgba(50,200,110,0.3);
    font-size: 14px;
}}
#startBtn:hover {{
    background: {BTN_START_HOVER};
}}
#stopBtn {{
    background: {BTN_STOP};
    border-color: rgba(220,60,60,0.3);
    font-size: 14px;
}}
#stopBtn:hover {{
    background: {BTN_STOP_HOVER};
}}

/* ── combos ── */
QComboBox {{
    background: {BTN_BG};
    border: 1px solid {GLASS_BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    color: {TEXT_PRIMARY};
    font-size: 12px;
    min-width: 100px;
}}
QComboBox:hover {{ border-color: rgba(255,255,255,0.2); }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: #1e1e24;
    color: {TEXT_PRIMARY};
    border: 1px solid {GLASS_BORDER};
    border-radius: 8px;
    selection-background-color: {ACCENT_DIM};
    outline: none;
}}

/* ── text edit ── */
QTextEdit {{
    background: rgba(0,0,0,0.3);
    border: 1px solid {GLASS_BORDER};
    border-radius: 10px;
    padding: 10px;
    font-size: 11px;
    color: #bbb;
    font-family: "JetBrains Mono", "Fira Code", monospace;
}}

/* ── checkboxes ── */
QCheckBox {{
    color: #aaa;
    font-size: 12px;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {GLASS_BORDER};
    border-radius: 4px;
    background: {BTN_BG};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT_DIM};
    border-color: {ACCENT};
}}

/* ── separators ── */
.separator {{
    background: {GLASS_BORDER};
    max-height: 1px;
    margin: 4px 0;
}}
"""


class AppWindow(QMainWindow):
    """Glass-morphism control panel for Ghost — voice + screen assistant."""

    def __init__(self, config: dict):
        super().__init__()
        self._cfg = config
        self._worker: PipelineWorker = None
        self._overlay: OverlayWindow = None
        self._tray: TrayIcon = None

        self._setup_ui()
        self._setup_overlay()
        self._setup_tray()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setWindowTitle("Ghost")
        self.setFixedSize(WIN_W, WIN_H)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        icon_path = PROJECT_ROOT / "ghost.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # Center at top of screen
        screen = self.screen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - WIN_W) // 2
            y = geo.y() + 30  # top margin
            self.move(x, y)

        # Central widget with glass background (painted via stylesheet)
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(8)

        # ── title ──
        title = QLabel("Ghost")
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # ── status ──
        self._status_label = QLabel("⚫ Idle")
        self._status_label.setObjectName("statusLabel")
        self._status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._status_label)

        # ── separator ──
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        # ── project folder ──
        lbl = QLabel("PROJECT")
        lbl.setObjectName("sectionLabel")
        layout.addWidget(lbl)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)
        workdir = self._cfg.get("backend", {}).get("workdir", "")
        self._folder_label = QLabel(workdir)
        self._folder_label.setWordWrap(True)
        self._folder_label.setStyleSheet("font-size:10px; color:#888; padding:2px 0;")
        folder_row.addWidget(self._folder_label, 1)
        fb = QPushButton("...")
        fb.setObjectName("folderBtn")
        fb.setMaximumWidth(36)
        fb.clicked.connect(self._pick_folder)
        folder_row.addWidget(fb)
        layout.addLayout(folder_row)

        # ── provider row ──
        lbl = QLabel("PROVIDER")
        lbl.setObjectName("sectionLabel")
        layout.addWidget(lbl)

        prov_row = QHBoxLayout()
        prov_row.setSpacing(8)
        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["claude", "codex"])
        self._provider_combo.setCurrentText(
            self._cfg.get("backend", {}).get("provider", "claude"))
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        prov_row.addWidget(self._provider_combo)

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setMinimumWidth(130)
        prov_row.addWidget(self._model_combo)
        layout.addLayout(prov_row)

        # ── model row ──
        self._update_model_list("claude")
        current_model = self._cfg.get("backend", {}).get("model", "haiku")
        idx = self._model_combo.findText(current_model)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        else:
            self._model_combo.setCurrentText(current_model)

        # ── options ──
        lbl = QLabel("OPTIONS")
        lbl.setObjectName("sectionLabel")
        layout.addWidget(lbl)

        self._screenshot_cb = QCheckBox("Capture screen (for code demos)")
        self._screenshot_cb.setChecked(True)
        self._screenshot_cb.setToolTip("When checked, sends screenshots to Claude for context")
        layout.addWidget(self._screenshot_cb)

        # ── buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._start_btn = QPushButton("● Start")
        self._start_btn.setObjectName("startBtn")
        self._start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("■ Stop")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setEnabled(False)
        btn_row.addWidget(self._stop_btn)

        layout.addLayout(btn_row)

        # ── log ──
        lbl = QLabel("LOG")
        lbl.setObjectName("sectionLabel")
        layout.addWidget(lbl)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(140)
        layout.addWidget(self._log)

    # ── helper: model list ──
    def _update_model_list(self, provider: str) -> None:
        self._model_combo.clear()
        if provider == "claude":
            self._model_combo.addItems(["haiku", "sonnet", "fable", "opus"])
            self._model_combo.setCurrentText("haiku")
        elif provider == "codex":
            self._model_combo.addItems(["gpt-5.6-luna", "gpt-5.6-sol", "o3", "gpt-5.5"])
            self._model_combo.setCurrentText("gpt-5.6-luna")

    def _on_provider_changed(self, provider: str) -> None:
        self._update_model_list(provider)

    # ── folder picker ──
    def _pick_folder(self) -> None:
        start = self._folder_label.text() or os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, "Select project folder", start)
        if d:
            self._folder_label.setText(d)
            self._cfg.setdefault("backend", {})["workdir"] = d
            self._add_log(f"Project: {d}")

    # ── log helper ──
    def _add_log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log.append(f"[{ts}] {msg}")
        lines = self._log.toPlainText().split("\n")
        if len(lines) > 200:
            self._log.setPlainText("\n".join(lines[-150:]))

    # ------------------------------------------------------------------
    # overlay + tray
    # ------------------------------------------------------------------

    def _setup_overlay(self) -> None:
        self._overlay = OverlayWindow()
        self._overlay.show()

    def _setup_tray(self) -> None:
        self._tray = TrayIcon(
            app=QApplication.instance(),
            on_show=self._show_window,
            on_quit=self._do_quit,
        )
        self._tray.setup()

    def _app(self):
        from PySide6.QtWidgets import QApplication
        return QApplication.instance()

    # ------------------------------------------------------------------
    # start / stop
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        self._start_btn.setEnabled(False)
        self._status_label.setText("Starting...")
        self._status_label.setStyleSheet(f"font-size: 13px; color: #ffaa44;")

        provider = self._provider_combo.currentText()
        model = self._model_combo.currentText()
        use_screenshot = self._screenshot_cb.isChecked()
        workdir = self._folder_label.text()

        self._cfg.setdefault("backend", {})["provider"] = provider
        self._cfg["backend"]["model"] = model
        self._cfg["backend"]["workdir"] = workdir
        self._cfg["screenshot"]["enabled"] = use_screenshot

        self._add_log(f"Start: {provider}/{model} | project: {workdir} | screen={'on' if use_screenshot else 'off'}")

        self._worker = PipelineWorker(self._cfg)
        self._worker.question_detected.connect(self._on_question)
        self._worker.answer_ready.connect(self._on_answer)
        self._worker.status_changed.connect(self._on_status)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

        self._stop_btn.setEnabled(True)

    def _on_stop(self) -> None:
        self._status_label.setText("Stopping...")
        self._status_label.setStyleSheet("font-size: 13px; color: #ff6644;")
        if self._worker:
            self._worker.stop()
            self._worker = None
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status_label.setText("⚫ Idle")
        self._status_label.setStyleSheet(f"font-size: 13px; color: {ACCENT};")
        self._add_log("Stopped")

    # ------------------------------------------------------------------
    # signal handlers
    # ------------------------------------------------------------------

    @Slot(str)
    def _on_question(self, text: str) -> None:
        self._add_log(f"Q: {text}")

    @Slot(str)
    def _on_answer(self, text: str) -> None:
        short = text[:100] + ("..." if len(text) > 100 else "")
        self._add_log(f"A: {short}")
        self._overlay.show_answer(text)

    @Slot(str)
    def _on_status(self, status: str) -> None:
        icons = {"listening": "🟢", "processing": "🟡", "idle": "⚫", "error": "🔴"}
        self._status_label.setText(f"{icons.get(status, '⚫')} {status}")
        color = {"listening": "#4c4", "processing": "#ffaa44", "idle": ACCENT}.get(status, "#888")
        self._status_label.setStyleSheet(f"font-size: 13px; color: {color};")

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self._add_log(f"ERR: {msg}")

    @Slot()
    def _on_worker_finished(self) -> None:
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # window events
    # ------------------------------------------------------------------

    def _show_window(self) -> None:
        self.show(); self.raise_(); self.activateWindow()

    def _do_quit(self) -> None:
        self._quit_on_close = True
        if self._worker: self._worker.stop()
        if self._overlay: self._overlay.force_hide()
        if self._tray: self._tray.dispose()
        self.close()

    _quit_on_close = False
    def closeEvent(self, event) -> None:
        if getattr(self, '_quit_on_close', False):
            event.accept(); return
        self.hide(); event.ignore()

    # ── drag window ──
    _drag_pos = None
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
    def mouseReleaseEvent(self, event):
        self._drag_pos = None
