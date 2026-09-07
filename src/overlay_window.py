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

from PySide6.QtCore import QObject, Qt, QSize, Slot
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
from cli_contract import CLIContractError, codex_isolation_args

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


class _WorkerRelay(QObject):
    """Receive queued signals on the GUI thread for exactly one worker run."""

    def __init__(self, window, worker, generation):
        super().__init__(window)
        self._window = window
        self._worker = worker
        self._generation = generation

    def _dispatch(self, handler, text):
        window = self._window
        if window._worker is self._worker and window._generation == self._generation:
            handler(text)

    @Slot(str)
    def question(self, text):
        self._dispatch(self._window._on_question, text)

    @Slot(str)
    def chunk(self, text):
        self._dispatch(self._window._on_answer_chunk, text)

    @Slot(str)
    def answer(self, text):
        self._dispatch(self._window._on_answer, text)

    @Slot(str)
    def status(self, text):
        self._dispatch(self._window._on_status, text)

    @Slot(str)
    def error(self, text):
        self._dispatch(self._window._on_error, text)

    @Slot()
    def finished(self):
        # Stop invalidates the generation before this final cleanup notification.
        self._window._on_worker_finished(self._worker)


class OverlayWindow(QWidget):
    """Frosted-glass bottom bar."""

    def __init__(self, config: dict = None, parent=None):
        super().__init__(parent)
        self._cfg = config or {}
        self._worker = None
        self._worker_relay = None
        self._generation = 0
        self._stopping = False
        self._cleanup_failed = False
        self._history = deque(maxlen=6)
        self._tray = None
        self._quit_close = False
        self._has_content = False
        self._got_chunks = False
        self._answer_start = None
        self._answer_pending = False
        self._tools = [dict(tool, models=list(tool.get("models", []))) for tool in load_tools()]
        self._initial_tool_index = self._restore_backend_selection()

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
        self._tool.setCurrentIndex(self._initial_tool_index)
        self._tool.setStyleSheet(self._combo_style())
        self._tool.currentIndexChanged.connect(self._on_tool_changed)
        top.addWidget(self._tool)

        # model selector
        self._model = QComboBox()
        self._model.setMinimumWidth(118)
        self._model.setStyleSheet(self._combo_style())
        top.addWidget(self._model)
        self._refresh_models()
        configured_model = self._cfg.get("backend", {}).get("model")
        if isinstance(configured_model, str) and configured_model:
            self._model.setCurrentText(configured_model)

        # screenshot toggle (pill)
        self._scr = QPushButton()
        self._scr.setCheckable(True)
        self._scr.setChecked(self._cfg.get("screenshot", {}).get("enabled", True))
        self._scr.setIcon(icon("camera", ACCENT if self._scr.isChecked() else "#8e8e93", 18))
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

    def _restore_backend_selection(self) -> int:
        """Retain an explicit backend even if it is absent from tools.yaml."""
        backend = self._cfg.get("backend", {})
        provider = backend.get("provider", "claude")
        model = backend.get("model")
        for index, tool in enumerate(self._tools):
            if tool.get("provider") != provider:
                continue
            if provider == "codex" and tool.get("codex_provider_spec") != backend.get("codex_provider_spec"):
                continue
            if provider == "generic" and tool.get("command") != backend.get("command"):
                continue
            if provider == "codex":
                # Python equality considers 1 == True. Keep the original
                # metadata so strict preflight still rejects an invalid type.
                tool["codex_provider_spec"] = backend.get("codex_provider_spec")
            if isinstance(model, str) and model and model not in tool["models"]:
                tool["models"].append(model)
            return index
        # A transient entry preserves the configured metadata in memory; it
        # does not migrate config or make a different provider the fallback.
        self._tools.append({
            "id": "__configured_backend__",
            "label": "Codex (configured)" if provider == "codex" else "Configured backend",
            "provider": provider,
            "models": [model if isinstance(model, str) and model else ("sonnet" if provider == "claude" else "")],
            "command": backend.get("command"),
            "codex_provider_spec": backend.get("codex_provider_spec"),
        })
        return len(self._tools) - 1

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
        self._on_stop()
        if self._worker is None:
            self._finish_close()

    def _finish_close(self):
        if self._tray:
            self._tray.dispose()
            self._tray = None
        self.close()

    def closeEvent(self, e):
        if self._quit_close:
            if self._worker is None:
                e.accept()
            else:
                e.ignore()
            return
        self.hide()
        e.ignore()

    # ── start / stop ──

    def _on_start(self):
        if self._worker is not None or self._quit_close:
            return
        tool = self._current_tool()
        model = self._model.currentText()
        be = self._cfg.setdefault("backend", {})
        if tool.get("provider") == "codex":
            try:
                codex_isolation_args(Path(be.get("workdir") or PROJECT_ROOT), tool.get("codex_provider_spec"))
            except CLIContractError as error:
                self._on_error(f"{error.code}: {error}")
                return
        from worker import PipelineWorker
        self._start.setEnabled(False)

        be["provider"] = tool.get("provider", "claude")
        be["model"] = model
        be["command"] = tool.get("command")
        be["codex_provider_spec"] = tool.get("codex_provider_spec")
        self._cfg.setdefault("screenshot", {})["enabled"] = self._scr.isChecked()

        self._status_lbl.setText(f"○ Запуск {tool['label']} · {model}…")

        self._generation += 1
        self._stopping = False
        self._worker = PipelineWorker(self._cfg)
        relay = _WorkerRelay(self, self._worker, self._generation)
        self._worker_relay = relay
        self._worker.question_detected.connect(relay.question, Qt.QueuedConnection)
        self._worker.answer_chunk.connect(relay.chunk, Qt.QueuedConnection)
        self._worker.answer_ready.connect(relay.answer, Qt.QueuedConnection)
        self._worker.status_changed.connect(relay.status, Qt.QueuedConnection)
        self._worker.error_occurred.connect(relay.error, Qt.QueuedConnection)
        self._worker.finished.connect(relay.finished, Qt.QueuedConnection)
        self._worker.start()
        self._stop.setEnabled(True)

    def _on_stop(self):
        if self._cleanup_failed:
            return
        if self._worker is not None and not self._stopping:
            self._generation += 1
            self._stopping = True
            self._worker.stop()
        self._start.setEnabled(self._worker is None and not self._quit_close)
        self._stop.setEnabled(False)
        self._status_lbl.setText(self._stt_badge())
        self._answer_area.setHtml(self._welcome_html())
        self._has_content = False
        self._got_chunks = False
        self._answer_start = None
        self._answer_pending = False

    def _on_worker_finished(self, worker):
        if self._worker is not worker:
            return
        if getattr(worker, "cleanup_complete", False) is not True:
            self._cleanup_failed = True
            self._generation += 1
            self._stopping = True
            self._start.setEnabled(False)
            self._stop.setEnabled(False)
            self._on_error("Очистка ресурсов не завершена; перезапуск и выход заблокированы.")
            return
        self._cleanup_failed = False
        self._worker = None
        self._stopping = False
        if self._worker_relay is not None:
            self._worker_relay.deleteLater()
            self._worker_relay = None
        self._start.setEnabled(not self._quit_close)
        self._stop.setEnabled(False)
        if self._quit_close:
            self._finish_close()

    # ── pipeline signal handlers ──

    def _on_question(self, text):
        self._status_lbl.setText("◐ Печатаю…")
        self._append_answer(f"Q: {text}", color=TEXT_2)
        self._begin_answer_block()           # empty paragraph for live streaming
        self._got_chunks = False
        self._answer_pending = True

    def _begin_answer_block(self):
        if not self._has_content:
            self._answer_area.clear()
            self._has_content = True
        cur = self._answer_area.textCursor()
        cur.movePosition(_CURSOR_END)
        if self._answer_area.toPlainText().strip():
            cur.insertBlock()
        self._answer_start = cur.position()
        self._answer_area.setTextCursor(cur)

    def _on_answer_chunk(self, text):
        """A streamed token — append inline so the answer types out live."""
        if not self._answer_pending:
            return
        self._got_chunks = True
        cur = self._answer_area.textCursor()
        cur.movePosition(_CURSOR_END)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(TEXT))
        cur.insertText(text, fmt)
        self._answer_area.setTextCursor(cur)
        self._answer_area.ensureCursorVisible()

    def _on_answer(self, text):
        if not self._answer_pending:
            return
        self._replace_answer(text, TEXT)
        self._answer_pending = False
        self._status_lbl.setText(f"● Ответ · {time.strftime('%H:%M')}")
        self._history.append(text)
        self.raise_()

    def _on_error(self, text):
        if self._answer_pending:
            self._replace_answer(text, RED)
        else:
            self._append_answer(text, color=RED)
        self._answer_pending = False
        self._status_lbl.setText(f"● {text}")

    def _replace_answer(self, text, color):
        """Replace the provisional answer with its authoritative final outcome."""
        cur = self._answer_area.textCursor()
        cur.setPosition(self._answer_start)
        cur.movePosition(_CURSOR_END, QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cur.insertText(text, fmt)
        self._answer_area.setTextCursor(cur)
        self._answer_area.ensureCursorVisible()
        self._answer_start = None

    def _append_answer(self, text, color=TEXT):
        if not self._has_content:            # drop the welcome placeholder
            self._answer_area.clear()
            self._has_content = True
        cur = self._answer_area.textCursor()
        cur.movePosition(_CURSOR_END)
        if self._answer_area.toPlainText().strip():
            cur.insertBlock()                # force a new paragraph (real line break)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cur.insertText(text, fmt)
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
        if not self._answer_pending:
            self._begin_answer_block()
            self._answer_pending = True
        self._on_answer(text)

    def force_hide(self):
        self._quit()

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
