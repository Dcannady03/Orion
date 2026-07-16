"""
Orion Core

Responsible for:
- Booting Orion
- Initializing core services
- Displaying startup information
- Running the main command loop

Author:
Daniel Cannady

Project:
Orion AI Operating System
"""

from orion.core.config import ConfigManager
from orion.core.profile import ProfileManager
from orion.core.router import CommandRouter
from orion.intelligence.factory import AIProviderFactory
from orion.intelligence.brain import Brain
from orion.services.registry import ServiceRegistry
from orion.services.briefing import BriefingService, SystemBriefingProvider
from orion.services.home import HomeService
from orion.services.weather import WeatherBriefingProvider, WeatherService
from orion.services.calendar import (
    CalendarBriefingProvider, CalendarProvider, CalendarService,
    GoogleCalendarClient, MicrosoftCalendarClient,
)
from orion.services.workspace import WorkspaceManager
from orion.services.ai_control import AIControlService
from orion.services.provider_manager import ProviderManager
from orion.services.vault import VaultService
from orion.services.connect import ConnectService, ConnectBriefingProvider, GmailClient, DiscordWebhookClient
from orion.services.request_router import RequestRouterService
from orion.services.project_context import ProjectContext
from orion.services.companion import CompanionSettings, ActionTrustStore
from orion.services.discovery import (
    ApplicationCatalog, ApplicationDiscoveryService, ApplicationMatcher, ApplicationLauncherService,
)
from orion.memory.session import SessionMemory
from orion.conversation import ConversationService
from orion.knowledge import KnowledgeIndex
from orion.plugins.manager import PluginManager
from orion.actions import ActionHistory, ActionService, PolicyDecision
from orion.ui.console import Console
from datetime import datetime
import importlib
import subprocess
import sys
from orion.interfaces.discord import DiscordBotInterface


