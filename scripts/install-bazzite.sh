#!/usr/bin/env bash
set -euo pipefail

ORION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ORION_ROOT/.venv"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
LAUNCHER="$BIN_DIR/orion"
DESKTOP_FILE="$APP_DIR/orion.desktop"

echo "=========================================="
echo "       Orion Linux/Bazzite Installer"
echo "=========================================="
echo "Project: $ORION_ROOT"
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 was not found."
    echo "On Bazzite, use a Distrobox/Toolbox environment or install Python through a supported Bazzite method."
    exit 1
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
echo "[OK] Python $PYTHON_VERSION"

if ! python3 -c 'import venv' >/dev/null 2>&1; then
    echo "ERROR: Python's venv module is unavailable."
    echo "Use a Python installation that includes venv, or run Orion inside Distrobox."
    exit 1
fi

echo "[1/5] Creating virtual environment..."
python3 -m venv "$VENV_DIR"

echo "[2/5] Updating pip..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip

if [[ -f "$ORION_ROOT/requirements.txt" ]]; then
    echo "[3/5] Installing Orion dependencies..."
    "$VENV_DIR/bin/python" -m pip install -r "$ORION_ROOT/requirements.txt"
else
    echo "[3/5] No requirements.txt found; skipping dependency installation."
fi

echo "[4/5] Installing command launcher..."
mkdir -p "$BIN_DIR"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
ORION_ROOT="$ORION_ROOT"
cd "\$ORION_ROOT"
exec "\$ORION_ROOT/.venv/bin/python" -m orion.main "\$@"
EOF
chmod +x "$LAUNCHER"

echo "[5/5] Installing desktop entry..."
mkdir -p "$APP_DIR"
ICON_VALUE="utilities-terminal"

for candidate in \
    "$ORION_ROOT/docs/orion_icon.png" \
    "$ORION_ROOT/assets/orion_icon.png" \
    "$ORION_ROOT/orion_icon.png"; do
    if [[ -f "$candidate" ]]; then
        mkdir -p "$ICON_DIR"
        cp "$candidate" "$ICON_DIR/orion.png"
        ICON_VALUE="$ICON_DIR/orion.png"
        break
    fi
done

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Orion
GenericName=Personal AI Operating System
Comment=Launch the Orion AI assistant
Exec=$LAUNCHER
Icon=$ICON_VALUE
Terminal=true
Categories=Utility;Development;
Keywords=AI;Assistant;Orion;
StartupNotify=true
EOF
chmod +x "$DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi

echo
echo "=========================================="
echo " Orion installation completed"
echo "=========================================="
echo
echo "Run Orion with:"
echo "  orion"
echo
echo "You can also launch Orion from the KDE application menu."
echo

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo "NOTE: $BIN_DIR is not currently in PATH."
    echo "Add this line to ~/.bashrc:"
    echo '  export PATH="$HOME/.local/bin:$PATH"'
    echo
    echo "Then run:"
    echo "  source ~/.bashrc"
fi
