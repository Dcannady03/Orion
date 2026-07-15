"""Orion Vault: centralized local credential management.

Secrets remain outside normal configuration and Git. Environment variables take
precedence; the local vault is protected with owner-only permissions where the
platform supports them. Native OS credential backends can replace this storage
behind the same service later.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from orion.intelligence.secrets import SecretStore


@dataclass(frozen=True)
class VaultEntry:
    key: str
    category: str
    configured: bool
    source: str


@dataclass(frozen=True)
class VaultHealth:
    key: str
    configured: bool
    healthy: bool
    message: str


class VaultService:
    PROVIDERS = ("openai", "gemini", "discord", "discord_bot")

    def __init__(self, config_manager, provider_manager=None, store: SecretStore | None = None):
        self.config = config_manager
        path = self.config.get(
            "vault.path",
            self.config.get("providers.secrets_path", ".orion/vault.yaml"),
        )
        self.store = store or SecretStore(path)
        self.provider_manager = provider_manager

    @property
    def path(self) -> Path:
        return self.store.path

    def list_entries(self) -> list[VaultEntry]:
        entries = [VaultEntry("ollama", "AI Provider", True, "local")]
        for key in self.PROVIDERS:
            configured = bool(self.store.get(key))
            entries.append(VaultEntry(key, "AI Provider" if key in {"openai", "gemini"} else "Communication", configured, self.store.source(key)))
        return entries

    def add(self, key: str, secret: str) -> None:
        normalized = self._validate(key)
        self.store.set(normalized, secret)
        self.config.set(f"providers.{normalized}.enabled", True)
        self.config.save()

    def remove(self, key: str) -> None:
        normalized = self._validate(key)
        self.store.delete(normalized)
        self.config.set(f"providers.{normalized}.enabled", False)
        if self.config.get("providers.default", "ollama") == normalized:
            self.config.set("providers.default", "ollama")
        self.config.save()

    def health(self, checker: Callable[[str], list[str]] | None = None) -> list[VaultHealth]:
        results = [VaultHealth("ollama", True, True, "Local provider; no API key required")]
        for key in self.PROVIDERS:
            configured = bool(self.store.get(key))
            if not configured:
                results.append(VaultHealth(key, False, False, "Not configured"))
                continue
            try:
                if key in {"openai", "gemini"} and checker:
                    models = checker(key)
                    message = f"Connected; {len(models)} compatible model(s)"
                else:
                    message = "Configured"
                results.append(VaultHealth(key, True, True, message))
            except (ConnectionError, OSError, ValueError) as exc:
                results.append(VaultHealth(key, True, False, str(exc)))
        return results

    def migrate_legacy_store(self) -> bool:
        legacy = Path(self.config.get("providers.secrets_path", ".orion/secrets.yaml"))
        if legacy == self.path or not legacy.exists() or self.path.exists():
            return False
        legacy_store = SecretStore(legacy)
        migrated = False
        for key in self.PROVIDERS:
            value = legacy_store.get_file_value(key)
            if value:
                self.store.set(key, value)
                migrated = True
        return migrated

    @classmethod
    def _validate(cls, key: str) -> str:
        normalized = key.lower().strip()
        if normalized not in cls.PROVIDERS:
            raise ValueError("Vault currently supports: openai, gemini, discord, discord_bot")
        return normalized
