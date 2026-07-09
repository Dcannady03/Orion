"""
OpenAI AI Provider Placeholder

This will later connect Orion to the OpenAI API.
"""

from orion.intelligence.provider import AIProvider


class OpenAIProvider(AIProvider):
    """Placeholder provider for OpenAI."""

    def __init__(self, model: str):
        self.model = model

    def name(self) -> str:
        return f"openai:{self.model}"

    def chat(self, prompt: str) -> str:
        raise NotImplementedError(
            "OpenAI provider is not implemented yet. "
            "Set providers.default to ollama for now."
        )
