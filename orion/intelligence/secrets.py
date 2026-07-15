"""Local secret references for Orion AI providers.

Environment variables are preferred. A workspace-local secret file is available
for users who explicitly choose persistence. Secret values are never written to
Orion's normal YAML configuration.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import yaml


class SecretStore:
    ENV_NAMES = {
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }

    def __init__(self, path: str | Path = ".orion/secrets.yaml"):
        self.path = Path(path)

    def source(self, provider: str) -> str:
        key = provider.lower().strip()
        env_name = self.ENV_NAMES.get(key)
        if env_name and os.environ.get(env_name):
            return f"environment ({env_name})"
        return "local vault" if self.get_file_value(key) else "not configured"

    def get_file_value(self, provider: str) -> str:
        key = provider.lower().strip()
        if not self.path.exists():
            return ""
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return ""
        value = data.get(key, {}).get("api_key", "") if isinstance(data, dict) else ""
        return str(value).strip()

    def get(self, provider: str) -> str:
        key = provider.lower().strip()
        env_name = self.ENV_NAMES.get(key)
        if env_name and os.environ.get(env_name):
            return os.environ[env_name].strip()
        return self.get_file_value(key)

    def set(self, provider: str, api_key: str) -> None:
        key = provider.lower().strip()
        value = api_key.strip()
        if not value:
            raise ValueError("API key cannot be empty.")
        data = {}
        if self.path.exists():
            try:
                data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise ValueError(f"Secret store is invalid: {exc}") from exc
        if not isinstance(data, dict):
            data = {}
        data.setdefault(key, {})["api_key"] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        temporary.replace(self.path)

    def delete(self, provider: str) -> None:
        if not self.path.exists():
            return
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            data.pop(provider.lower().strip(), None)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
            try:
                os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            temporary.replace(self.path)
