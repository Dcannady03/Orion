"""
Orion Command Router

Responsible for:
- Receiving user commands
- Routing commands to the correct Orion subsystem
- Keeping command handling out of main.py
"""


class CommandRouter:
    """Routes commands entered into Orion's CLI."""

    def __init__(self, orion):
        self.orion = orion

    def handle(self, command: str) -> bool:
        """
        Handle a single command.

        Returns:
            True if Orion should keep running.
            False if Orion should shut down.
        """
        raw_command = command.strip()
        command_lower = raw_command.lower()

        if command_lower == "":
            return True

        if command_lower == "help":
            self.show_help()

        elif command_lower == "status":
            self.show_status()

        elif command_lower == "config":
            self.show_config()

        elif command_lower == "ask":
            print("Usage: ask <your question>")

        elif command_lower.startswith("ask "):
            prompt = raw_command[4:].strip()
            self.ask_ai(prompt)

        elif command_lower in ["exit", "quit"]:
            print("Shutting down Orion.")
            return False

        else:
            print(f"Unknown command: {raw_command}")
            print("Type 'help' for available commands.")

        return True

    def show_help(self):
        """Display available commands."""
        print("""
Available commands:
  help     Show this help menu
  status   Show Orion system status
  config   Show loaded configuration
  ask      Ask Orion's configured AI provider
  exit     Shut down Orion
""")

    def show_status(self):
        """Display Orion status."""
        print(f"System Status: {self.orion.status}")
        print("Core: Online")
        print("Command Router: Online")
        print(f"AI Provider: {self.orion.ai_provider.name()}")

    def show_config(self):
        """Display loaded configuration."""
        print("Loaded configuration:")
        print(self.orion.config)

    def ask_ai(self, prompt: str):
        """Send a prompt to Orion's configured AI provider."""
        if not prompt:
            print("Usage: ask <your question>")
            return

        print("Thinking...")

        try:
            response = self.orion.ai_provider.chat(prompt)
        except Exception as exc:
            print(f"AI Error: {exc}")
            return

        if response:
            print()
            print(response)
        else:
            print("No response received from AI provider.")
