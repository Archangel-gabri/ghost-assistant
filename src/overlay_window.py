"""
overlay_window.py — Ghost overlay in a Glass OS / iOS aesthetic.

Light frosted-glass bar pinned to the bottom of the screen:
controls + answer + history. Tool and model selectors are data-driven from
tools.yaml (see utils.load_tools).
"""

import logging
import os
import time
from pathlib import Path
from collections import deque
from typing import Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import (
    QPainter, QColor, QPen, QLinearGradient, QBrush, QTextCursor,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QApplication,
    QPushButton, QLabel, QTextEdit, QComboBox, QFileDialog,
)

from utils import load_tools
from icons import icon

# В Qt6 перечисление переехало внутрь QTextCursor.MoveOperation. Держим ссылку
# одной константой: обращение по старому пути роняло приложение на первом ответе.
_CURSOR_END = QTextCursor.MoveOperation.End

logger = logging.getLogger("overlay")
PROJECT_ROOT = Path(__file__).resolve().parent

# ── geometry ──
WIN_HEIGHT = 190
WIN_WIDTH_RATIO = 0.66
WIN_MIN_WIDTH = 360
WIN_MIN_HEIGHT = 130
WIN_PADDING_BOTTOM = 16
RADIUS = 24
EDGE_DETECT_MARGIN = 8

# ── Glass OS light palette ──
GLASS_TOP = QColor(252, 252, 254, 208)     # frosted white (top of gradient)
GLASS_BOTTOM = QColor(238, 239, 244, 196)  # subtle grey (bottom of gradient)
GLASS_BORDER = QColor(255, 255, 255, 180)  # bright hairline
GLASS_SHADOW = QColor(0, 0, 0, 28)         # faint outer hairline

ACCENT = "#0a84ff"      # iOS blue
GREEN = "#34c759"       # iOS green
RED = "#ff3b30"         # iOS red
TEXT = "#1d1d1f"        # near-black
TEXT_2 = "#6e6e73"      # iOS secondary grey
CTRL_BG = "rgba(255,255,255,0.55)"
CTRL_BG_HOVER = "rgba(255,255,255,0.85)"
CTRL_BORDER = "rgba(0,0,0,0.08)"
FONT = ('font-family: "SF Pro Display", "SF Pro Text", -apple-system, '
        '"Inter", "Segoe UI", sans-serif;')


