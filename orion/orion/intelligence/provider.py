"""
Orion AI Provider Interface

Defines the common contract every AI backend must follow.
"""

from abc import ABC, abstractmethod


class AIProvider(ABC):
    """Base interface for Orion AI providers."""

    @abstractmethod
    def chat(self, prompt: str, system_prompt: str | None = None) -> str:
        """Send a prompt to the provider and return a response."""
        raise NotImplementedError

    @abstractmethod
    def name(self) -> str:
        """Return the provider name."""
        raise NotImplementedError
