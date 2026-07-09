"""
Orion Brain

The Brain is Orion's central intelligence layer.

Responsibilities:
- Receive requests from the command router
- Decide how intelligence requests should be handled
- Talk to the configured AI provider
- Provide a future home for memory, tools, skills, and agents
"""

from orion.intelligence.intent import IntentDetector


class Brain:
    """Central intelligence coordinator for Orion."""

    def __init__(self, ai_provider, config_manager=None):
        self.ai_provider = ai_provider
        self.config_manager = config_manager
        self.intent_detector = IntentDetector()

    def name(self) -> str:
        """Return a readable name for the active intelligence stack."""
        return f"brain -> {self.ai_provider.name()}"

    def ask(self, prompt: str) -> str:
        """
        Handle a general intelligence request.

        For now, this forwards directly to the configured AI provider.
        Later this method can add memory, context, skill routing, approval checks,
        and model selection.
        """
        prompt = prompt.strip()

        if not prompt:
            return "Usage: ask <your question>"

        intent = self.intent_detector.detect(prompt)

        if intent == "chat":
            return self.ai_provider.chat(prompt)

        # Future intents will route to skills/tools here.
        return self.ai_provider.chat(prompt)
