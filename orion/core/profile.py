"""
Orion Profile Manager

Responsible for:
- Loading the user profile
- Providing safe access to profile values
- Keeping user identity separate from system configuration
"""

from pathlib import Path
import shutil
import yaml

from orion.core.paths import OrionPaths


class ProfileManager:
    """Loads and reads Orion's user profile."""

    def __init__(self, profile_path: str | Path | None = None):
        self.paths = OrionPaths()
        self.paths.ensure()
        self.profile_path = Path(profile_path) if profile_path is not None else self.paths.profile
        self.profile = {}
        if profile_path is None and not self.profile_path.exists():
            legacy = self.paths.install_root / "config" / "profile.yaml"
            if legacy.exists():
                self.profile_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy, self.profile_path)

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
