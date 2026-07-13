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
from orion.memory.session import SessionMemory
from orion.conversation import ConversationService
from orion.knowledge import KnowledgeIndex
from orion.plugins.manager import PluginManager
from orion.actions import ActionHistory, ActionService


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
        self.action_service.register_handler(
            "echo", lambda action: action.parameters.get("message", "")
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

    def start(self):
        """Starts Orion and enters the command loop."""
        self.banner()

        print(f"Hello {self.user_name}.")
        print()
        print("Configuration Loaded.")
        print("User Profile Loaded.")
        print("System Initialized.")
        print(f"Status: {self.status}")
        print()
        print(f"Welcome to {self.name}.")
        print("=" * 50)

        self.command_loop()

    def command_loop(self):
        """Run Orion's interactive command loop."""
        running = True

        while running:
            user_input = input(f"\n{self.name}> ")
            running = self.router.handle(user_input)

    def banner(self):
        """Displays the Orion startup banner."""
        print("=" * 50)
        print(f"{self.name:^50}")
        print("=" * 50)
        print(f"Version : {self.version}")
        print(f"Codename: {self.codename}")
        print()
