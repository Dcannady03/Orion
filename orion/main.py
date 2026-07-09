from orion.core.orion import Orion
from pathlib import Path
import yaml

APP_NAME = "Orion"
VERSION = "0.0.1"
CODENAME = "First Light"


def load_config():
    config_path = Path("config/default.yaml")

    if not config_path.exists():
        print("Configuration file not found.")
        return {}

    with open(config_path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def print_banner():
    print("=" * 50)
    print(f"{APP_NAME:^50}")
    print("=" * 50)
    print(f"Version : {VERSION}")
    print(f"Codename: {CODENAME}")
    print()
    print("Hello Daniel.")
    print()


def handle_command(command, config):
    command = command.strip().lower()

    if command == "help":
        print("""
Available commands:
  help     Show this help menu
  status   Show Orion system status
  config   Show loaded configuration
  exit     Shut down Orion
""")

    elif command == "status":
        print("System Status: READY")
        print("Core: Online")
        print("Command Router: Online")

    elif command == "config":
        print("Loaded configuration:")
        print(config)

    elif command in ["exit", "quit"]:
        print("Shutting down Orion.")
        return False

    elif command == "":
        pass

    else:
        print(f"Unknown command: {command}")
        print("Type 'help' for available commands.")

    return True


def main():
    print_banner()

    config = load_config()

    if config:
        print("Configuration Loaded.")
    else:
        print("Configuration Empty or Missing.")

    print("System Initialized.")
    print("Status: READY")
    print()
    print("Welcome to Orion.")
    print("=" * 50)

    running = True
    while running:
        user_input = input("\nOrion> ")
        running = handle_command(user_input, config)


if __name__ == "__main__":
    main()