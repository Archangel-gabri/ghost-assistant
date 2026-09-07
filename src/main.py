#!/usr/bin/env python3
"""
main.py — Ghost — voice + screen assistant.
Qt6 desktop application with control panel, system tray, and overlay.

Usage:
    python main.py              # launch GUI
    python main.py --cli        # terminal-only mode (legacy)
    python main.py --once "Q"   # single-shot CLI
"""

import argparse
from contextlib import contextmanager
import logging
import os
import sys
from pathlib import Path

import yaml
from PySide6.QtWidgets import QApplication

PROJECT_ROOT = Path(__file__).resolve().parent

# Force X11 backend on Wayland for reliable overlay behavior
if "WAYLAND_DISPLAY" in os.environ and "DISPLAY" in os.environ:
    os.environ.setdefault("GDK_BACKEND", "x11")
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)s: %(message)s",
)
log = logging.getLogger("ghost")


def load_config(path: Path = None) -> dict:
    if path is None:
        path = PROJECT_ROOT / "config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# GUI mode
# ---------------------------------------------------------------------------

def run_gui(cfg: dict) -> int:
    from overlay_window import OverlayWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Ghost")
    app.setOrganizationName("ghost")
    app.setQuitOnLastWindowClosed(False)

    icon_path = PROJECT_ROOT / "ghost.png"
    if icon_path.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon_path)))

    window = OverlayWindow(cfg)
    window.show()

    return app.exec()


# ---------------------------------------------------------------------------
# CLI fallback
# ---------------------------------------------------------------------------

@contextmanager
def _cli_resources(cfg: dict):
    """Check LLM configuration before optional capture; always release resources."""
    from config_helper import ConfigBuilder

    builder = ConfigBuilder(cfg)
    session = builder.create_llm_session()
    grabber = None
    try:
        if not session.start() and session.mode == "configuration-error":
            raise ValueError(session.startup_error or "LLM configuration error")
        if cfg.get("screenshot", {}).get("enabled", True):
            grabber = builder.create_screen_grabber()
        yield session, grabber
    finally:
        try:
            if grabber is not None:
                grabber.close()
        finally:
            session.close()


def run_cli(cfg: dict) -> None:
    """Legacy terminal mode (kept for debugging)."""
    with _cli_resources(cfg) as (session, grabber):
        log.info(f"LLM: {session.provider}/{session.model} ({session.mode})")

        print("═" * 48)
        print("  Ghost — terminal mode")
        print(f"  LLM: {session.provider}/{session.model} ({session.mode})")
        print("  Type question, Enter to send, Ctrl+C to exit")
        print("═" * 48)

        try:
            while True:
                question = input("❯❯ ").strip()
                if not question:
                    continue
                if question.lower() in ("q", "quit", "exit", "выход"):
                    break
                ss_path = str(grabber.grab_force()) if grabber is not None else None
                answer = session.ask(question, screenshot_path=ss_path)
                print(f"\n  {answer}\n")
        except KeyboardInterrupt:
            print()


def run_once_cli(cfg: dict, question: str) -> None:
    with _cli_resources(cfg) as (session, grabber):
        ss_path = str(grabber.grab_force()) if grabber is not None else None
        answer = session.ask(question, screenshot_path=ss_path)
        print(answer)


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ghost — voice + screen assistant")
    parser.add_argument("--cli", action="store_true", help="Terminal-only mode")
    parser.add_argument("--once", type=str, metavar="QUESTION", help="Single-shot CLI")
    parser.add_argument("--config", type=str, default=None, help="Path to config.yaml")
    args = parser.parse_args()

    cfg = load_config(Path(args.config) if args.config else None)

    if args.once:
        run_once_cli(cfg, args.once)
    elif args.cli:
        run_cli(cfg)
    else:
        sys.exit(run_gui(cfg))


if __name__ == "__main__":
    main()