class Orion:
    """Main Orion application."""

    def __init__(self):
        # Load configuration
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load()

        # Load user profile
        self.profile_manager = ProfileManager()
        self.profile = self.profile_manager.load()

        # Orion information
        self.name = self.config_manager.get("orion.name", "Orion")
        self.version = self.config_manager.get("orion.version", "0.0.1")
        self.codename = self.config_manager.get(
            "orion.codename",
            "First Light"
        )

        # User profile
        self.user_name = self.profile_manager.name

        # System status
        self.status = "READY"

        # Shared service registry
        self.services = ServiceRegistry()

        # Phase 2 services and skills
        workspace_root = self.config_manager.get("workspace.default_path", ".")
        self.workspace_manager = self.services.register(
            "workspace", WorkspaceManager(workspace_root)
        )
        self.session_memory = self.services.register(
            "session_memory", SessionMemory()
        )
        self.conversation = self.services.register(
            "conversation", ConversationService(self.workspace_manager.root)
        )
        self.code_skill = None
        self.search_skill = None
        self.project_context = self.services.register(
            "project_context", ProjectContext(self.workspace_manager.root)
        )
        self.knowledge_index = self.services.register(
            "knowledge_index", KnowledgeIndex(self.workspace_manager.root)
        )
        self.action_history = ActionHistory(self.workspace_manager.root)
        self.action_service = self.services.register(
            "actions", ActionService(self.action_history)
        )
        self.companion_settings = self.services.register(
            "companion_settings", CompanionSettings(self.workspace_manager.root)
        )
        self.action_trust = self.services.register(
            "action_trust", ActionTrustStore(self.workspace_manager.root)
        )
        self.action_service.register_handler(
            "echo", lambda action: action.parameters.get("message", "")
        )
        self.action_service.register_handler(
            "protected_echo", lambda action: action.parameters.get("message", "")
        )
        self.action_service.approval.set_policy(
            "protected_echo", PolicyDecision.REQUIRE_APPROVAL,
            "Protected demonstration actions require explicit approval.",
        )

        # Phase 3 application discovery and launch services. The catalog is
        # generated from Windows Start Menu/Desktop shortcuts and augmented by
        # user aliases rather than a hardcoded application list.
        self.application_catalog = self.services.register(
            "application_catalog", ApplicationCatalog(self.workspace_manager.root)
        )
        self.discovery_service = self.services.register(
            "discovery", ApplicationDiscoveryService(self.application_catalog)
        )
        self.application_matcher = self.services.register(
            "application_matcher", ApplicationMatcher(self.application_catalog)
        )
        self.application_launcher = self.services.register(
            "application_launcher", ApplicationLauncherService(self.application_matcher)
        )

        # Morning Star briefing providers contribute independently. Startup only
        # knows how to render the resulting briefing, not where facts came from.
        self.briefing_service = self.services.register("briefing", BriefingService())
        self.briefing_service.register_provider(SystemBriefingProvider(self))

        # Weather is Orion's first external information service. It uses the
        # profile location by default and contributes through Morning Star's
        # provider contract. Network failures are isolated by BriefingService.
        weather_location = (
            self.config_manager.get("weather.location", "")
            or self.profile_manager.get("location", "")
        )
        weather_units = self.config_manager.get("weather.units", "imperial")
        weather_timeout = float(self.config_manager.get("weather.timeout_seconds", 5.0))
        from orion.services.weather import OpenMeteoClient
        self.weather_service = self.services.register(
            "weather", WeatherService(
                weather_location,
                units=weather_units,
                client=OpenMeteoClient(timeout=weather_timeout),
                user_name=self.user_name,
            ),
        )
        self.briefing_service.register_provider(WeatherBriefingProvider(self.weather_service))

        # Constellation keeps Calendar provider-neutral. Google and Microsoft
        # may be enabled independently and their events are merged by time.
        calendar_enabled = bool(self.config_manager.get("calendar.enabled", False))
        calendar_timezone = self.config_manager.get("calendar.timezone", "") or self.profile_manager.timezone
        calendar_providers = []

        google_enabled = bool(self.config_manager.get("calendar.google.enabled", calendar_enabled))
        calendar_providers.append(CalendarProvider(
            "google",
            self.config_manager.get("calendar.google.name", "Personal Google"),
            GoogleCalendarClient(
                self.config_manager.get("calendar.google.credentials_path", "config/google-calendar-credentials.json"),
                self.config_manager.get("calendar.google.token_path", ".orion/google-calendar-token.json"),
            ),
            self.config_manager.get("calendar.google.calendar_id", "primary"),
            google_enabled,
        ))

        microsoft_enabled = bool(self.config_manager.get("calendar.microsoft.enabled", False))
        calendar_providers.append(CalendarProvider(
            "microsoft",
            self.config_manager.get("calendar.microsoft.name", "Personal Outlook"),
            MicrosoftCalendarClient(
                self.config_manager.get("calendar.microsoft.client_id", ""),
                self.config_manager.get("calendar.microsoft.token_path", ".orion/microsoft-calendar-token.json"),
                tenant=self.config_manager.get("calendar.microsoft.tenant", "common"),
                timeout=float(self.config_manager.get("calendar.microsoft.timeout_seconds", 10.0)),
            ),
            enabled=microsoft_enabled,
        ))

        self.calendar_service = self.services.register(
            "calendar", CalendarService(
                enabled=calendar_enabled,
                timezone=calendar_timezone,
                providers=calendar_providers,
                user_name=self.user_name,
                provider_state_writer=self._save_calendar_provider_state,
                provider_config_writer=self._save_calendar_provider_config,
            ),
        )
        self.briefing_service.register_provider(CalendarBriefingProvider(self.calendar_service))
        self.action_service.register_handler(
            "open_app",
            lambda action: self.application_launcher.launch(
                action.parameters.get("name", ""),
                allow_search_fallback=action.parameters.get("allow_search_fallback", True),
            ),
        )
        self.action_service.approval.set_policy(
            "open_app", PolicyDecision.REQUIRE_APPROVAL,
            "Launching applications requires explicit approval.",
        )

        # Plugin system. Plugins may register services and commands without
        # modifying Orion's core.
        plugin_root = self.config_manager.get("plugins.path", "plugins")
        self.plugin_manager = PluginManager(self, plugin_root)
        self.plugin_manager.load_all()

        # Core systems
        self.ai_provider = AIProviderFactory(self.config_manager).create()
        self.ai_control = self.services.register(
            "ai_control", AIControlService(self.ai_provider, self.config_manager)
        )
        self.brain = Brain(
            ai_provider=self.ai_provider,
            config_manager=self.config_manager,
            profile_manager=self.profile_manager,
            memory=self.session_memory,
            services=self.services,
        )
        self.request_router = self.services.register(
            "request_router", RequestRouterService(
                self.brain, self.weather_service, self.calendar_service
            )
        )
        self.provider_manager = self.services.register(
            "provider_manager", ProviderManager(self, self.config_manager)
        )
        self.vault = self.services.register(
            "vault", VaultService(self.config_manager, self.provider_manager, self.provider_manager.secrets)
        )
        self.vault.migrate_legacy_store()

        # Orion Connect unifies communication services behind one center.
        self.connect_service = self.services.register(
            "connect",
            ConnectService(
                GmailClient(
                    self.config_manager.get("connect.gmail.credentials_path", "config/google-gmail-credentials.json"),
                    self.config_manager.get("connect.gmail.token_path", ".orion/google-gmail-token.json"),
                ),
                DiscordWebhookClient(
                    self.vault.store.get("discord"),
                    timeout=float(self.config_manager.get("connect.discord.timeout_seconds", 10.0)),
                ),
                vault=self.vault,
            ),
        )
        self.briefing_service.register_provider(ConnectBriefingProvider(self.connect_service))

        # Home Center is initialized after every briefing provider is registered so
        # its snapshots include System, Weather, Calendar, and Connect cards.
        self.home_service = self.services.register(
            "home", HomeService(self, self.briefing_service)
        )

        self.discord_interface = None
        self.router = CommandRouter(self)
        self.console = Console(self)

    def _save_calendar_provider_state(self, provider_key: str, enabled: bool) -> None:
        self.config_manager.set("calendar.enabled", True)
        self.config_manager.set(f"calendar.{provider_key}.enabled", enabled)
        self.config_manager.save()

    def _save_calendar_provider_config(self, provider_key: str, field: str, value: str) -> None:
        self.config_manager.set(f"calendar.{provider_key}.{field}", value)
        self.config_manager.save()

    def start(self, *, discord: bool = False):
        """Start Orion and enter the polished Companion command loop."""
        if discord:
            self.start_discord_interface()
        self.console.render_home(self.home_service.build(), developer_mode=self.companion_settings.developer_mode)
        self.command_loop()


    def start_discord_interface(self):
        """Start the optional two-way Discord gateway beside the CLI."""
        token = self.vault.store.get("discord_bot")
        owners = self.config_manager.get(
            "connect.discord_bot.owner_user_ids",
            self.config_manager.get("connect.discord_bot.allowed_user_ids", []),
        )
        channels = self.config_manager.get("connect.discord_bot.allowed_channel_ids", [])
        roles = self.config_manager.get("connect.discord_bot.allowed_role_ids", [])
        allow_channel_members = bool(
            self.config_manager.get("connect.discord_bot.allow_channel_members", False)
        )
        interface = DiscordBotInterface(
            self,
            token,
            owners,
            channels,
            roles,
            allow_channel_members,
        )
        try:
            interface.start()
        except RuntimeError as exc:
            if "discord.py" not in str(exc):
                print(f"[WARN] Discord interface could not start: {exc}")
                return None
            print("\nDiscord Interface")
            print("-" * 50)
            print("The Discord interface requires the optional package: discord.py")
            try:
                answer = input("Would you like Orion to install it now? [Y/n]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer not in {"", "y", "yes"}:
                print("Discord was not started. Install later with:")
                print(f'  "{sys.executable}" -m pip install -U discord.py')
                return None
            print("Installing discord.py...")
            completed = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-U", "discord.py"],
                check=False,
            )
            if completed.returncode != 0:
                print("[WARN] Installation failed. Orion will continue without Discord.")
                print(f'Try manually: "{sys.executable}" -m pip install -U discord.py')
                return None
            importlib.invalidate_caches()
            print("[OK] discord.py installed.")
            try:
                interface.start()
            except Exception as retry_exc:
                print(f"[WARN] Discord still could not start: {retry_exc}")
                print("Restart Orion and try again.")
                return None
        except Exception as exc:
            print(f"[WARN] Discord interface could not start: {exc}")
            return None
        self.discord_interface = interface
        return interface

    def command_loop(self):
        """Run Orion's interactive command loop with history and completion."""
        running = True
        while running:
            try:
                user_input = self.console.prompt(self.name)
            except (EOFError, KeyboardInterrupt):
                print("\nShutting down Orion.")
                break
            running = self.router.handle(user_input)

    def banner(self):
        """Displays the Orion startup banner."""
        print("=" * 50)
        print(f"{self.name:^50}")
        print("=" * 50)
        print(f"Version : {self.version}")
        print(f"Codename: {self.codename}")
        print()
