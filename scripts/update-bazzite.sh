#!/usr/bin/env bash
set -euo pipefail

ORION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ORION_ROOT/.venv"

cd "$ORION_ROOT"

if [[ -d .git ]]; then
    echo "[1/3] Pulling the latest Orion source..."
    git pull --ff-only
else
    echo "[1/3] This directory is not a Git repository; skipping git pull."
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "Orion's virtual environment is missing."
    echo "Run: ./scripts/install-bazzite.sh"
    exit 1
fi

echo "[2/3] Updating pip..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip

echo "[3/3] Updating dependencies..."
"$VENV_DIR/bin/python" -m pip install -r requirements.txt

echo "Orion is up to date."
