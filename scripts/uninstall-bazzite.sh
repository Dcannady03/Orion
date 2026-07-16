#!/usr/bin/env bash
set -euo pipefail

rm -f "$HOME/.local/bin/orion"
rm -f "$HOME/.local/share/applications/orion.desktop"
rm -f "$HOME/.local/share/icons/hicolor/256x256/apps/orion.png"

echo "Removed Orion's Linux launcher and desktop entry."
echo "The project files, configuration, and .venv were left in place."
