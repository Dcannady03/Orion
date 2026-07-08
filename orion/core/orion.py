"""
Orion Core

Responsible for:
- Booting Orion
- Initializing core services
- Displaying startup information

Author:
Daniel Cannady

Project:
Orion AI Operating System
"""

from orion.core.config import ConfigManager


class Orion:
    """Main Orion application."""

    def __init__(self):
        # Load configuration
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load()

        # Orion information
        self.name = self.config_manager.get("orion.name", "Orion")
        self.version = self.config_manager.get("orion.version", "0.0.1")
        self.codename = self.config_manager.get(
            "orion.codename",
            "First Light"
        )

        # User profile
        self.user_name = self.config_manager.get(
            "orion.user_name",
            "Daniel"
        )

        # System status
        self.status = "READY"

    def start(self):
        """Starts Orion."""

        self.banner()

        print(f"Hello {self.user_name}.")
        print()
        print("Configuration Loaded.")
        print("System Initialized.")
        print(f"Status: {self.status}")
        print()
        print(f"Welcome to {self.name}.")
        print("=" * 50)

    def banner(self):
        """Displays the Orion startup banner."""

        print("=" * 50)
        print(f"{self.name:^50}")
        print("=" * 50)
        print(f"Version : {self.version}")
        print(f"Codename: {self.codename}")
        print()