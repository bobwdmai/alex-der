#!/usr/bin/env bash
# One-shot installer for bob-der2.0 (alex-der)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> bob-der2.0 installer (codename: alex-der)"
echo "    Working dir: $SCRIPT_DIR"
echo

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 not found. Install Python 3.11+ first."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "==> Python $PY_VERSION found"

# Create venv
if [[ ! -d "$SCRIPT_DIR/.venv" ]]; then
    echo "==> Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/.venv"
fi

source "$SCRIPT_DIR/.venv/bin/activate"

echo "==> Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet rich requests 2>/dev/null || python3 -m pip install --user rich requests

# Make launcher executable
chmod +x "$SCRIPT_DIR/alex-der"

# Optional: symlink to ~/.local/bin
if [[ -d "$HOME/.local/bin" ]]; then
    ln -sf "$SCRIPT_DIR/alex-der" "$HOME/.local/bin/alex-der"
    ln -sf "$SCRIPT_DIR/alex-der" "$HOME/.local/bin/bob-der"
    echo "==> Symlinked to ~/.local/bin/alex-der and ~/.local/bin/bob-der"
fi

# Check Ollama
if command -v ollama &>/dev/null; then
    echo "==> Ollama found: $(ollama --version 2>/dev/null || echo 'installed')"
    echo
    echo "==> Pulling model qwen3-coder-next:cloud ..."
    echo "    (this may take a while on first run)"
    ollama pull qwen3-coder-next:cloud || echo "    [WARN] Pull failed — model will be pulled on first use"
else
    echo
    echo "[WARN] Ollama not found. Install it from https://ollama.com"
    echo "       Then run: ollama pull qwen3-coder-next:cloud"
fi

echo
echo "==> Done! Run with:"
echo "    ./alex-der"
echo "    # or, if ~/.local/bin is in PATH:"
echo "    alex-der"
