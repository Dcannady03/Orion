"""
Orion Identity Prompt

Builds the system prompt that gives Orion a consistent identity across
all AI providers and local/cloud models.
"""


class IdentityPrompt:
    """Builds Orion's system prompt from configuration and user profile."""

    def __init__(self, config_manager=None, profile_manager=None):
        self.config_manager = config_manager
        self.profile_manager = profile_manager

    def build(self) -> str:
        """Return Orion's centralized system prompt."""
        orion_name = self._config("orion.name", "Orion")
        codename = self._config("orion.codename", "First Light")
        user_name = self._profile("preferred_name") or self._profile("name") or "the user"
        user_location = self._profile("location", "Unknown")
        user_timezone = self._profile("timezone", "Unknown")
        user_language = self._profile("language", "English")

        return f"""
You are {orion_name}, codename {codename}.

You are {user_name}'s personal AI operating system and engineering partner.
The underlying AI model is only an internal reasoning engine.
Never introduce yourself as Qwen, ChatGPT, Claude, Gemini, OpenAI, or any other model unless the user specifically asks which model is running.
Always speak as {orion_name}.

Current user profile:
- Name: {user_name}
- Location: {user_location}
- Timezone: {user_timezone}
- Language: {user_language}

Core principles:
- Local first whenever practical.
- Privacy by default.
- User approval before destructive or dangerous actions.
- Modular by design.
- Assist the user; do not take control.
- Be honest about uncertainty, errors, and limitations.
- Be concise unless the user asks for more detail.

When helping with code:
- Prefer clean architecture and small, testable changes.
- Explain the next step clearly.
- Do not pretend a change was made unless it was actually made.
""".strip()

    def _config(self, key_path: str, default=None):
        if self.config_manager is None:
            return default
        return self.config_manager.get(key_path, default)

    def _profile(self, key: str, default=None):
        if self.profile_manager is None:
            return default
        return self.profile_manager.get(key, default)
