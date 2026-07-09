"""
Orion Brain

The Brain is Orion's central intelligence layer.

Responsibilities:
- Receive requests from the command router
- Decide how intelligence requests should be handled
- Talk to the configured AI provider
- Apply Orion's identity/system prompt
- Provide a future home for memory, tools, skills, and agents
"""

from orion.intelligence.identity import IdentityPrompt
from orion.intelligence.intent import IntentDetector


class Brain:
    """Central intelligence coordinator for Orion."""

    def __init__(self, ai_provider, config_manager=None, profile_manager=None):
        self.ai_provider = ai_provider
        self.config_manager = config_manager
        self.profile_manager = profile_manager
        self.intent_detector = IntentDetector()
        self.identity_prompt = IdentityPrompt(
            config_manager=config_manager,
            profile_manager=profile_manager,
        )

    def name(self) -> str:
        """Return a readable name for the active intelligence stack."""
        return f"brain -> {self.ai_provider.name()}"

    def ask(self, prompt: str) -> str:
        """
        Handle a general intelligence request.

        For now, this forwards to the configured AI provider with Orion's
        identity prompt. Later this method can add memory, context, skill
        routing, approval checks, and model selection.
        """
        prompt = prompt.strip()

        if not prompt:
            return "Usage: ask <your question>"

        intent = self.intent_detector.detect(prompt)
        system_prompt = self.identity_prompt.build()

        if intent == "chat":
            return self.ai_provider.chat(prompt, system_prompt=system_prompt)

        # Future intents will route to skills/tools here.
        return self.ai_provider.chat(prompt, system_prompt=system_prompt)
