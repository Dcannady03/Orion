"""Guided, provider-neutral first-run setup for Orion."""

from __future__ import annotations

from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from shutil import copy2
from typing import Callable

import yaml

from orion.core.config import ConfigManager
from orion.core.paths import OrionPaths
from orion.intelligence.secrets import SecretStore
from orion.services.ai_routing import AIRoutingService
from orion.services.execution_engines import ExecutionEngineService
from orion.services.email import build_email_service
from orion.services.provider_manager import ProviderManager
from orion.services.vault import VaultService


InputProvider = Callable[[str], str]
OutputProvider = Callable[[str], None]


@dataclass(frozen=True)
class FirstContactResult:
    completed: bool
    config_path: Path
    profile_path: Path
    workspace_path: Path


class FirstContact:
    """Configure Orion through its normal profile, provider, Vault, and routing services."""

    AI_OPTIONS = (
        "Ollama / local AI",
        "OpenAI",
        "Google Gemini",
        "Multiple providers",
        "Skip for now",
    )
    PROVIDER_LABELS = {
        "ollama": "Ollama",
        "openai": "OpenAI",
        "gemini": "Google Gemini",
    }

    def __init__(
        self,
        config_path: str | Path | None = None,
        profile_path: str | Path | None = None,
        *,
        input_provider: InputProvider = input,
        secret_input_provider: InputProvider = getpass,
        output_provider: OutputProvider = print,
        config_manager=None,
        provider_manager=None,
        vault=None,
        routing_service=None,
        execution_engines=None,
        email_service=None,
    ) -> None:
        self.paths = OrionPaths()
        self.paths.ensure()
        default_config = config_path is None
        self.config_path = Path(config_path) if config_path is not None else self.paths.config
        self.profile_path = Path(profile_path) if profile_path is not None else self.paths.profile
        self.input = input_provider
        self.secret_input = secret_input_provider
        self.output = output_provider

        self.config_manager = config_manager or ConfigManager(
            self.paths.defaults,
            local_config_path=self.config_path,
        )
        if not getattr(self.config_manager, "config", None):
            self.config_manager.load()

        vault_path = self.paths.vault if default_config else self.config_path.parent / "vault" / "vault.yaml"
        self.vault = vault or VaultService(
            self.config_manager,
            store=SecretStore(vault_path),
        )
        self.provider_manager = provider_manager or ProviderManager(
            None,
            self.config_manager,
            self.vault.store,
        )
        if getattr(self.vault, "provider_manager", None) is None:
            self.vault.provider_manager = self.provider_manager
        self.routing = routing_service or AIRoutingService(
            self.config_manager,
            self.provider_manager,
        )
        self.execution_engines = execution_engines or ExecutionEngineService(
            self.config_manager
        )
        if email_service is None:
            email_paths = self.paths if default_config else OrionPaths(
                user_root=self.config_path.parent / "user"
            )
            email_paths.ensure()
            email_service = build_email_service(self.config_manager, email_paths)
        self.email_service = email_service

    @property
    def is_required(self) -> bool:
        """Return True when Orion cannot perform a normal personalized boot."""
        if not self.config_path.exists() or not self.profile_path.exists():
            return True
        profile = self._read_profile()
        return not bool(profile.get("preferred_name") or profile.get("name"))

    def run(self, *, force: bool = False) -> FirstContactResult:
        if not force and not self.is_required:
            return FirstContactResult(False, self.config_path, self.profile_path, Path("."))
        try:
            return self._run()
        except (EOFError, KeyboardInterrupt):
            self.output("")
            self.output("First Contact cancelled. Existing Orion settings were preserved.")
            workspace = Path(str(self.config_manager.get("workspace.default_path", ".")))
            return FirstContactResult(False, self.config_path, self.profile_path, workspace)

    def _run(self) -> FirstContactResult:
        existing_profile = self._read_profile()
        self._show_welcome()

        existing_name = str(
            existing_profile.get("preferred_name") or existing_profile.get("name") or ""
        ).strip()
        name = self._ask(
            "What should I call you?",
            default=existing_name or None,
            required=True,
        )
        full_name = self._ask(
            "What is your full name?",
            default=str(existing_profile.get("name") or name),
        )
        location = self._ask(
            "Where are you located? (city, state/country)",
            default=str(existing_profile.get("location") or "") or None,
            required=True,
        )
        timezone = self._ask(
            "What is your timezone?",
            default=str(existing_profile.get("timezone") or "America/Los_Angeles"),
        )
        language = self._ask(
            "Preferred language",
            default=str(existing_profile.get("language") or "English"),
        )
        use_options = (
            "Personal assistant",
            "Software development",
            "Home automation",
            "A mix of everything",
        )
        intended_use = self._choose(
            "How will you primarily use Orion?",
            use_options,
            default=self._option_default(
                use_options,
                str(existing_profile.get("intended_use") or "A mix of everything"),
                4,
            ),
        )

        current_workspace = str(
            self.config_manager.get("workspace.default_path", str(Path.cwd()))
        )
        workspace_text = self._ask("Default workspace folder", default=current_workspace)
        workspace = Path(workspace_text).expanduser()

        ai_choice = self._choose(
            "Choose your AI setup",
            self.AI_OPTIONS,
            default=self._ai_default(),
        )
        selected_providers = self._selected_providers(ai_choice)

        weather_enabled = self._yes_no(
            "Enable live weather?",
            default=bool(self.config_manager.get("weather.enabled", True)),
        )
        calendar_options = ("Not now", "Google Calendar", "Microsoft Outlook", "Both")
        calendar_choice = self._choose(
            "Connect a calendar now?",
            calendar_options,
            default=self._option_default(
                calendar_options,
                self._calendar_choice(),
                1,
            ),
        )
        email_options = ("Not now", "Gmail", "Outlook / Microsoft 365", "Both")
        email_choice = self._choose(
            "Prepare email integration?",
            email_options,
            default=self._option_default(email_options, self._email_choice(), 1),
        )
        docker_enabled = self._yes_no(
            "Prepare Docker integration?",
            default=bool(self.config_manager.get("docker.enabled", False)),
        )

        self._show_review(
            name=name,
            location=location,
            timezone=timezone,
            workspace=workspace,
            ai_setup=ai_choice,
            weather=weather_enabled,
            calendar=calendar_choice,
            email=email_choice,
            docker=docker_enabled,
        )
        if not self._yes_no("Apply these First Contact changes?", default=True):
            self.output("First Contact cancelled. No files were changed.")
            return FirstContactResult(False, self.config_path, self.profile_path, workspace)

        profile = dict(existing_profile)
        profile.update({
            "name": full_name,
            "preferred_name": name,
            "timezone": timezone,
            "location": location,
            "language": language,
            "intended_use": intended_use,
            "onboarding_complete": True,
        })
        self._apply_non_ai_config(
            workspace=workspace,
            weather_enabled=weather_enabled,
            calendar_choice=calendar_choice,
            email_choice=email_choice,
            docker_enabled=docker_enabled,
        )
        self._backup_existing(self.config_path)
        self._backup_existing(self.profile_path)
        self.config_manager.save()
        self._write_yaml(self.profile_path, profile)
        workspace.mkdir(parents=True, exist_ok=True)

        connected: set[str] = set()
        for provider in selected_providers:
            if provider == "ollama":
                if self._setup_ollama():
                    connected.add(provider)
            elif self._setup_cloud_provider(provider):
                connected.add(provider)

        self._setup_email(email_choice)

        if ai_choice != "Skip for now":
            self._select_active_provider(selected_providers, connected)
        if ai_choice == "Multiple providers":
            self._select_routing_profile()

        self.config_manager.set("onboarding.completed", True)
        self.config_manager.set("onboarding.experience", "First Contact")
        self.config_manager.save()

        self._show_final_summary(workspace)
        self.output("")
        self.output("First Contact complete.")
        self.output(f"Welcome to Orion, {name}.")
        self.output("Use 'ai providers', 'vault health', or 'execution status' at any time.")
        self.output("")
        return FirstContactResult(True, self.config_path, self.profile_path, workspace)

    def _selected_providers(self, choice: str) -> tuple[str, ...]:
        mapping = {
            "Ollama / local AI": ("ollama",),
            "OpenAI": ("openai",),
            "Google Gemini": ("gemini",),
            "Skip for now": (),
        }
        if choice != "Multiple providers":
            return mapping[choice]
        while True:
            selected = tuple(
                provider
                for provider in ("ollama", "openai", "gemini")
                if self._yes_no(
                    f"Include {self.PROVIDER_LABELS[provider]}?",
                    default=self._provider_enabled(provider),
                )
            )
            if len(selected) >= 2 or not selected:
                return selected
            self.output("Choose at least two providers for multiple-provider setup, or none to skip.")

    def _setup_ollama(self) -> bool:
        self.output("")
        self.output("Ollama / local AI")
        current_url = str(
            self.config_manager.get("providers.ollama.base_url", "http://localhost:11434")
        )
        base_url = self._ask("Ollama address", default=current_url)
        self.output("Scanning Ollama for installed models...")
        try:
            models = self.provider_manager.preview_models("ollama", base_url=base_url)
        except (ConnectionError, OSError, TimeoutError, ValueError):
            self.config_manager.set("providers.ollama.enabled", True)
            self.config_manager.set("providers.ollama.base_url", base_url)
            self.config_manager.save()
            self.output("Ollama is currently unavailable. First Contact will continue.")
            self.output("Existing provider and model settings were preserved.")
            return False
        current_model = str(self.config_manager.get("providers.ollama.model", ""))
        selected_model = self._choose_model("Ollama", models, current_model)
        self.config_manager.set("providers.ollama.enabled", True)
        self.config_manager.set("providers.ollama.base_url", base_url)
        if selected_model:
            self.config_manager.set("providers.ollama.model", selected_model)
        self.config_manager.save()
        self.output(f"[OK] Ollama connected with {len(models)} installed model(s).")
        return True

    def _setup_cloud_provider(self, provider: str) -> bool:
        label = self.PROVIDER_LABELS[provider]
        self.output("")
        self.output(label)
        existing = bool(self.vault.store.get(provider))
        if existing and self._yes_no(
            f"Keep and verify the existing {label} credential?",
            default=True,
        ):
            try:
                models = self.provider_manager.test_connection(provider)
            except (ConnectionError, OSError, TimeoutError, ValueError):
                self.output(f"{label} could not be verified right now.")
                self.output("The existing credential and active provider were preserved.")
                return False
            current_model = str(self.config_manager.get(f"providers.{provider}.model", ""))
            selected_model = self._choose_model(label, models, current_model)
            self.config_manager.set(f"providers.{provider}.enabled", True)
            if selected_model and selected_model != current_model:
                self.config_manager.set(f"providers.{provider}.model", selected_model)
            self.config_manager.save()
            self.output(f"[OK] Existing {label} connection verified.")
            return True

        self.output("The API key is hidden, verified in memory, and saved only through Orion Vault.")
        api_key = self.secret_input(f"{label} API key (blank cancels): ").strip()
        if not api_key:
            self.output(f"{label} setup cancelled. Existing settings were preserved.")
            return False
        try:
            verified = self.vault.verify_provider(provider, api_key)
        except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
            self.output(f"Could not connect {label}: {exc}")
            self.output("Existing credentials and active provider were preserved.")
            return False
        current_model = str(self.config_manager.get(f"providers.{provider}.model", ""))
        selected_model = self._choose_model(label, list(verified.models), current_model)
        try:
            self.vault.commit_provider(verified, model=selected_model or current_model)
        except (OSError, TypeError, ValueError):
            self.output(f"Could not save {label} in Orion Vault.")
            self.output("Existing credentials and active provider were preserved.")
            return False
        self.output(f"[OK] {label} verified and saved in Orion Vault.")
        return True

    def _choose_model(self, label: str, models: list[str] | tuple[str, ...], current: str) -> str:
        available = [str(model) for model in models if str(model).strip()]
        if not available:
            self.output(f"{label} returned no compatible models; keeping {current or 'the existing default'}.")
            return current
        self.output(f"Available {label} models:")
        for index, model in enumerate(available, start=1):
            marker = " [current]" if model == current else ""
            self.output(f"  {index}. {model}{marker}")
        default = available.index(current) + 1 if current in available else 1
        selected = self._choose(f"Choose the default {label} model", tuple(available), default=default)
        return selected

    def _select_active_provider(self, selected: tuple[str, ...], connected: set[str]) -> None:
        candidates = tuple(provider for provider in selected if provider in connected)
        if not candidates:
            self.output("No newly selected AI provider was verified; the previous active provider was preserved.")
            return
        current = str(self.config_manager.get("providers.default", "ollama")).lower()
        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            labels = tuple(self.PROVIDER_LABELS[item] for item in candidates)
            chosen_label = self._choose(
                "Choose Orion's initial active provider",
                labels,
                default=(candidates.index(current) + 1 if current in candidates else 1),
            )
            chosen = candidates[labels.index(chosen_label)]
        try:
            active = self.provider_manager.activate(chosen)
        except (ConnectionError, OSError, ValueError):
            self.output("The selected provider could not be activated; the previous provider was preserved.")
            return
        self.output(f"[OK] Active AI: {active.name()}")

    def _select_routing_profile(self) -> None:
        names = tuple(self.routing.PROFILES)
        current = str(self.config_manager.get("ai.routing.profile", "balanced")).lower()
        selected = self._choose(
            "Choose an AI routing profile",
            tuple(name.title() for name in names),
            default=(names.index(current) + 1 if current in names else names.index("balanced") + 1),
        ).lower()
        self.routing.set_profile(selected)
        self.output(f"[OK] AI routing profile: {selected.title()}")

    def _apply_non_ai_config(
        self,
        *,
        workspace: Path,
        weather_enabled: bool,
        calendar_choice: str,
        email_choice: str,
        docker_enabled: bool,
    ) -> None:
        google_enabled = calendar_choice in {"Google Calendar", "Both"}
        microsoft_enabled = calendar_choice in {"Microsoft Outlook", "Both"}
        self.config_manager.set("workspace.default_path", str(workspace))
        self.config_manager.set("weather.enabled", weather_enabled)
        self.config_manager.set("calendar.enabled", google_enabled or microsoft_enabled)
        self.config_manager.set("calendar.google.enabled", google_enabled)
        self.config_manager.set("calendar.microsoft.enabled", microsoft_enabled)
        self.config_manager.set("docker.enabled", docker_enabled)

    def _setup_email(self, email_choice: str) -> None:
        selected = []
        if email_choice in {"Gmail", "Both"}:
            selected.append("gmail")
        if email_choice in {"Outlook / Microsoft 365", "Both"}:
            selected.append("microsoft")
        for key in selected:
            provider = self.email_service.providers[key]
            if provider.enabled and provider.adapter.connected:
                self.output(f"[OK] {provider.display_name} Mail authorization preserved.")
                continue
            if key == "gmail" and not provider.adapter.configured:
                default = str(provider.adapter.credentials_path)
                entered = self._ask(
                    "Google Desktop OAuth client file for Gmail",
                    default=default,
                )
                try:
                    self.email_service.configure_provider("gmail", credentials_path=entered)
                except (OSError, ValueError):
                    self.output("Gmail OAuth client configuration could not be saved.")
                    continue
                if not provider.adapter.configured:
                    self.output(
                        "Gmail setup skipped because the Google OAuth client file is not available."
                    )
                    continue
            if key == "microsoft" and not provider.adapter.configured:
                client_id = self._ask(
                    "Microsoft Application (client) ID for Mail",
                    default=None,
                )
                if not client_id:
                    self.output(
                        "Microsoft Mail setup skipped because an Entra Application client ID is required."
                    )
                    continue
                tenant = self._ask(
                    "Microsoft tenant",
                    default=str(getattr(provider.adapter.oauth, "tenant", "common")),
                )
                try:
                    self.email_service.configure_provider(
                        "microsoft", client_id=client_id, tenant=tenant
                    )
                except (OSError, ValueError):
                    self.output("Microsoft Mail client configuration could not be saved.")
                    continue
            if key == "gmail" and bool(self.config_manager.get("calendar.google.enabled", False)):
                self.output("Google Calendar is configured; Gmail requires separate explicit read-only consent.")
            if key == "microsoft" and bool(self.config_manager.get("calendar.microsoft.enabled", False)):
                self.output("Microsoft Calendar is configured; Mail.Read requires separate explicit consent.")
            self.output(f"Connecting {provider.display_name} with read-only Mail permission...")
            try:
                account = self.email_service.connect(key)
            except (ConnectionError, OSError, ValueError):
                self.output(
                    f"{provider.display_name} Mail could not be connected; existing Email and Calendar "
                    "settings were preserved."
                )
                continue
            self.output(f"[OK] {provider.display_name} Mail connected: {account.email_address}")

    def _show_welcome(self) -> None:
        self.output("=" * 58)
        self.output(f"{'ORION — FIRST CONTACT':^58}")
        self.output("=" * 58)
        self.output("Hello. I am Orion.")
        self.output("First Contact can configure local AI, cloud AI, or both.")
        self.output("Cloud credentials are verified and stored only in Orion Vault.")
        self.output("Press Enter to keep the displayed value.")
        self.output("")

    def _show_review(self, **values) -> None:
        self.output("")
        self.output("First Contact changes")
        self.output("-" * 58)
        labels = {
            "name": "Name",
            "location": "Location",
            "timezone": "Timezone",
            "workspace": "Workspace",
            "ai_setup": "AI setup",
            "weather": "Weather",
            "calendar": "Calendar",
            "email": "Email",
            "docker": "Docker",
        }
        for key, value in values.items():
            if isinstance(value, bool):
                value = "Enabled" if value else "Not enabled"
            self.output(f"{labels[key]:<12}: {value}")
        self.output("-" * 58)

    def _show_final_summary(self, workspace: Path) -> None:
        active = str(self.config_manager.get("providers.default", "ollama")).lower()
        model = str(self.config_manager.get(f"providers.{active}.model", "Not selected"))
        connected = [
            item
            for item in self.provider_manager.statuses()
            if item.enabled and item.configured and item.key != active
        ]
        other = ", ".join(
            f"{self.PROVIDER_LABELS[item.key]} ({item.model or 'default model'})"
            for item in connected
        ) or "None"
        services = self._configured_services()

        self.output("")
        self.output("Orion is ready")
        self.output("-" * 58)
        self.output(f"Active AI   : {self.PROVIDER_LABELS.get(active, active.title())} ({model})")
        self.output(f"Other connected AI: {other}")
        profile = str(self.config_manager.get("ai.routing.profile", "balanced")).title()
        self.output(f"Routing     : {profile}")
        self.output(f"Workspace   : {workspace}")
        self.output(f"Services    : {', '.join(services) if services else 'None'}")
        self.output("Execution engines:")
        try:
            engines = self.execution_engines.status()
        except (OSError, TypeError, ValueError):
            engines = ()
        codex_ready = any(
            engine.engine_id == "codex" and engine.ready_for_implementation
            for engine in engines
        )
        for engine in engines:
            if engine.engine_id == "chatgpt_desktop":
                state = "Installed (desktop app only; not a CLI execution engine)" if engine.installed else "Not Installed"
            elif engine.engine_id == "codex_desktop":
                if engine.installed and codex_ready:
                    state = "Installed (separate CLI detected)"
                elif engine.installed:
                    state = "Installed (desktop app only)"
                else:
                    state = engine.status.replace("_", " ").title()
            elif engine.ready_for_implementation:
                state = "Ready"
            else:
                state = engine.status.replace("_", " ").title()
            self.output(f"  {engine.name}: {state}")
        self.output("Note: ChatGPT Desktop is not a CLI execution engine.")
        self.output("-" * 58)

    def _configured_services(self) -> tuple[str, ...]:
        services: list[str] = []
        if bool(self.config_manager.get("weather.enabled", False)):
            services.append("Weather")
        if bool(self.config_manager.get("calendar.google.enabled", False)):
            services.append("Google Calendar")
        if bool(self.config_manager.get("calendar.microsoft.enabled", False)):
            services.append("Microsoft Outlook Calendar")
        if bool(self.config_manager.get("email.gmail.enabled", False)):
            services.append("Gmail (read-only)")
        if bool(self.config_manager.get("email.microsoft.enabled", False)):
            services.append("Outlook / Microsoft 365 Mail (read-only)")
        if bool(self.config_manager.get("docker.enabled", False)):
            services.append("Docker")
        if bool(self.config_manager.get("connect.discord_bot.enabled", False)) and bool(
            self.vault.store.get("discord_bot")
        ):
            services.append("Discord Bot")
        return tuple(services)

    def _read_profile(self) -> dict:
        if not self.profile_path.exists():
            return {}
        try:
            value = yaml.safe_load(self.profile_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        return value if isinstance(value, dict) else {}

    def _provider_enabled(self, provider: str) -> bool:
        if provider == "ollama":
            return bool(self.config_manager.get("providers.ollama.enabled", True))
        return bool(
            self.config_manager.get(f"providers.{provider}.enabled", False)
            and self.vault.store.get(provider)
        )

    def _ai_default(self) -> int:
        connected = [key for key in ("ollama", "openai", "gemini") if self._provider_enabled(key)]
        if len(connected) > 1:
            return 4
        active = str(self.config_manager.get("providers.default", "ollama")).lower()
        return {"ollama": 1, "openai": 2, "gemini": 3}.get(active, 5)

    def _calendar_choice(self) -> str:
        google = bool(self.config_manager.get("calendar.google.enabled", False))
        microsoft = bool(self.config_manager.get("calendar.microsoft.enabled", False))
        if google and microsoft:
            return "Both"
        if google:
            return "Google Calendar"
        if microsoft:
            return "Microsoft Outlook"
        return "Not now"

    def _email_choice(self) -> str:
        gmail = bool(self.config_manager.get("email.gmail.enabled", False))
        microsoft = bool(self.config_manager.get("email.microsoft.enabled", False))
        if gmail and microsoft:
            return "Both"
        if gmail:
            return "Gmail"
        if microsoft:
            return "Outlook / Microsoft 365"
        # Preserve the legacy single-provider default during migration.
        if bool(self.config_manager.get("email.enabled", False)):
            provider = str(self.config_manager.get("email.provider", "gmail")).lower()
            return "Outlook / Microsoft 365" if "outlook" in provider else "Gmail"
        return "Not now"

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
            marker = " (default)" if index == default else ""
            self.output(f"  {index}. {option}{marker}")
        while True:
            answer = self.input(f"Choose [{default}]: ").strip()
            if not answer:
                return options[default - 1]
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
            self.output(f"Choose a number from 1 to {len(options)}.")

    @staticmethod
    def _option_default(options: tuple[str, ...], value: str, fallback: int) -> int:
        return options.index(value) + 1 if value in options else fallback

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
