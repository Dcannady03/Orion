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
from orion.core.paths import OrionPaths


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
        self.paths = getattr(config_manager, "paths", None) or OrionPaths()
        self.paths.ensure()
        configured = self.config.get("vault.path", self.config.get("providers.secrets_path", ""))
        path = self.paths.user_file(configured) if configured else self.paths.vault
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
        migrated = False
        for legacy in self._legacy_candidates():
            if legacy.resolve() == self.path.resolve() or not legacy.is_file():
                continue
            legacy_store = SecretStore(legacy)
            for key in self.PROVIDERS:
                if self.store.get_file_value(key):
                    continue
                value = legacy_store.get_file_value(key)
                if value:
                    self.store.set(key, value)
                    migrated = True
        return migrated

    def _legacy_candidates(self) -> tuple[Path, ...]:
        """Find pre-external-vault files, including copies saved by the updater."""
        configured = (
            self.config.get("vault.path", "vault/vault.yaml"),
            self.config.get("providers.secrets_path", ".orion/secrets.yaml"),
            ".orion/vault.yaml",
            ".orion/secrets.yaml",
        )
        relative_paths: list[Path] = []
        candidates: list[Path] = []
        for value in configured:
            path = Path(str(value)).expanduser()
            if path.is_absolute():
                candidates.append(path)
            else:
                relative_paths.append(path)
                candidates.append(self.paths.install_root / path)

        backups = sorted(
            (
                path / "application"
                for path in self.paths.backups.glob("application-*")
                if (path / "application").is_dir()
            ),
            reverse=True,
        )
        for application in backups:
            candidates.extend(application / path for path in relative_paths)

        unique: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                unique.append(candidate)
        return tuple(unique)

    @classmethod
    def _validate(cls, key: str) -> str:
        normalized = key.lower().strip()
        if normalized not in cls.PROVIDERS:
            raise ValueError("Vault currently supports: openai, gemini, discord, discord_bot")
        return normalized
