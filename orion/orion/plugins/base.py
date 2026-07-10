"""Plugin contracts for Orion."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PluginContext:
    """Controlled access to Orion resources exposed to plugins."""
    orion: Any
    services: Any
    workspace_root: Path


class OrionPlugin:
    """Base class for Orion plugins."""
    name = "unnamed"
    version = "0.0.0"
    description = ""

    def activate(self, context: PluginContext) -> None:
        """Register services or initialize resources."""

    def deactivate(self) -> None:
        """Release resources before unloading."""

    def handle(self, command: str) -> bool:
        """Handle a command and return True when claimed."""
        return False

    def help_lines(self) -> list[str]:
        """Return CLI help lines supplied by this plugin."""
        return []
