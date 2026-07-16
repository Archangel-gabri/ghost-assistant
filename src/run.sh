#!/bin/bash
# Ghost — voice + screen assistant — desktop launcher
# Starts the Qt6 GUI with control panel, system tray, and overlay.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# activate venv
source .venv/bin/activate

# force X11/XWayland backend for reliable overlay behavior on Wayland
export GDK_BACKEND=x11
export QT_QPA_PLATFORM=xcb

# run GUI
exec python main.py "$@"