class OverlayWindow(QWidget):
    """Frosted-glass bottom bar."""

    def __init__(self, config: dict = None, parent=None):
        super().__init__(parent)
        self._cfg = config or {}
        self._worker = None
        self._history = deque(maxlen=6)
        self._tray = None
        self._quit_close = False
        self._has_content = False
        self._got_chunks = False
        self._tools = load_tools()

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

    # ── positioning ──

    def _position(self):
        s = QApplication.primaryScreen()
        if not s:
            return
        g = s.availableGeometry()
        w = int(g.width() * WIN_WIDTH_RATIO)
        h = WIN_HEIGHT
        x = g.x() + (g.width() - w) // 2
        y = g.y() + g.height() - h - WIN_PADDING_BOTTOM
        self.setMinimumSize(WIN_MIN_WIDTH, WIN_MIN_HEIGHT)
        self.resize(w, h)
        self.move(x, y)
        logger.info(f"Overlay: {w}x{h}+{x}+{y}")

    # ── UI ──

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 12)
        outer.setSpacing(8)

        # ── top row ──
        top = QHBoxLayout()
        top.setSpacing(8)

        wd = self._cfg.get("backend", {}).get("workdir", "")
        self._folder_btn = QPushButton("  " + (self._short_path(wd) or "Проект"))
        self._folder_btn.setIcon(icon("folder", TEXT_2, 17))
        self._folder_btn.setIconSize(QSize(17, 17))
        self._folder_btn.setStyleSheet(self._chip_style())
        self._folder_btn.setToolTip(wd or "Выбрать папку проекта")
        self._folder_btn.clicked.connect(self._pick_folder)
        top.addWidget(self._folder_btn)

        top.addStretch()

        # tool selector (data-driven)
        self._tool = QComboBox()
        self._tool.addItems([t["label"] for t in self._tools])
        self._tool.setStyleSheet(self._combo_style())
        self._tool.currentIndexChanged.connect(self._on_tool_changed)
        top.addWidget(self._tool)

        # model selector
        self._model = QComboBox()
        self._model.setMinimumWidth(118)
        self._model.setStyleSheet(self._combo_style())
        top.addWidget(self._model)
        self._refresh_models()

        # screenshot toggle (pill)
        self._scr = QPushButton()
        self._scr.setCheckable(True)
        self._scr.setChecked(True)
        self._scr.setIcon(icon("camera", ACCENT, 18))
        self._scr.setIconSize(QSize(18, 18))
        self._scr.setToolTip("Захват экрана для вопросов по коду")
        self._scr.setStyleSheet(self._toggle_style())
        self._scr.toggled.connect(
            lambda on: self._scr.setIcon(icon("camera", ACCENT if on else "#8e8e93", 18)))
        top.addWidget(self._scr)

        # settings
        self._settings = QPushButton()
        self._settings.setIcon(icon("gear", TEXT_2, 18))
        self._settings.setIconSize(QSize(18, 18))
        self._settings.setToolTip("Изменить инструменты (tools.yaml)")
        self._settings.setStyleSheet(self._icon_style())
        self._settings.clicked.connect(self._open_settings)
        top.addWidget(self._settings)

        # start / stop
        self._start = QPushButton()
        self._start.setIcon(icon("play", "#ffffff", 16))
        self._start.setIconSize(QSize(16, 16))
        self._start.setToolTip("Начать слушать")
        self._start.setStyleSheet(self._primary_style(ACCENT))
        self._start.clicked.connect(self._on_start)
        top.addWidget(self._start)

        self._stop = QPushButton()
        self._stop.setIcon(icon("stop", "#ffffff", 15))
        self._stop.setIconSize(QSize(15, 15))
        self._stop.setEnabled(False)
        self._stop.setToolTip("Остановить")
        self._stop.setStyleSheet(self._primary_style(RED))
        self._stop.clicked.connect(self._on_stop)
        top.addWidget(self._stop)

        outer.addLayout(top)

        # ── answer area ──
        self._answer_area = QTextEdit()
        self._answer_area.setReadOnly(True)
        self._answer_area.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(255,255,255,0.45);
                border: 1px solid rgba(255,255,255,0.6);
                border-radius: 16px; padding: 10px 12px;
                font-size: 14px; color: {TEXT}; {FONT}
            }}
            QScrollBar:vertical {{ background: transparent; width: 6px; margin: 4px; }}
            QScrollBar::handle:vertical {{
                background: rgba(0,0,0,0.18); border-radius: 3px; min-height: 24px;
            }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
        """)
        self._answer_area.setHtml(self._welcome_html())
        outer.addWidget(self._answer_area, 1)

        # ── status row ──
        self._status_lbl = QLabel(self._stt_badge())
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setStyleSheet(f"color: {TEXT_2}; font-size: 11px; {FONT}")
        outer.addWidget(self._status_lbl)

    # ── style helpers ──

    def _chip_style(self):
        return f"""
            QPushButton {{
                background: {CTRL_BG}; border: 1px solid {CTRL_BORDER};
                border-radius: 13px; padding: 5px 12px; color: {TEXT};
                font-size: 12px; font-weight: 500; {FONT}
            }}
            QPushButton:hover {{ background: {CTRL_BG_HOVER}; }}
        """

    def _combo_style(self):
        return f"""
            QComboBox {{
                background: {CTRL_BG}; border: 1px solid {CTRL_BORDER};
                border-radius: 12px; padding: 5px 10px; color: {TEXT};
                font-size: 12px; font-weight: 500; {FONT}
            }}
            QComboBox:hover {{ background: {CTRL_BG_HOVER}; }}
            QComboBox::drop-down {{ border: none; width: 16px; }}
            QComboBox QAbstractItemView {{
                background: #fbfbfd; color: {TEXT}; border: 1px solid {CTRL_BORDER};
                border-radius: 12px; padding: 4px; outline: none;
                selection-background-color: {ACCENT}; selection-color: white;
            }}
        """

    def _toggle_style(self):
        return f"""
            QPushButton {{
                background: {CTRL_BG}; border: 1px solid {CTRL_BORDER};
                border-radius: 13px; padding: 5px 9px; font-size: 13px; {FONT}
            }}
            QPushButton:hover {{ background: {CTRL_BG_HOVER}; }}
            QPushButton:checked {{
                background: rgba(10,132,255,0.18);
                border: 1px solid rgba(10,132,255,0.5);
            }}
        """

    def _icon_style(self):
        return f"""
            QPushButton {{
                background: {CTRL_BG}; border: 1px solid {CTRL_BORDER};
                border-radius: 13px; padding: 5px 9px; color: {TEXT_2};
                font-size: 14px; {FONT}
            }}
            QPushButton:hover {{ background: {CTRL_BG_HOVER}; }}
        """

    def _primary_style(self, color):
        return f"""
            QPushButton {{
                background: {color}; border: none; border-radius: 13px;
                padding: 5px 14px; color: white; font-size: 13px;
                font-weight: 600; {FONT}
            }}
            QPushButton:hover {{ background: {color}; }}
            QPushButton:disabled {{ background: rgba(0,0,0,0.12); color: rgba(0,0,0,0.3); }}
        """

    # ── content helpers ──

    @staticmethod
    def _short_path(p: str) -> str:
        if not p:
            return ""
        p = p.rstrip("/")
        return "…/" + "/".join(p.split("/")[-2:]) if p.count("/") > 2 else p

    def _welcome_html(self):
        return (f'<p style="color:{ACCENT};text-align:center;font-size:14px;">'
                f'✦ Готово — нажми ▶, чтобы слушать</p>')

    def _stt_badge(self):
        stt = "Groq ⚡" if os.environ.get("GROQ_API_KEY") else "локально"
        return f"STT: {stt}"

    # ── data-driven tool/model ──

    def _current_tool(self) -> dict:
        idx = self._tool.currentIndex()
        return self._tools[idx] if 0 <= idx < len(self._tools) else self._tools[0]

    def _refresh_models(self):
        self._model.clear()
        self._model.addItems([str(m) for m in self._current_tool().get("models", [])])

    def _on_tool_changed(self, _idx):
        self._refresh_models()

    # ── folder / settings ──

    def _pick_folder(self):
        start = self._cfg.get("backend", {}).get("workdir") or os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, "Папка проекта", start)
        if d:
            self._folder_btn.setText("📁  " + self._short_path(d))
            self._folder_btn.setToolTip(d)
            self._cfg.setdefault("backend", {})["workdir"] = d

    def _open_settings(self):
        import subprocess
        path = PROJECT_ROOT / "tools.yaml"
        try:
            subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            logger.warning(f"cannot open tools.yaml: {e}")

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
        if self._worker:
            self._worker.stop()
        if self._tray:
            self._tray.dispose()
        self.close()

    def closeEvent(self, e):
        if self._quit_close:
            e.accept()
            return
        self.hide()
        e.ignore()

    # ── start / stop ──

    def _on_start(self):
        from worker import PipelineWorker
        self._start.setEnabled(False)

        tool = self._current_tool()
        model = self._model.currentText()
        be = self._cfg.setdefault("backend", {})
        be["provider"] = tool.get("provider", "claude")
        be["model"] = model
        be["command"] = tool.get("command")
        self._cfg.setdefault("screenshot", {})["enabled"] = self._scr.isChecked()

        self._status_lbl.setText(f"○ Запуск {tool['label']} · {model}…")

        self._worker = PipelineWorker(self._cfg)
        self._worker.question_detected.connect(self._on_question)
        self._worker.answer_chunk.connect(self._on_answer_chunk)
        self._worker.answer_ready.connect(self._on_answer)
        self._worker.status_changed.connect(self._on_status)
        self._worker.error_occurred.connect(lambda e: self._status_lbl.setText(f"● {e}"))
        self._worker.finished.connect(lambda: (
            self._start.setEnabled(True), self._stop.setEnabled(False)
        ))
        self._worker.start()
        self._stop.setEnabled(True)

    def _on_stop(self):
        if self._worker:
            self._worker.stop()
            self._worker = None
        self._start.setEnabled(True)
        self._stop.setEnabled(False)
        self._status_lbl.setText(self._stt_badge())
        self._answer_area.setHtml(self._welcome_html())
        self._has_content = False

    # ── pipeline signal handlers ──

    def _on_question(self, text):
        self._status_lbl.setText("◐ Печатаю…")
        self._append_answer(f"Q: {text}", color=TEXT_2)
        self._begin_answer_block()           # empty paragraph for live streaming
        self._got_chunks = False

    def _begin_answer_block(self):
        if not self._has_content:
            self._answer_area.clear()
            self._has_content = True
        cur = self._answer_area.textCursor()
        cur.movePosition(_CURSOR_END)
        if self._answer_area.toPlainText().strip():
            cur.insertBlock()
        self._answer_area.setTextCursor(cur)

    def _on_answer_chunk(self, text):
        """A streamed token — append inline so the answer types out live."""
        self._got_chunks = True
        cur = self._answer_area.textCursor()
        cur.movePosition(_CURSOR_END)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(TEXT))
        cur.insertText(text, fmt)
        self._answer_area.setTextCursor(cur)
        self._answer_area.ensureCursorVisible()

    def _on_answer(self, text):
        if not self._got_chunks:             # non-streaming provider → show full text
            self._append_answer(text, color=TEXT)
        self._status_lbl.setText(f"● Ответ · {time.strftime('%H:%M')}")
        self._history.append(text)
        self.raise_()

    def _append_answer(self, text, color=TEXT):
        if not self._has_content:            # drop the welcome placeholder
            self._answer_area.clear()
            self._has_content = True
        cur = self._answer_area.textCursor()
        cur.movePosition(_CURSOR_END)
        if self._answer_area.toPlainText().strip():
            cur.insertBlock()                # force a new paragraph (real line break)
        cur.insertHtml(f'<span style="color:{color};line-height:1.4;">{text}</span>')
        self._answer_area.setTextCursor(cur)
        self._answer_area.ensureCursorVisible()

    def _on_status(self, s):
        if s == "listening":
            self._status_lbl.setText("● Слушаю…")

    # ── frosted-glass background ──

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)

        grad = QLinearGradient(0, 0, 0, rect.height())
        grad.setColorAt(0.0, GLASS_TOP)
        grad.setColorAt(1.0, GLASS_BOTTOM)
        p.setBrush(QBrush(grad))
        p.setPen(QPen(GLASS_SHADOW, 1))
        p.drawRoundedRect(rect, RADIUS, RADIUS)

        # bright top highlight for the "glass" edge
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(GLASS_BORDER, 1))
        p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), RADIUS - 1, RADIUS - 1)

    # ── backward compat ──

    def show_answer(self, text):
        self._on_answer(text)

    def force_hide(self):
        self._quit_close = True
        if self._tray:
            self._tray.dispose()
        self.close()

    # ── drag & resize ──
    _drag_pos = None
    _resize_edge = None

    def _get_edge(self, pos) -> Optional[str]:
        r = self.rect()
        x, y, w, h = pos.x(), pos.y(), r.width(), r.height()
        edge = ""
        if y < EDGE_DETECT_MARGIN:
            edge += "n"
        if y > h - EDGE_DETECT_MARGIN:
            edge += "s"
        if x < EDGE_DETECT_MARGIN:
            edge += "w"
        if x > w - EDGE_DETECT_MARGIN:
            edge += "e"
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
            g = self.geometry()
            edge = self._resize_edge
            if 'n' in edge:
                g.setTop(g.top() + delta.y())
            if 's' in edge:
                g.setBottom(g.bottom() + delta.y())
            if 'w' in edge:
                g.setLeft(g.left() + delta.x())
            if 'e' in edge:
                g.setRight(g.right() + delta.x())
            if g.width() < WIN_MIN_WIDTH:
                g.setWidth(WIN_MIN_WIDTH)
            if g.height() < WIN_MIN_HEIGHT:
                g.setHeight(WIN_MIN_HEIGHT)
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
