"""Orion configuration manager with update-safe local overrides."""
from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from orion.core.paths import OrionPaths


_MISSING = object()


class ConfigManager:
    """Load repository defaults and optional private local overrides.

    The normal Orion runtime uses ``config/default.yaml`` as read-only product
    defaults and stores user changes in ``~/.orion/config/local.yaml``. Tests
    and tools that pass an explicit ``config_path`` retain the original
    single-file behavior unless they also provide ``local_config_path``.
    """

    PERSISTENT_ROOTS = {
        "providers",
        "workspace",
        "memory",
        "voice",
        "safety",
        "plugins",
        "weather",
        "calendar",
        "email",
        "ai",
        "team",
        "vault",
        "connect",
    }

    def __init__(
        self,
        config_path: str | Path | None = None,
        local_config_path: str | Path | None = None,
    ):
        explicit_config = config_path is not None
        self.paths = OrionPaths()
        self.paths.ensure()
        self.config_path = Path(config_path) if explicit_config else self.paths.defaults
        self.layered = not explicit_config or local_config_path is not None

        if self.layered:
            configured_local = local_config_path or os.environ.get("ORION_LOCAL_CONFIG")
            self.local_config_path = Path(configured_local) if configured_local else self.paths.config
        else:
            self.local_config_path = self.config_path

        self.defaults: dict[str, Any] = {}
        self.local_config: dict[str, Any] = {}
        self.config: dict[str, Any] = {}
        self.recovered_from: Path | None = None

    def load(self) -> dict:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        self.defaults = self._read_yaml(self.config_path)
        if not self.layered:
            self.config = deepcopy(self.defaults)
            return self.config

        if self.local_config_path.exists():
            self.local_config = self._read_yaml(self.local_config_path)
        elif self.paths.legacy_local_config.exists() and self.local_config_path == self.paths.config:
            self.local_config = self._read_yaml(self.paths.legacy_local_config)
            self._write_yaml(self.local_config_path, self.local_config)
        else:
            self.local_config = self._recover_local_overrides()
            if self.local_config:
                self._write_yaml(self.local_config_path, self.local_config)

        self.config = self._deep_merge(self.defaults, self.local_config)
        return self.config

    def get(self, key_path: str, default=None):
        keys = key_path.split(".")
        value: Any = self.config

        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]

        return value

    def set(self, key_path: str, value) -> None:
        """Set a nested configuration value in memory."""
        keys = key_path.split(".")
        current = self.config
        for key in keys[:-1]:
            child = current.get(key)
            if not isinstance(child, dict):
                child = {}
                current[key] = child
            current = child
        current[keys[-1]] = value

    def save(self) -> None:
        """Persist user changes without modifying repository defaults."""
        if not self.layered:
            self._write_yaml(self.config_path, self.config)
            return

        self.local_config = self._deep_diff(self.defaults, self.config)
        self._write_yaml(self.local_config_path, self.local_config)

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as file:
            value = yaml.safe_load(file) or {}
        if not isinstance(value, dict):
            raise ValueError(f"Configuration root must be a mapping: {path}")
        return value

    @staticmethod
    def _write_yaml(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(value, file, sort_keys=False, allow_unicode=True)

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged

    @classmethod
    def _deep_diff(cls, base: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in current.items():
            base_value = base.get(key, _MISSING)
            if isinstance(value, dict) and isinstance(base_value, dict):
                nested = cls._deep_diff(base_value, value)
                if nested:
                    result[key] = nested
            elif base_value is _MISSING or value != base_value:
                result[key] = deepcopy(value)
        return result

    def _recover_local_overrides(self) -> dict[str, Any]:
        """Recover private settings from the newest pre-update backup.

        Relay stores update backups beside the installation repository. This
        one-time migration lets an existing installation recover settings such
        as Discord owner/channel IDs after repository defaults were replaced.
        Product identity keys (version/codename) are intentionally excluded.
        """
        install_root = self.config_path.resolve().parent.parent
        candidates: list[Path] = []

        # Current package updates store application snapshots under the external
        # user-data root. Recover settings that predate layered configuration.
        candidates.extend(sorted(
            (
                path / "application" / "config" / self.config_path.name
                for path in self.paths.backups.glob("application-*")
                if (path / "application").is_dir()
            ),
            reverse=True,
        ))

        # Older Relay builds used a sibling Orion-backups/update-* directory.
        backup_parent = install_root.parent / f"{install_root.name}-backups"
        if backup_parent.is_dir():
            candidates.extend(sorted(
                (
                    path / "config" / self.config_path.name
                    for path in backup_parent.glob("update-*")
                ),
                reverse=True,
            ))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                legacy = self._read_yaml(candidate)
            except (OSError, ValueError, yaml.YAMLError):
                continue

            selected = {
                key: deepcopy(value)
                for key, value in legacy.items()
                if key in self.PERSISTENT_ROOTS
            }
            current_selected = {
                key: deepcopy(value)
                for key, value in self.defaults.items()
                if key in self.PERSISTENT_ROOTS
            }
            overrides = self._deep_diff(current_selected, selected)
            if overrides:
                self.recovered_from = candidate
                return overrides
        return {}
