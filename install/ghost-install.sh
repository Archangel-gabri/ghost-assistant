#!/bin/bash
# ghost-install.sh — Install Ghost as desktop application

set -e

echo "╔════════════════════════════════════════════════════════════╗"
echo "║     Ghost — voice + screen assistant — Installation               ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "📦 Installing Ghost..."
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Install Python 3.10+"
    exit 1
fi

echo "✓ Python $(python3 --version | cut -d' ' -f2)"
echo ""

# Install package
echo "📥 Installing package dependencies..."
pip install --break-system-packages -e . > /dev/null 2>&1 || pip install -e . || {
    echo "⚠️  Regular install failed, trying with requirements.txt..."
    pip install --break-system-packages -r requirements.txt || pip install -r requirements.txt
}

echo "✓ Package installed"
echo ""

# Desktop entry
echo "🎯 Installing desktop entry..."
DESKTOP_FILE="$PROJECT_DIR/install/ghost.desktop"

# System wide (requires sudo)
if sudo cp "$DESKTOP_FILE" /usr/share/applications/ghost.desktop 2>/dev/null; then
    echo "✓ Installed to /usr/share/applications (system-wide)"
    sudo update-desktop-database /usr/share/applications 2>/dev/null || true
else
    # User only
    mkdir -p ~/.local/share/applications
    cp "$DESKTOP_FILE" ~/.local/share/applications/ghost.desktop
    echo "✓ Installed to ~/.local/share/applications (user only)"
    update-desktop-database ~/.local/share/applications 2>/dev/null || true
fi

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                  ✅ Installation Complete                  ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "🎬 Launch Ghost:"
echo ""
echo "  1. Search 'Ghost' in application menu"
echo "  2. OR run: ghost --config config-fast.yaml"
echo "  3. OR run: ghost --cli (terminal mode)"
echo ""
echo "⚡ First run:"
echo "  • Install STT backend: bash install/ghost-stt-install.sh"
echo "  • Use Fast config (Moonshine): faster, 95% accurate"
echo "  • Configure microphone in system settings"
echo ""
echo "📚 Documentation:"
echo "  • Quick start: less docs/QUICKSTART.md"
echo "  • Speed guide: less docs/SPEED_GUIDE.md"
echo "  • Full docs: less README.md"
echo ""
