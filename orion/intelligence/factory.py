"""
Orion AI Provider Factory

Creates the correct AI provider from Orion's configuration.
"""

from orion.intelligence.ollama_provider import OllamaProvider
from orion.intelligence.openai_provider import OpenAIProvider


class AIProviderFactory:
    """Builds AI provider instances from ConfigManager settings."""

    def __init__(self, config_manager):
        self.config_manager = config_manager

    def create(self):
        provider_name = self.config_manager.get("providers.default", "ollama")

        if provider_name == "ollama":
            enabled = self.config_manager.get("providers.ollama.enabled", False)
            if not enabled:
                raise ValueError("Ollama provider is selected but not enabled.")

            base_url = self.config_manager.get(
                "providers.ollama.base_url",
                "http://localhost:11434",
            )
            model = self.config_manager.get("providers.ollama.model", "llama3")
            return OllamaProvider(base_url=base_url, model=model)

        if provider_name == "openai":
            enabled = self.config_manager.get("providers.openai.enabled", False)
            if not enabled:
                raise ValueError("OpenAI provider is selected but not enabled.")

            model = self.config_manager.get("providers.openai.model", "gpt-4.1-mini")
            return OpenAIProvider(model=model)

        raise ValueError(f"Unknown AI provider: {provider_name}")
