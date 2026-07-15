"""Orion AI provider factory."""

from orion.intelligence.ollama_provider import OllamaProvider
from orion.intelligence.openai_provider import OpenAIProvider
from orion.intelligence.gemini_provider import GeminiProvider
from orion.intelligence.secrets import SecretStore


class AIProviderFactory:
    def __init__(self, config_manager, secret_store=None):
        self.config_manager = config_manager
        self.secret_store = secret_store or SecretStore(
            self.config_manager.get("providers.secrets_path", ".orion/secrets.yaml")
        )

    def create(self, provider_name: str | None = None):
        provider_name = (provider_name or self.config_manager.get("providers.default", "ollama")).lower()
        if provider_name == "ollama":
            if not self.config_manager.get("providers.ollama.enabled", False):
                raise ValueError("Ollama provider is selected but not enabled.")
            return OllamaProvider(
                base_url=self.config_manager.get("providers.ollama.base_url", "http://localhost:11434"),
                model=self.config_manager.get("providers.ollama.model", "llama3"),
            )
        if provider_name == "openai":
            if not self.config_manager.get("providers.openai.enabled", False):
                raise ValueError("OpenAI provider is selected but not enabled.")
            return OpenAIProvider(
                model=self.config_manager.get("providers.openai.model", "gpt-4.1-mini"),
                api_key=self.secret_store.get("openai"),
                base_url=self.config_manager.get("providers.openai.base_url", "https://api.openai.com/v1"),
                timeout=float(self.config_manager.get("providers.openai.timeout_seconds", 60)),
            )
        if provider_name == "gemini":
            if not self.config_manager.get("providers.gemini.enabled", False):
                raise ValueError("Gemini provider is selected but not enabled.")
            return GeminiProvider(
                model=self.config_manager.get("providers.gemini.model", "gemini-2.5-flash"),
                api_key=self.secret_store.get("gemini"),
                base_url=self.config_manager.get("providers.gemini.base_url", "https://generativelanguage.googleapis.com/v1beta"),
                timeout=float(self.config_manager.get("providers.gemini.timeout_seconds", 60)),
            )
        raise ValueError(f"Unknown AI provider: {provider_name}")
