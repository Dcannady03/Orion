"""Provider federation management for Polaris."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orion.core.paths import OrionPaths
from orion.intelligence.factory import AIProviderFactory
from orion.intelligence.secrets import SecretStore


@dataclass(frozen=True)
class ProviderStatus:
    key: str
    enabled: bool
    configured: bool
    active: bool
    model: str


class _ConfigOverlay:
    """Read-only candidate settings layered over Orion's live configuration."""

    def __init__(self, config, values: dict[str, Any]):
        self.config = config
        self.values = values

    def get(self, key: str, default=None):
        return self.values.get(key, self.config.get(key, default))


class _SecretOverlay:
    """Expose one in-memory candidate credential without persisting it."""

    def __init__(self, store: SecretStore, provider: str, secret: str):
        self.store = store
        self.provider = provider
        self.secret = secret

    def get(self, provider: str) -> str:
        return self.secret if provider.lower().strip() == self.provider else self.store.get(provider)


class ProviderManager:
    PROVIDERS = ("ollama", "openai", "gemini")

    def __init__(self, orion, config_manager, secret_store=None):
        self.orion = orion
        self.config = config_manager
        paths = getattr(self.config, "paths", None) or OrionPaths()
        configured_path = self.config.get(
            "vault.path",
            self.config.get("providers.secrets_path", ""),
        )
        resolved_path = paths.user_file(configured_path) if configured_path else paths.vault
        self.secrets = secret_store or SecretStore(resolved_path)

    def statuses(self) -> list[ProviderStatus]:
        active = self.config.get("providers.default", "ollama")
        results = []
        for key in self.PROVIDERS:
            enabled = bool(self.config.get(f"providers.{key}.enabled", key == "ollama"))
            configured = True if key == "ollama" else bool(self.secrets.get(key))
            results.append(ProviderStatus(key, enabled, configured, key == active, self.config.get(f"providers.{key}.model", "")))
        return results

    def configure(self, provider: str, api_key: str, model: str | None = None) -> None:
        """Compatibility API routed through the verified Orion Vault transaction."""
        key = self._validate(provider)
        if key == "ollama":
            raise ValueError("Ollama does not require an API key.")
        from orion.services.vault import VaultService

        VaultService(
            self.config,
            store=self.secrets,
            provider_manager=self,
        ).connect_provider(key, api_key, model=model)

    def activate(self, provider: str, persist: bool = True):
        key = self._validate(provider)
        instance = AIProviderFactory(self.config, self.secrets).create(key)
        if self.orion is not None:
            self.orion.ai_provider = instance
            self.orion.brain.ai_provider = instance
            self.orion.ai_control.provider = instance
        if persist:
            self.config.set("providers.default", key)
            self.config.save()
        return instance

    def models(self, provider: str) -> list[str]:
        key = self._validate(provider)
        return AIProviderFactory(self.config, self.secrets).create(key).list_models()

    def test_connection(self, provider: str) -> list[str]:
        """Verify credentials without generating a billable model response."""
        key = self._validate(provider)
        if key == "ollama":
            return self.preview_models(key)
        if not self.secrets.get(key):
            raise ValueError(f"{key.title()} API key is not configured.")
        return self.preview_models(key, api_key=self.secrets.get(key))

    def verify_credentials(
        self,
        provider: str,
        api_key: str,
        *,
        model: str | None = None,
    ) -> list[str]:
        """Verify a cloud credential in memory without changing Vault or config."""
        key = self._validate(provider)
        if key == "ollama":
            raise ValueError("Ollama does not use an API credential.")
        candidate = str(api_key).strip()
        if not candidate:
            raise ValueError("API key cannot be empty.")
        return self.preview_models(key, api_key=candidate, model=model)

    def preview_models(
        self,
        provider: str,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> list[str]:
        """Discover models using candidate settings without persisting changes."""
        key = self._validate(provider)
        values: dict[str, Any] = {f"providers.{key}.enabled": True}
        if model is not None and str(model).strip():
            values[f"providers.{key}.model"] = str(model).strip()
        if base_url is not None and str(base_url).strip():
            values[f"providers.{key}.base_url"] = str(base_url).strip()
        config = _ConfigOverlay(self.config, values)
        secrets = self.secrets
        if api_key is not None:
            secrets = _SecretOverlay(self.secrets, key, str(api_key).strip())
        return AIProviderFactory(config, secrets).create(key).list_models()

    def set_model(self, provider: str, model: str, persist: bool = True):
        key = self._validate(provider)
        instance = AIProviderFactory(self.config, self.secrets).create(key)
        instance.select_model(model)
        if persist:
            self.config.set(f"providers.{key}.model", model)
            self.config.set("providers.default", key)
            self.config.save()
        self.orion.ai_provider = instance
        self.orion.brain.ai_provider = instance
        self.orion.ai_control.provider = instance
        return instance

    @classmethod
    def _validate(cls, provider: str) -> str:
        key = provider.lower().strip()
        if key not in cls.PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")
        return key
