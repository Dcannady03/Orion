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