"""Provider federation management for Polaris."""

from __future__ import annotations

from dataclasses import dataclass
from orion.intelligence.factory import AIProviderFactory
from orion.intelligence.secrets import SecretStore


@dataclass(frozen=True)
class ProviderStatus:
    key: str
    enabled: bool
    configured: bool
    active: bool
    model: str


class ProviderManager:
    PROVIDERS = ("ollama", "openai", "gemini")

    def __init__(self, orion, config_manager, secret_store=None):
        self.orion = orion
        self.config = config_manager
        self.secrets = secret_store or SecretStore(self.config.get("providers.secrets_path", ".orion/secrets.yaml"))

    def statuses(self) -> list[ProviderStatus]:
        active = self.config.get("providers.default", "ollama")
        results = []
        for key in self.PROVIDERS:
            enabled = bool(self.config.get(f"providers.{key}.enabled", key == "ollama"))
            configured = True if key == "ollama" else bool(self.secrets.get(key))
            results.append(ProviderStatus(key, enabled, configured, key == active, self.config.get(f"providers.{key}.model", "")))
        return results

    def configure(self, provider: str, api_key: str, model: str | None = None) -> None:
        key = self._validate(provider)
        if key == "ollama":
            raise ValueError("Ollama does not require an API key.")
        self.secrets.set(key, api_key)
        self.config.set(f"providers.{key}.enabled", True)
        if model:
            self.config.set(f"providers.{key}.model", model.strip())
        self.config.save()

    def activate(self, provider: str, persist: bool = True):
        key = self._validate(provider)
        instance = AIProviderFactory(self.config, self.secrets).create(key)
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
            return self.models(key)
        if not self.secrets.get(key):
            raise ValueError(f"{key.title()} API key is not configured.")
        return self.models(key)

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
