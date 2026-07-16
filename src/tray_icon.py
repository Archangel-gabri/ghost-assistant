"""
tray_icon.py — system tray icon for Ghost — voice + screen assistant.

Shows icon in system tray with context menu:
  - Show/Hide control panel
  - Start/Stop listening
  - Exit

Uses QSystemTrayIcon — works on X11, XWayland, and GNOME Wayland
(via StatusNotifier protocol through libdbusmenu-qt).
"""

import logging
from pathlib import Path

from PySide6.QtGui import QIcon, QAction
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication

logger = logging.getLogger("tray")

ICON_PATH = Path(__file__).resolve().parent / "ghost.png"


class TrayIcon:
    """System tray icon with context menu."""

    def __init__(self, app: QApplication, on_show: callable = None,
                 on_quit: callable = None):
        self._app = app
        self._on_show = on_show
        self._on_quit = on_quit
        self._tray: QSystemTrayIcon = None
        self._menu: QMenu = None

    def setup(self) -> bool:
        """Create and show tray icon. Returns False if not supported."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray not available on this desktop")
            return False

        icon = QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QIcon()
        self._tray = QSystemTrayIcon(icon, self._app)
        self._tray.setToolTip("Ghost — voice + screen assistant")

        # context menu
        self._menu = QMenu()
        self._menu.setStyleSheet("""
            QMenu {
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #333;
                padding: 4px;
            }
            QMenu::item { padding: 6px 24px; }
            QMenu::item:selected { background-color: #3a3a3a; }
        """)

        show_action = QAction("Show Control Panel")
        show_action.triggered.connect(self._on_show or (lambda: None))
        self._menu.addAction(show_action)

        self._menu.addSeparator()

        quit_action = QAction("Exit")
        quit_action.triggered.connect(self._on_quit or self._app.quit)
        self._menu.addAction(quit_action)

        self._tray.setContextMenu(self._menu)
        self._tray.show()
        logger.info("Tray icon created")
        return True

    def set_tooltip(self, text: str) -> None:
        if self._tray:
            self._tray.setToolTip(f"Ghost — {text}")

    def dispose(self) -> None:
        if self._tray:
            self._tray.hide()
            self._tray = None
