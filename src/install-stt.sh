#!/bin/bash
# install-stt.sh — Install STT backends

set -e

echo "╔════════════════════════════════════════════════════════════╗"
echo "║         Ghost STT Backend Installer                        ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Install Python 3.10+"
    exit 1
fi

# Check if venv exists
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
else
    echo "✓ Using existing venv"
    source .venv/bin/activate
fi

echo ""
echo "Select STT backend to install:"
echo ""
echo "1) Moonshine (RECOMMENDED) — 2-3x faster, 95% accuracy"
echo "2) Distil-Whisper (FASTEST) — 6x faster, 93% accuracy"
echo "3) Faster-Whisper (standard) — baseline, 99% accuracy"
echo "4) All backends"
echo ""
read -p "Enter choice (1-4): " choice

case $choice in
    1)
        echo "📥 Installing Moonshine..."
        pip install --upgrade transformers torch torchaudio
        echo "✓ Moonshine installed"
        ;;
    2)
        echo "📥 Installing Distil-Whisper..."
        pip install --upgrade transformers torch torchaudio
        echo "✓ Distil-Whisper installed"
        ;;
    3)
        echo "📥 Installing Faster-Whisper..."
        pip install --upgrade faster-whisper torch
        echo "✓ Faster-Whisper installed"
        ;;
    4)
        echo "📥 Installing all backends..."
        pip install --upgrade transformers faster-whisper torch torchaudio
        echo "✓ All backends installed"
        ;;
    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac

# Install GUI deps if needed
echo ""
echo "📥 Installing core dependencies..."
pip install --upgrade pyside6 mss pillow imagehash numpy sounddevice pyyaml

echo ""
echo "✓ Installation complete!"
echo ""
echo "Test your STT backend:"
echo "  python3 stt_fast.py <audio.wav> moonshine"
echo ""
echo "Run Ghost:"
echo "  python3 main.py --config config-fast.yaml"
