"""
Orion Configuration Manager
"""

from pathlib import Path
import yaml


class ConfigManager:
    def __init__(self, config_path: str = "config/default.yaml"):
        self.config_path = Path(config_path)
        self.config = {}

    def load(self) -> dict:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with self.config_path.open("r", encoding="utf-8") as file:
            self.config = yaml.safe_load(file) or {}

        return self.config

    def get(self, key_path: str, default=None):
        keys = key_path.split(".")
        value = self.config

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
        """Persist the current configuration without discarding unrelated keys."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config_path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(self.config, file, sort_keys=False, allow_unicode=True)
