"""
Orion Profile Manager

Responsible for:
- Loading the user profile
- Providing safe access to profile values
- Keeping user identity separate from system configuration
"""

from pathlib import Path
import yaml


class ProfileManager:
    """Loads and reads Orion's user profile."""

    def __init__(self, profile_path: str = "config/profile.yaml"):
        self.profile_path = Path(profile_path)
        self.profile = {}

    def load(self) -> dict:
        """Load the profile YAML file."""
        if not self.profile_path.exists():
            raise FileNotFoundError(
                f"Profile file not found: {self.profile_path}"
            )

        with self.profile_path.open("r", encoding="utf-8") as file:
            self.profile = yaml.safe_load(file) or {}

        return self.profile

    def get(self, key: str, default=None):
        """Get a profile value by key."""
        return self.profile.get(key, default)

    @property
    def name(self) -> str:
        """Return the user's preferred display name."""
        return (
            self.get("preferred_name")
            or self.get("name")
            or "User"
        )

    @property
    def timezone(self) -> str:
        """Return the user's configured timezone."""
        return self.get("timezone", "UTC")

    @property
    def location(self) -> str:
        """Return the user's configured location."""
        return self.get("location", "Unknown")

    @property
    def language(self) -> str:
        """Return the user's preferred language."""
        return self.get("language", "English")
