"""Discovery and lifecycle management for Orion plugins."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from orion.plugins.base import OrionPlugin, PluginContext


@dataclass(frozen=True)
class PluginRecord:
    name: str
    version: str
    description: str
    path: Path
    status: str
    error: str = ""


class PluginManager:
    """Discover, load, isolate, and route commands to Orion plugins."""

    def __init__(self, orion: Any, plugin_root: str | Path = "plugins"):
        self.orion = orion
        root = Path(plugin_root).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        self.plugin_root = root.resolve()
        self._plugins: dict[str, OrionPlugin] = {}
        self._records: dict[str, PluginRecord] = {}

    def discover(self) -> list[Path]:
        if not self.plugin_root.exists():
            return []
        return sorted(
            path for path in self.plugin_root.glob("*/plugin.py") if path.is_file()
        )

    def load_all(self) -> None:
        for path in self.discover():
            self.load(path)

    def load(self, path: str | Path) -> bool:
        plugin_path = Path(path).resolve()
        fallback_name = plugin_path.parent.name
        try:
            module = self._load_module(plugin_path)
            factory = getattr(module, "create_plugin", None)
            if not callable(factory):
                raise ValueError("Plugin must export create_plugin().")
            plugin = factory()
            if not isinstance(plugin, OrionPlugin):
                raise TypeError("create_plugin() must return an OrionPlugin instance.")
            name = self._normalize_name(plugin.name)
            if name in self._plugins:
                raise ValueError(f"Plugin already loaded: {name}")
            context = PluginContext(
                orion=self.orion,
                services=self.orion.services,
                workspace_root=self.orion.workspace_manager.root,
            )
            plugin.activate(context)
            self._plugins[name] = plugin
            self._records[name] = PluginRecord(
                name=name,
                version=str(plugin.version),
                description=str(plugin.description),
                path=plugin_path,
                status="loaded",
            )
            return True
        except Exception as exc:
            name = self._normalize_name(fallback_name)
            self._records[name] = PluginRecord(
                name=name,
                version="unknown",
                description="",
                path=plugin_path,
                status="failed",
                error=str(exc),
            )
            return False

    def dispatch(self, command: str) -> bool:
        for plugin in tuple(self._plugins.values()):
            try:
                if plugin.handle(command):
                    return True
            except Exception as exc:
                name = self._normalize_name(plugin.name)
                record = self._records[name]
                self._records[name] = PluginRecord(
                    name=record.name,
                    version=record.version,
                    description=record.description,
                    path=record.path,
                    status="error",
                    error=str(exc),
                )
                print(f"Plugin Error ({name}): {exc}")
                return True
        return False

    def help_lines(self) -> list[str]:
        lines: list[str] = []
        for plugin in self._plugins.values():
            try:
                lines.extend(plugin.help_lines())
            except Exception:
                continue
        return lines

    def records(self) -> list[PluginRecord]:
        return [self._records[name] for name in sorted(self._records)]

    def loaded_count(self) -> int:
        return len(self._plugins)

    def failed_count(self) -> int:
        return sum(1 for record in self._records.values() if record.status != "loaded")

    def get(self, name: str) -> OrionPlugin | None:
        return self._plugins.get(self._normalize_name(name))

    def _load_module(self, path: Path) -> ModuleType:
        module_name = f"orion_external_plugin_{path.parent.name}_{abs(hash(path))}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load plugin module: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = str(name).strip().lower().replace(" ", "_")
        if not normalized:
            raise ValueError("Plugin name cannot be empty.")
        return normalized
