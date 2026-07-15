"""Guided first-run setup for Orion.

First Contact runs before Orion's service graph is constructed.  It gathers the
minimum information needed for a useful, personalized first launch and writes
normal Orion YAML files, so the rest of the application does not need special
first-run branches.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from shutil import copy2
from typing import Callable

import yaml

from orion.version import __codename__, __version__


InputProvider = Callable[[str], str]
OutputProvider = Callable[[str], None]


@dataclass(frozen=True)
class FirstContactResult:
    completed: bool
    config_path: Path
    profile_path: Path
    workspace_path: Path


class FirstContact:
    """Create Orion's initial profile and configuration conversationally."""

    def __init__(
        self,
        config_path: str | Path = "config/default.yaml",
        profile_path: str | Path = "config/profile.yaml",
        *,
        input_provider: InputProvider = input,
        output_provider: OutputProvider = print,
    ) -> None:
        self.config_path = Path(config_path)
        self.profile_path = Path(profile_path)
        self.input = input_provider
        self.output = output_provider

    @property
    def is_required(self) -> bool:
        """Return True when Orion cannot perform a normal personalized boot."""
        if not self.config_path.exists() or not self.profile_path.exists():
            return True
        try:
            profile = yaml.safe_load(self.profile_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return True
        return not bool(profile.get("preferred_name") or profile.get("name"))

    def run(self, *, force: bool = False) -> FirstContactResult:
        if not force and not self.is_required:
            return FirstContactResult(False, self.config_path, self.profile_path, Path("."))

        self._show_welcome()
        name = self._ask("What should I call you?", required=True)
        full_name = self._ask("What is your full name?", default=name)
        location = self._ask("Where are you located? (city, state/country)", required=True)
        timezone = self._ask("What is your timezone?", default="America/Los_Angeles")
        language = self._ask("Preferred language", default="English")
        intended_use = self._choose(
            "How will you primarily use Orion?",
            ("Personal assistant", "Software development", "Home automation", "A mix of everything"),
            default=4,
        )

        workspace_text = self._ask("Default workspace folder", default=str(Path.cwd()))
        workspace = Path(workspace_text).expanduser()

        provider = self._choose("Choose your AI provider", ("Ollama (local)", "OpenAI (coming soon)"), default=1)
        if provider == "OpenAI (coming soon)":
            self.output("OpenAI support is not active in this release, so Orion will use Ollama for now.")
        ollama_url = self._ask("Ollama address", default="http://localhost:11434")
        ollama_model = self._ask("Ollama model", default="qwen3.6:35b")

        weather_enabled = self._yes_no("Enable live weather?", default=True)
        calendar_choice = self._choose(
            "Connect a calendar now?",
            ("Not now", "Google Calendar", "Microsoft Outlook", "Both"),
            default=1,
        )
        email_choice = self._choose(
            "Prepare email integration?",
            ("Not now", "Gmail", "Microsoft Outlook"),
            default=1,
        )
        docker_enabled = self._yes_no("Prepare Docker integration?", default=False)

        self._show_summary(
            name=name,
            location=location,
            timezone=timezone,
            workspace=workspace,
            model=ollama_model,
            weather=weather_enabled,
            calendar=calendar_choice,
            email=email_choice,
            docker=docker_enabled,
        )
        if not self._yes_no("Create this Orion profile?", default=True):
            self.output("First Contact cancelled. No files were changed.")
            return FirstContactResult(False, self.config_path, self.profile_path, workspace)

        profile = {
            "name": full_name,
            "preferred_name": name,
            "timezone": timezone,
            "location": location,
            "language": language,
            "intended_use": intended_use,
            "onboarding_complete": True,
        }
        config = self._build_config(
            workspace=workspace,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            weather_enabled=weather_enabled,
            calendar_choice=calendar_choice,
            email_choice=email_choice,
            docker_enabled=docker_enabled,
        )

        self._backup_existing(self.config_path)
        self._backup_existing(self.profile_path)
        self._write_yaml(self.config_path, config)
        self._write_yaml(self.profile_path, profile)
        workspace.mkdir(parents=True, exist_ok=True)

        self.output("")
        self.output("First Contact complete.")
        self.output(f"Welcome to Orion, {name}.")
        self.output("You can change any service later from Orion's settings and provider commands.")
        self.output("")
        return FirstContactResult(True, self.config_path, self.profile_path, workspace)

    def _show_welcome(self) -> None:
        self.output("=" * 58)
        self.output(f"{'ORION — FIRST CONTACT':^58}")
        self.output("=" * 58)
        self.output("Hello. I am Orion.")
        self.output("Before we begin, I would like to learn how I can help you.")
        self.output("Press Enter to accept any suggested answer.")
        self.output("")

    def _show_summary(self, **values) -> None:
        self.output("")
        self.output("Your Orion profile")
        self.output("-" * 58)
        labels = {
            "name": "Name", "location": "Location", "timezone": "Timezone",
            "workspace": "Workspace", "model": "AI model", "weather": "Weather",
            "calendar": "Calendar", "email": "Email", "docker": "Docker",
        }
        for key, value in values.items():
            if isinstance(value, bool):
                value = "Enabled" if value else "Not enabled"
            self.output(f"{labels[key]:<12}: {value}")
        self.output("-" * 58)

    def _ask(self, prompt: str, *, default: str | None = None, required: bool = False) -> str:
        while True:
            suffix = f" [{default}]" if default is not None else ""
            answer = self.input(f"{prompt}{suffix}: ").strip()
            if answer:
                return answer
            if default is not None:
                return default
            if not required:
                return ""
            self.output("Please enter a value so Orion can continue.")

    def _yes_no(self, prompt: str, *, default: bool) -> bool:
        hint = "Y/n" if default else "y/N"
        while True:
            answer = self.input(f"{prompt} [{hint}]: ").strip().lower()
            if not answer:
                return default
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            self.output("Please answer yes or no.")

    def _choose(self, prompt: str, options: tuple[str, ...], *, default: int) -> str:
        self.output(prompt)
        for index, option in enumerate(options, start=1):
            marker = " (recommended)" if index == default else ""
            self.output(f"  {index}. {option}{marker}")
        while True:
            answer = self.input(f"Choose [{default}]: ").strip()
            if not answer:
                return options[default - 1]
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
            self.output(f"Choose a number from 1 to {len(options)}.")

    @staticmethod
    def _build_config(
        *, workspace: Path, ollama_url: str, ollama_model: str,
        weather_enabled: bool, calendar_choice: str, email_choice: str,
        docker_enabled: bool,
    ) -> dict:
        google_enabled = calendar_choice in {"Google Calendar", "Both"}
        microsoft_enabled = calendar_choice in {"Microsoft Outlook", "Both"}
        calendar_enabled = google_enabled or microsoft_enabled
        return {
            "orion": {"name": "Orion", "version": __version__, "codename": __codename__},
            "providers": {
                "default": "ollama",
                "openai": {"enabled": False, "model": "gpt-4.1-mini"},
                "ollama": {"enabled": True, "base_url": ollama_url, "model": ollama_model},
            },
            "workspace": {"default_path": str(workspace)},
            "memory": {"enabled": True, "database_path": "data/orion_memory.sqlite"},
            "voice": {"enabled": False, "wake_word": "Orion"},
            "safety": {
                "require_approval": True,
                "dangerous_actions": ["delete_file", "restart_service", "shutdown", "run_shell_command"],
            },
            "plugins": {"path": "plugins", "enabled": True},
            "weather": {"enabled": weather_enabled, "location": "", "units": "imperial", "timeout_seconds": 5},
            "calendar": {
                "enabled": calendar_enabled,
                "timezone": "",
                "google": {
                    "enabled": google_enabled, "name": "Personal Google", "calendar_id": "primary",
                    "credentials_path": "config/google-calendar-credentials.json",
                    "token_path": ".orion/google-calendar-token.json",
                },
                "microsoft": {
                    "enabled": microsoft_enabled, "name": "Personal Outlook", "client_id": "",
                    "tenant": "common", "token_path": ".orion/microsoft-calendar-token.json",
                    "timeout_seconds": 10,
                },
            },
            "email": {"enabled": email_choice != "Not now", "provider": email_choice.lower().replace(" ", "_")},
            "docker": {"enabled": docker_enabled},
            "onboarding": {"completed": True, "experience": "First Contact"},
        }

    @staticmethod
    def _backup_existing(path: Path) -> None:
        if not path.exists():
            return
        backup = path.with_suffix(path.suffix + ".before-first-contact")
        copy2(path, backup)

    @staticmethod
    def _write_yaml(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        temporary.replace(path)
