#!/usr/bin/env bash
# Ghost — desktop installer. Registers Ghost as a real app: icon in the
# application menu + a shortcut on the Desktop, launched like any native app.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/src/main.py"
CFG="$HERE/src/config-fast.yaml"
ICON_SRC="$HERE/assets/ghost-512.png"

echo "▸ Installing Ghost…"

# 1) dependencies (system python; use a venv if you prefer isolation)
python3 -m pip install --quiet --user -r "$HERE/requirements.txt" 2>/dev/null \
  || python3 -m pip install --quiet --break-system-packages -r "$HERE/requirements.txt"

# 2) icon into the hicolor theme
ICON_DIR="$HOME/.local/share/icons/hicolor/512x512/apps"
mkdir -p "$ICON_DIR"
cp "$ICON_SRC" "$ICON_DIR/ghost.png"

# 3) application menu entry
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$APP_DIR"
DESKTOP="$APP_DIR/ghost.desktop"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Ghost
GenericName=Voice Assistant
Comment=Voice + screen AI assistant
Exec=python3 "$APP" --config "$CFG"
Icon=ghost
Terminal=false
Categories=Utility;Office;
StartupNotify=true
StartupWMClass=Ghost
EOF
chmod +x "$DESKTOP"

# 4) shortcut on the Desktop
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Рабочий стол")"
if [ -d "$DESKTOP_DIR" ]; then
  cp "$DESKTOP" "$DESKTOP_DIR/Ghost.desktop"
  chmod +x "$DESKTOP_DIR/Ghost.desktop"
  gio set "$DESKTOP_DIR/Ghost.desktop" metadata::trusted true 2>/dev/null || true
fi

# 5) refresh caches
update-desktop-database "$APP_DIR" 2>/dev/null || true
gtk-update-icon-cache -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "✓ Ghost installed — find it in the app menu or double-click the Desktop icon."
