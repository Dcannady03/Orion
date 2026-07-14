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
from orion.services.workspace import WorkspaceManager
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
        self.brain = Brain(
            ai_provider=self.ai_provider,
            config_manager=self.config_manager,
            profile_manager=self.profile_manager,
            memory=self.session_memory,
            services=self.services,
        )
        self.router = CommandRouter(self)
        self.console = Console(self)

    def start(self):
        """Start Orion and enter the polished Companion command loop."""
        self.banner()
        hour = datetime.now().hour
        greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        print(f"{greeting}, {self.user_name}.")
        print()
        self.console.success("Configuration loaded")
        self.console.success("User profile loaded")
        self.console.success(f"Workspace ready: {self.workspace_manager.root.name}")
        self.console.success(f"AI provider ready: {self.ai_provider.name()}")
        app_count = len(self.application_catalog.applications())
        self.console.success(f"Application library ready: {app_count} discovered")
        self.console.success(f"Trust settings loaded: {len(self.action_trust.entries())} trusted")
        print()
        print("System ready. What would you like to do today?")
        print("=" * 50)
        self.command_loop()

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
