#!/usr/bin/env bash
# One-shot installer for bob-der2.0 (alex-der)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> bob-der2.0 installer (codename: alex-der)"
echo "    Working dir: $SCRIPT_DIR"
echo

# ── Python check ──────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 not found. Install Python 3.11+ first."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "==> Python $PY_VERSION found"

# ── Venv (optional) ───────────────────────────────────────────────────────────
VENV_ACTIVE=false

if [[ ! -d "$SCRIPT_DIR/.venv" ]]; then
    echo "==> Trying to create virtual environment..."
    if python3 -m venv "$SCRIPT_DIR/.venv" 2>/dev/null; then
        echo "    Virtual environment created."
    else
        echo "    Venv unavailable (python3-venv not installed) — using system Python."
    fi
fi

if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
    VENV_ACTIVE=true
fi

# ── Dependencies ──────────────────────────────────────────────────────────────
echo "==> Checking dependencies..."

_install_deps() {
    if $VENV_ACTIVE; then
        pip install --quiet --upgrade pip
        pip install --quiet rich requests
    else
        # Try pip with --user, then pip3, then pipx-style fallback
        if python3 -m pip install --quiet --user rich requests 2>/dev/null; then
            echo "    Installed via pip --user"
        else
            echo "    pip not available — checking if deps are already present..."
        fi
    fi
}

# Check if already installed
RICH_OK=$(python3 -c "import rich; print('ok')" 2>/dev/null || echo "missing")
REQ_OK=$(python3 -c "import requests; print('ok')" 2>/dev/null || echo "missing")

if [[ "$RICH_OK" == "ok" && "$REQ_OK" == "ok" ]]; then
    echo "    rich and requests already available — skipping install."
else
    _install_deps
    # Re-check
    RICH_OK=$(python3 -c "import rich; print('ok')" 2>/dev/null || echo "missing")
    REQ_OK=$(python3 -c "import requests; print('ok')" 2>/dev/null || echo "missing")
    if [[ "$RICH_OK" != "ok" || "$REQ_OK" != "ok" ]]; then
        echo "[ERROR] Could not install required packages (rich, requests)."
        echo "        Try manually: sudo apt install python3-rich python3-requests"
        echo "        Or:           pip install --user rich requests"
        exit 1
    fi
fi

echo "    rich: $RICH_OK  requests: $REQ_OK"

# ── Launcher script ───────────────────────────────────────────────────────────
chmod +x "$SCRIPT_DIR/alex-der"

# Update shebang in launcher to use whichever Python is active
if $VENV_ACTIVE; then
    PY_BIN="$SCRIPT_DIR/.venv/bin/python3"
else
    PY_BIN="$(command -v python3)"
fi

# Rewrite launcher to point at the correct interpreter
cat > "$SCRIPT_DIR/alex-der" <<LAUNCHER
#!/usr/bin/env bash
# bob-der2.0 launcher — codename: alex-der
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
exec "$PY_BIN" "\$SCRIPT_DIR/main.py" "\$@"
LAUNCHER
chmod +x "$SCRIPT_DIR/alex-der"

# ── ~/.local/bin shortcuts ────────────────────────────────────────────────────
LOCALBIN="$HOME/.local/bin"
mkdir -p "$LOCALBIN"

for SHORTCUT in alex-der bob-der2; do
    TARGET="$LOCALBIN/$SHORTCUT"
    cat > "$TARGET" <<SC
#!/usr/bin/env bash
exec "$PY_BIN" "$SCRIPT_DIR/main.py" "\$@"
SC
    chmod +x "$TARGET"
    echo "==> Installed shortcut: $TARGET"
done

# Warn if ~/.local/bin isn't in PATH
if ! echo "$PATH" | grep -q "$LOCALBIN"; then
    echo
    echo "    [NOTE] $LOCALBIN is not in your PATH."
    echo "    Add this to ~/.bashrc or ~/.zshrc:"
    echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# ── Ollama ────────────────────────────────────────────────────────────────────
echo
if command -v ollama &>/dev/null; then
    echo "==> Ollama found: $(ollama --version 2>/dev/null || echo 'installed')"
    echo "==> Pulling model qwen3-coder-next:cloud ..."
    echo "    (this may take a while on first run)"
    ollama pull qwen3-coder-next:cloud || echo "    [WARN] Pull failed — model will be pulled on first use"
else
    echo "[WARN] Ollama not found."
    echo "       Install: https://ollama.com"
    echo "       Then:    ollama pull qwen3-coder-next:cloud"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo
echo "==> Done! Run with:"
echo "    $SCRIPT_DIR/alex-der"
if echo "$PATH" | grep -q "$LOCALBIN"; then
    echo "    alex-der    (shortcut)"
    echo "    bob-der2    (shortcut)"
fi
