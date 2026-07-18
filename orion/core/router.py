"""
Orion Command Router

Responsible for:
- Receiving user commands
- Routing commands to the correct Orion subsystem
- Keeping command handling out of main.py
"""

from getpass import getpass

from orion.agents import AgentDefinition, AgentPermissions
from orion.services.team import TeamPlanningError
from orion.services.codex_bridge import CodexBridgeError
from orion.services.execution_engines import ExecutionEngineUnavailable


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

        elif command_lower == "home":
            self.show_home()

        elif command_lower == "status":
            self.show_status()

        elif command_lower == "briefing":
            self.show_briefing()

        elif command_lower == "weather" or command_lower.startswith("weather "):
            self.show_weather(raw_command)

        elif command_lower == "calendar" or command_lower.startswith("calendar "):
            self.show_calendar(raw_command)

        elif command_lower in {"connect", "connect status"}:
            self.show_connect()

        elif command_lower in {"connect health", "connect test"}:
            self.connect_health()

        elif command_lower in {"connect add gmail", "email configure gmail"}:
            self.connect_gmail()

        elif command_lower in {"connect add discord", "discord configure"}:
            self.connect_discord()

        elif command_lower in {"connect add discord bot", "discord bot configure"}:
            self.connect_discord_bot()

        elif command_lower in {"discord bot status", "connect discord bot status"}:
            self.discord_bot_status()
        elif command_lower in {"connect debug", "discord debug"}:
            self.connect_debug()

        elif command_lower in {"connect enable discord bot", "discord bot enable"}:
            self.set_discord_bot_enabled(True)

        elif command_lower in {"connect disable discord bot", "discord bot disable"}:
            self.set_discord_bot_enabled(False)

        elif command_lower in {"email", "email inbox"}:
            self.email_inbox()

        elif command_lower == "email unread":
            self.email_unread()

        elif command_lower.startswith("email search "):
            self.email_search(raw_command[len("email search "):].strip())

        elif command_lower.startswith("email read "):
            self.email_read(raw_command[len("email read "):].strip())

        elif command_lower in {"email compose", "email send"}:
            self.email_compose()

        elif command_lower.startswith("discord send "):
            self.discord_send(raw_command[len("discord send "):].strip())

        elif command_lower in {"change ollama model", "ollama model", "ollama models", "ai models"}:
            self.change_ollama_model()

        elif command_lower in {"vault", "vault list"}:
            self.show_vault()

        elif command_lower.startswith("vault add "):
            self.vault_add(raw_command[len("vault add "):].strip())

        elif command_lower.startswith("vault remove "):
            self.vault_remove(raw_command[len("vault remove "):].strip())

        elif command_lower in {"vault health", "vault test"}:
            self.vault_health()

        elif command_lower == "ai providers":
            self.show_ai_providers()

        elif command_lower in {"ai connect openai", "ai configure openai", "ai enable openai", "openai connect", "openai configure"}:
            self.configure_ai_provider("openai")

        elif command_lower in {"ai test openai", "openai test"}:
            self.test_ai_provider("openai")

        elif command_lower in {"ai disconnect openai", "ai disable openai", "openai disconnect"}:
            self.disconnect_ai_provider("openai")

        elif command_lower.startswith("ai provider configure "):
            self.configure_ai_provider(raw_command[len("ai provider configure "):].strip())

        elif command_lower.startswith("ai provider use "):
            self.use_ai_provider(raw_command[len("ai provider use "):].strip())

        elif command_lower.startswith("ai provider models "):
            self.show_provider_models(raw_command[len("ai provider models "):].strip())

        elif command_lower == "ai route status":
            self.show_ai_route_status()

        elif command_lower == "ai route on":
            self.set_ai_routing(True)

        elif command_lower == "ai route off":
            self.set_ai_routing(False)

        elif command_lower in {"ai route explain", "ai route explain last"}:
            self.explain_last_ai_route()

        elif command_lower == "ai stats":
            self.show_ai_stats()

        elif command_lower == "ai stats clear":
            self.clear_ai_stats()

        elif command_lower == "ai health":
            self.show_ai_health()

        elif command_lower in {"ai", "ai status"}:
            self.show_ai_status()

        elif command_lower.startswith("ai use "):
            self.use_ai_model(raw_command[len("ai use "):].strip())

        elif command_lower.startswith("use "):
            self.use_ai_model(raw_command[len("use "):].strip())

        elif command_lower.startswith("switch to "):
            self.use_ai_model(raw_command[len("switch to "):].strip())

        elif command_lower == "ai profiles":
            self.show_ai_profiles()

        elif command_lower.startswith("ai profile "):
            self.activate_ai_profile(raw_command[len("ai profile "):].strip())

        elif command_lower in {"benchmark models", "ai benchmark"}:
            self.benchmark_ai_models()

        elif command_lower in {"agent", "agent list"}:
            self.show_agents()

        elif command_lower == "agent show":
            print("Usage: agent show <name>")

        elif command_lower.startswith("agent show "):
            self.show_agent(raw_command[len("agent show "):].strip())

        elif command_lower == "agent create":
            self.create_agent()

        elif command_lower == "agent enable":
            print("Usage: agent enable <name>")

        elif command_lower.startswith("agent enable "):
            self.set_agent_enabled(raw_command[len("agent enable "):].strip(), True)

        elif command_lower == "agent disable":
            print("Usage: agent disable <name>")

        elif command_lower.startswith("agent disable "):
            self.set_agent_enabled(raw_command[len("agent disable "):].strip(), False)

        elif command_lower == "agent test":
            print("Usage: agent test <name>")

        elif command_lower.startswith("agent test "):
            self.test_agent(raw_command[len("agent test "):].strip())

        elif command_lower == "team":
            self.show_team()

        elif command_lower == "team roles":
            self.show_team_roles()

        elif command_lower == "team plan":
            print('Usage: team plan "<goal>"')

        elif command_lower.startswith("team plan "):
            self.team_plan(raw_command[len("team plan "):].strip())

        elif command_lower == "team status":
            print("Usage: team status <task-id>")

        elif command_lower.startswith("team status "):
            self.team_status(raw_command[len("team status "):].strip())

        elif command_lower == "team approve":
            print("Usage: team approve <team-task-id>")

        elif command_lower.startswith("team approve "):
            self.team_approve(raw_command[len("team approve "):].strip())

        elif command_lower == "team implement":
            print("Usage: team implement <team-task-id> <approval-id>")

        elif command_lower.startswith("team implement "):
            self.team_implement(raw_command[len("team implement "):].strip())

        elif command_lower == "team run":
            print("Usage: team run <run-id>")

        elif command_lower.startswith("team run "):
            self.team_run_status(raw_command[len("team run "):].strip())

        elif command_lower in {"execution", "execution status"}:
            self.show_execution_status()

        elif command_lower == "config":
            self.show_config()

        elif command_lower == "profile":
            self.show_profile()

        elif command_lower in {"git", "git status"}:
            self.git_status()

        elif command_lower == "git log":
            self.git_log()

        elif command_lower in {"git diff", "git diff staged"}:
            self.git_diff(staged=command_lower.endswith("staged"))

        elif command_lower == "git pull":
            self.git_pull()

        elif command_lower == "git push":
            self.git_push()

        elif command_lower in {"update", "update check"}:
            self.update_check(apply=command_lower == "update")

        elif command_lower == "update rollback":
            self.update_rollback()

        elif command_lower == "services":
            self.show_services()

        elif command_lower == "plugins":
            self.show_plugins()

        elif command_lower.startswith("plugins info "):
            name = raw_command[len("plugins info "):].strip()
            self.show_plugin_info(name)

        elif command_lower == "workspace":
            self.show_workspace()

        elif command_lower.startswith("workspace "):
            path = raw_command[len("workspace "):].strip()
            self.set_workspace(path)

        elif command_lower in ["files", "ls"]:
            self.list_workspace()

        elif command_lower.startswith("files "):
            path = raw_command[len("files "):].strip()
            self.list_workspace(path)

        elif command_lower.startswith("ls "):
            path = raw_command[len("ls "):].strip()
            self.list_workspace(path)

        elif command_lower == "remember":
            print("Usage: remember <key> <value>")

        elif command_lower.startswith("remember "):
            payload = raw_command[len("remember "):].strip()
            self.remember(payload)

        elif command_lower == "recall":
            print("Usage: recall <key>")

        elif command_lower.startswith("recall "):
            key = raw_command[len("recall "):].strip()
            self.recall(key)

        elif command_lower == "memory":
            self.show_memory()

        elif command_lower == "forget":
            print("Usage: forget <key>")

        elif command_lower.startswith("forget "):
            key = raw_command[len("forget "):].strip()
            self.forget(key)

        elif command_lower == "clear memory":
            self.clear_memory()

        elif command_lower in {"task", "task list"}:
            self.task_list()

        elif command_lower == "task create":
            print('Usage: task create "<goal>"')

        elif command_lower.startswith("task create "):
            self.task_create(raw_command[len("task create "):].strip())

        elif command_lower == "task show":
            print("Usage: task show <task-id>")

        elif command_lower.startswith("task show "):
            self.task_show(raw_command[len("task show "):].strip())

        elif command_lower == "task approve":
            print("Usage: task approve <task-id>")

        elif command_lower.startswith("task approve "):
            self.task_approve(raw_command[len("task approve "):].strip())

        elif command_lower == "task cancel":
            print("Usage: task cancel <task-id>")

        elif command_lower.startswith("task cancel "):
            self.task_cancel(raw_command[len("task cancel "):].strip())

        elif command_lower == "task events":
            print("Usage: task events <task-id>")

        elif command_lower.startswith("task events "):
            self.task_events(raw_command[len("task events "):].strip())

        elif command_lower == "task link-plan":
            print("Usage: task link-plan <task-id> <team-task-id>")

        elif command_lower.startswith("task link-plan "):
            self.task_link_plan(raw_command[len("task link-plan "):].strip())

        elif command_lower == "project":
            print("Usage: project <init|status|info|set|note|checkpoint|resume|rule>")

        elif command_lower == "project init":
            self.project_init()

        elif command_lower in {"project status", "project info"}:
            self.project_status(verbose=command_lower.endswith("info"))

        elif command_lower.startswith("project set "):
            payload = raw_command[len("project set "):].strip()
            self.project_set(payload)

        elif command_lower == "project note":
            print("Usage: project note <text>")

        elif command_lower.startswith("project note "):
            text = raw_command[len("project note "):].strip()
            self.project_note(text)

        elif command_lower == "project checkpoint":
            print("Usage: project checkpoint <summary>")

        elif command_lower.startswith("project checkpoint "):
            self.project_checkpoint(raw_command[len("project checkpoint "):].strip())

        elif command_lower == "project resume":
            self.project_resume()

        elif command_lower in {"project rules", "project rule list"}:
            self.project_rules()

        elif command_lower == "project rule add":
            print("Usage: project rule add <rule>")

        elif command_lower.startswith("project rule add "):
            self.project_rule_add(raw_command[len("project rule add "):].strip())

        elif command_lower == "project rule remove":
            print("Usage: project rule remove <id>")

        elif command_lower.startswith("project rule remove "):
            self.project_rule_remove(raw_command[len("project rule remove "):].strip())

        elif command_lower == "index":
            print("Usage: index <build|status|find|classes|functions|todos|imports>")

        elif command_lower == "index build":
            self.index_build()

        elif command_lower == "index status":
            self.index_status()

        elif command_lower == "index classes":
            self.index_symbols("class")

        elif command_lower == "index functions":
            self.index_symbols("function")

        elif command_lower == "index todos":
            self.index_todos()

        elif command_lower == "index imports":
            self.index_imports()

        elif command_lower == "index find":
            print("Usage: index find <text>")

        elif command_lower.startswith("index find "):
            self.index_find(raw_command[len("index find "):].strip())

        elif command_lower == "action echo":
            print("Usage: action echo <text>")

        elif command_lower.startswith("action echo "):
            self.action_echo(raw_command[len("action echo "):].strip())

        elif command_lower == "action request":
            print("Usage: action request <text>")

        elif command_lower.startswith("action request "):
            self.action_request(raw_command[len("action request "):].strip())

        elif command_lower == "action pending":
            self.action_pending()

        elif command_lower == "action approve":
            print("Usage: action approve <id>")

        elif command_lower.startswith("action approve "):
            self.action_approve(raw_command[len("action approve "):].strip())

        elif command_lower == "action deny":
            print("Usage: action deny <id>")

        elif command_lower.startswith("action deny "):
            self.action_deny(raw_command[len("action deny "):].strip())

        elif command_lower == "action history":
            self.action_history()

        elif command_lower == "apps scan":
            self.apps_scan()

        elif command_lower in {"apps", "apps list"}:
            self.apps_list()

        elif command_lower == "apps find":
            print("Usage: apps find <name>")

        elif command_lower.startswith("apps find "):
            self.apps_find(raw_command[len("apps find "):].strip())

        elif command_lower == "app alias":
            print("Usage: app alias <alias> = <application name>")

        elif command_lower.startswith("app alias "):
            self.app_alias(raw_command[len("app alias "):].strip())

        elif command_lower in {"open", "launch"}:
            print("Usage: open <application>")

        elif command_lower.startswith("open "):
            self.open_app(raw_command[len("open "):].strip())

        elif command_lower.startswith("launch "):
            self.open_app(raw_command[len("launch "):].strip())

        elif command_lower == "developer on":
            self.set_developer_mode(True)

        elif command_lower == "developer off":
            self.set_developer_mode(False)

        elif command_lower in {"settings", "settings companion"}:
            self.show_companion_settings()

        elif command_lower in {"trust", "trust list"}:
            self.show_trust()

        elif command_lower.startswith("trust revoke "):
            self.revoke_trust(raw_command[len("trust revoke "):].strip())

        elif command_lower == "history":
            self.show_history()

        elif command_lower in {"conversation", "conversation recent"}:
            self.show_conversation()

        elif command_lower.startswith("conversation recent "):
            value = raw_command[len("conversation recent "):].strip()
            self.show_conversation(value)

        elif command_lower == "conversation search":
            print("Usage: conversation search <text>")

        elif command_lower.startswith("conversation search "):
            query = raw_command[len("conversation search "):].strip()
            self.search_conversation(query)

        elif command_lower == "conversation clear":
            self.clear_conversation()

        elif command_lower == "about":
            self.show_about()

        elif command_lower == "ask":
            print("Usage: ask <your question>")

        elif command_lower.startswith("ask "):
            prompt = raw_command[4:].strip()
            result = self.orion.request_router.route(prompt)
            print(result.output)

        elif self._looks_like_weather(raw_command):
            self.show_weather(raw_command)

        elif self._looks_like_calendar(raw_command):
            self.show_calendar(raw_command)

        elif command_lower in ["exit", "quit"]:
            print("Shutting down Orion.")
            return False

        elif self.orion.plugin_manager.dispatch(raw_command):
            pass

        else:
            print(f"Unknown command: {raw_command}")
            print("Type 'help' for available commands.")

        return True

    def show_help(self):
        """Display a concise, task-oriented help menu."""
        print("\nOrion Abilities")
        print("=" * 50)
        print("  Ask & understand")
        print("    ask <question>             Ask Orion's AI provider")
        print("    conversation               Review recent context")
        print("    remember <key> <value>     Remember something this session")
        print()
        print("  Open & discover")
        print("    open <application>         Find and open an application")
        print("    apps find <name>           Search your application library")
        print("    apps scan                  Refresh discovered applications")
        print("    app alias <a> = <app>      Teach Orion your preferred name")
        print()
        print("  Projects & knowledge")
        print('    task create "<goal>"       Create a proposed project task')
        print("    task list                  List project tasks")
        print("    task show <id>             Show task state and artifacts")
        print("    task approve|cancel <id>   Make an explicit task decision")
        print("    task events <id>           Show append-only task progress")
        print("    task link-plan <id> <plan> Link an AI Team plan artifact")
        print("    project resume             Continue where you left off")
        print("    project status             Show project progress")
        print("    index build                Refresh the code knowledge index")
        print("    index find <text>          Search indexed project knowledge")
        print()
        print("  Safety & control")
        print("    action pending             Review pending actions")
        print("    trust list                 Review always-allowed applications")
        print("    settings                   Show Companion preferences")
        print("    developer on|off           Toggle diagnostic details")
        print()
        print("  AI Control Center")
        print("    ai status                  Show active model and capabilities")
        print("    ai models                  Scan and select installed models")
        print("    ai use <model|fastest>     Switch directly or by recommendation")
        print("    ai profile <name>          Activate a saved behavior profile")
        print("    ai benchmark               Compare local response latency")
        print("    ai stats                   Show measured provider/model performance")
        print("    ai stats clear             Reset adaptive-routing performance history")
        print("    ai health                  Show routing health by provider")
        print()
        print("  Agent Registry (planning only)")
        print("    agent list                 Show external agent definitions")
        print("    agent show <name>          Inspect instructions and permissions")
        print("    agent create               Create a least-privilege agent")
        print("    agent enable|disable <name> Change whether an agent may be assigned")
        print("    agent test <name>          Run one bounded structured-output test")
        print()
        print("  AI Team & Codex Bridge")
        print('    team plan "<goal>"         Create an Architect + Engineer plan')
        print("    team roles                 Show role provider/model assignments")
        print("    team status <task-id>      Reopen a persisted team plan")
        print("    team approve <task-id>     Bind approval to this plan and workspace")
        print("    team implement <id> <approval> Run one bounded local Codex execution")
        print("    team run <run-id>          Show structured results awaiting review")
        print("    execution status           Detect local execution engines")
        print()
        print("  System")
        print("    change ollama model        Choose from locally installed Ollama models")
        print("    vault                      Open Orion Vault")
        print("    vault add <provider>       Securely add Gemini or OpenAI credentials")
        print("    vault health               Verify configured credentials")
        print("    ai providers               Show Ollama, OpenAI, and Gemini connections")
        print("    ai connect openai          Securely connect the OpenAI API")
        print("    ai test openai             Verify the saved OpenAI connection")
        print("    ai disconnect openai       Remove OpenAI from Orion Vault")
        print("    ai provider configure ...  Connect a cloud AI provider")
        print("    ai provider use ...        Change Orion's active provider")
        print("    weather [location]         Show live weather and today's forecast")
        print("    weather tomorrow           Show tomorrow's forecast")
        print("    calendar [today|tomorrow]  Show your merged calendar agenda")
        print("    calendar next              Show your next event")
        print("    calendar connect <provider> Authorize Google or Microsoft Calendar")
        print("    calendar enable <provider>  Enable a calendar provider")
        print("    calendar disable <provider> Disable a calendar provider")
        print("    calendar configure <provider> Save provider settings")
        print("    calendar providers         List configured calendar providers")
        print("    home                       Show the Orion Home command center")
        print("    git status                 Show repository state")
        print("    git log                    Show recent commits")
        print("    git diff [staged]          Review local changes")
        print("    git pull | git push        Approval-gated repository sync")
        print("    update check | update      Check or install an Orion package")
        print("    update rollback            Restore the previous application backup")
        print("    briefing                   Show the current Morning Star briefing")
        print("    status                     Show system health")
        print("    workspace [path]           View or change workspace")
        print("    plugins                    Show loaded plugins")
        print("    help                       Show this menu")
        print("    exit                       Shut down Orion")
        print()
        print("Tip: use the Up/Down arrows for history and Tab for completion.")
        plugin_lines = self.orion.plugin_manager.help_lines()
        if plugin_lines:
            print("\nPlugin abilities:")
            for line in plugin_lines:
                print(line)

    def show_status(self):
        """Display a compact Companion health dashboard."""
        mode = "Developer" if self.orion.companion_settings.developer_mode else "Companion"
        index_state = "Built" if self.orion.knowledge_index.exists() else "Not built"
        project_state = "Initialized" if self.orion.project_context.initialized else "Not initialized"
        rows = (
            ("System", self.orion.status),
            ("Interface", mode),
            ("AI Provider", self.orion.ai_provider.name()),
            ("Brain", self.orion.brain.name()),
            ("Workspace", str(self.orion.workspace_manager.root)),
            ("Applications", f"{len(self.orion.application_catalog.applications())} discovered"),
            ("Trusted Actions", str(len(self.orion.action_trust.entries()))),
            ("Session Memory", f"{len(self.orion.session_memory)} items"),
            ("Conversation", f"{self.orion.conversation.count()} messages"),
            ("Knowledge Index", index_state),
            ("Project Context", project_state),
            ("Plugins", f"{self.orion.plugin_manager.loaded_count()} loaded / {self.orion.plugin_manager.failed_count()} failed"),
            ("Briefing", f"{len(self.orion.briefing_service.provider_names())} providers"),
            ("Weather", self.orion.weather_service.get_status().message),
            ("Calendar", self.orion.calendar_service.get_status().message),
            ("Services", f"{len(self.orion.services)} registered"),
        )
        print("\nOrion Status")
        print("=" * 50)
        width = max(len(label) for label, _ in rows)
        for label, value in rows:
            print(f"  {label:<{width}}  {value}")

    @staticmethod
    def _looks_like_weather(text: str) -> bool:
        value = text.strip().lower()
        weather_phrases = (
            "weather", "forecast", "temperature", "how hot", "how cold",
            "do i need an umbrella", "will it rain", "is it raining",
        )
        return any(phrase in value for phrase in weather_phrases)

    def show_weather(self, request: str):
        """Fetch live weather directly instead of asking the language model."""
        result = self.orion.weather_service.handle_request(request)
        if result.success:
            print(result.output)
            return
        print(f"Weather unavailable: {result.error}")
        if self.orion.companion_settings.developer_mode:
            print(f"Service status: {self.orion.weather_service.get_status().state.value}")

    @staticmethod
    def _looks_like_calendar(text: str) -> bool:
        value = text.strip().lower()
        calendar_phrases = (
            "calendar", "schedule", "agenda", "next meeting", "next event",
            "appointments", "am i free", "do i have anything", "what do i have today",
            "what's on my calendar", "what is on my calendar",
        )
        return any(phrase in value for phrase in calendar_phrases)

    def show_calendar(self, request: str):
        """Read connected calendar providers directly instead of asking the language model."""
        result = self.orion.calendar_service.handle_request(request)
        if result.success:
            print(result.output)
            return
        print(f"Calendar unavailable: {result.error}")
        if not self.orion.calendar_service.enabled or not self.orion.calendar_service.active_providers():
            print("Run 'calendar providers', then 'calendar enable <provider>'.")
        if self.orion.companion_settings.developer_mode:
            print(f"Service status: {self.orion.calendar_service.get_status().state.value}")

    def show_home(self):
        """Refresh and render Orion Home."""
        print()
        snapshot = self.orion.home_service.build()
        self.orion.console.render_home(
            snapshot, developer_mode=self.orion.companion_settings.developer_mode
        )

    def show_briefing(self):
        """Build and display the latest provider-neutral briefing."""
        print()
        self.orion.console.render_briefing(
            self.orion.briefing_service.build(),
            developer_mode=self.orion.companion_settings.developer_mode,
        )

    def show_services(self):
        """Display registered services and their implementation types."""
        print("Registered Services:")
        for name, service in self.orion.services.snapshot().items():
            print(f"  {name}: {type(service).__name__}")

    def show_plugins(self):
        """Display discovered plugin state."""
        records = self.orion.plugin_manager.records()
        if not records:
            print("No plugins discovered.")
            return
        print("Orion Plugins:")
        for record in records:
            marker = "[OK]" if record.status == "loaded" else "[ERROR]"
            print(f"  {marker} {record.name} v{record.version} - {record.status}")
            if record.error:
                print(f"        {record.error}")

    def show_plugin_info(self, name: str):
        """Display details for one plugin."""
        if not name:
            print("Usage: plugins info <name>")
            return
        record = next((item for item in self.orion.plugin_manager.records() if item.name == name.strip().lower()), None)
        if record is None:
            print(f"Plugin not found: {name}")
            return
        print(f"Plugin: {record.name}")
        print(f"Version: {record.version}")
        print(f"Status: {record.status}")
        print(f"Description: {record.description}")
        print(f"Path: {record.path}")
        if record.error:
            print(f"Error: {record.error}")

    def _ai_service(self):
        service = getattr(self.orion, "ai_control", None)
        if service is not None:
            return service
        # Compatibility for lightweight test doubles.
        from orion.services.ai_control import AIControlService
        return AIControlService(self.orion.ai_provider, self.orion.config_manager)

    def change_ollama_model(self):
        """Interactively select one of Ollama's locally installed models."""
        provider = self.orion.ai_provider
        if not hasattr(provider, "list_models") or not hasattr(provider, "select_model"):
            print("Ollama is not Orion's active AI provider.")
            return
        print("Scanning Ollama for installed models...")
        try:
            models = self._ai_service().models()
        except (ConnectionError, OSError, ValueError) as exc:
            print(f"Ollama unavailable: {exc}")
            return
        if not models:
            print("No Ollama models were found.")
            print("Install one with: ollama pull <model>")
            return
        current = provider.model
        print("\nInstalled Ollama Models")
        print("-" * 64)
        for index, model in enumerate(models, 1):
            marker = "  [current]" if model.name == current else ""
            tags = ", ".join(model.tags)
            print(f"  {index}. {model.name}{marker}")
            print(f"     {model.parameter_size} | {model.size_label} | Context {model.context_label} | {tags}")
        print("  0. Cancel")
        while True:
            choice = input("\nWhich model would you like to use? ").strip()
            if choice in {"", "0", "cancel", "q", "quit"}:
                print("Model selection cancelled.")
                return
            try:
                selected_index = int(choice) - 1
            except ValueError:
                print(f"Enter a number from 1 to {len(models)}, or 0 to cancel.")
                continue
            if not 0 <= selected_index < len(models):
                print(f"Enter a number from 1 to {len(models)}, or 0 to cancel.")
                continue
            selected = models[selected_index].name
            break
        self._select_ai_model(selected)

    def _select_ai_model(self, selected: str):
        service = self._ai_service()
        current = self.orion.ai_provider.model
        default_before = service.default_model()
        print(f"Loading {selected}...")
        try:
            previous, active = service.select(selected, persist=False)
        except (ConnectionError, OSError, ValueError) as exc:
            print(f"Could not change model: {exc}")
            return
        if active == previous:
            print(f"Orion is already using {active}.")
            if active == default_before:
                print(f"{active} is already your default model.")
            return

        print(f"\n[OK] {active} is loaded and ready.")
        print(f"Switched to {active}.")
        print("The new model is active now. No restart is required.")
        print(f"\nWould you like to make {active} your default model?")
        print("[Y] Yes")
        print("[N] No")
        make_default = input("> ").strip().lower()
        if make_default in {"", "y", "yes"}:
            try:
                service.set_default(active)
            except (OSError, ValueError) as exc:
                print(f"Could not save the default model: {exc}")
                print(f"Using {active} for this session only.")
                return
            print("\n[OK] Default model updated.")
            print(f"Orion will now start with {active} by default.")
        else:
            print(f"\nUsing {active} for this session only.")
            print(f"Your default model remains {default_before}.")

    def use_ai_model(self, request: str):
        """Switch by model name or a transparent recommendation keyword."""
        if not request:
            print("Usage: ai use <model|fastest|coding|reasoning|vision>")
            return
        service = self._ai_service()
        keyword = request.lower().replace("the ", "").replace(" model", "").strip()
        try:
            if keyword in {"fast", "fastest", "lightweight", "coding", "code", "reasoning", "best", "overall", "vision"}:
                model = service.recommend(keyword)
                if model is None:
                    print(f"No installed model matches the '{request}' capability.")
                    return
                print(f"Recommended model for {request}: {model.name}")
                request = model.name
            self._select_ai_model(request)
        except (ConnectionError, OSError, ValueError) as exc:
            print(f"Could not change model: {exc}")

    def show_connect(self):
        print("=" * 62)
        print(f"{'ORION CONNECT':^62}")
        print("=" * 62)
        for item in self.orion.connect_service.statuses():
            state = "[OK]" if item.healthy else ("[!]" if item.configured else "[--]")
            print(f"  {state} {item.name:<12} {item.detail}")
        print("-" * 62)
        print("Commands: connect add gmail | connect add discord | connect health")
        print("          email inbox | email unread | email search <text>")
        print("          email read <number|id> | email compose")
        print("          discord send <message> | connect add discord bot")
        print("          Start two-way Discord with: python -m orion.main --discord")

    def connect_health(self):
        print("Connect Health")
        print("-" * 62)
        for item in self.orion.connect_service.statuses():
            state = "[OK]" if item.healthy else "[--]"
            print(f"  {state} {item.name:<12} {item.detail}")

    def connect_gmail(self):
        print("Connect Gmail")
        print("A browser window will open for Google authorization.")
        print(f"OAuth file: {self.orion.connect_service.gmail.credentials_path}")
        answer = input("Continue? [Y/n]: ").strip().lower()
        if answer not in {"", "y", "yes"}:
            print("Gmail connection cancelled.")
            return
        try:
            self.orion.connect_service.gmail.connect()
            profile = self.orion.connect_service.gmail.profile()
            print(f"[OK] Gmail connected: {profile.get('emailAddress', 'account')}" )
        except Exception as exc:
            print(f"Could not connect Gmail: {exc}")

    def connect_discord(self):
        print("Connect Discord")
        print("Create a webhook in the Discord channel you want Orion to post to.")
        webhook = getpass("Webhook URL: ").strip()
        if not webhook:
            print("Discord connection cancelled.")
            return
        try:
            self.orion.vault.add("discord", webhook)
            self.orion.connect_service.discord.webhook_url = webhook
            info = self.orion.connect_service.discord.health()
            print(f"[OK] Discord connected: {info.get('name') or 'webhook'}")
        except Exception as exc:
            print(f"Could not connect Discord: {exc}")

    def connect_discord_bot(self):
        print("Connect Discord Bot")
        print("This enables two-way DMs and @mentions through the Orion Brain.")
        print("Create an application and bot in the Discord Developer Portal first.")
        token = getpass("Bot token: ").strip()
        if not token:
            print("Discord bot setup cancelled.")
            return

        raw_owners = input(
            "Owner Discord user ID(s), comma-separated (only owners may approve sensitive actions): "
        ).strip()
        allow_answer = input(
            "Allow anyone in the approved channel(s) to talk to Orion? [y/N]: "
        ).strip().lower()
        allow_channel_members = allow_answer in {"y", "yes"}
        raw_channels = input("Allowed channel ID(s), comma-separated: ").strip()
        raw_roles = input("Required role ID(s), comma-separated [optional]: ").strip()

        def parse_ids(raw_value):
            return [int(value.strip()) for value in raw_value.split(",") if value.strip()]

        try:
            owner_ids = parse_ids(raw_owners)
            channel_ids = parse_ids(raw_channels)
            role_ids = parse_ids(raw_roles)
        except ValueError:
            print("Discord IDs must contain numbers only.")
            return
        if not owner_ids:
            print("At least one owner Discord user ID is required.")
            return
        if not channel_ids:
            print("At least one allowed Discord channel ID is required.")
            return

        self.orion.vault.add("discord_bot", token)
        self.orion.config_manager.set("connect.discord_bot.owner_user_ids", owner_ids)
        # Keep the legacy field synchronized for backward compatibility.
        self.orion.config_manager.set("connect.discord_bot.allowed_user_ids", owner_ids)
        self.orion.config_manager.set("connect.discord_bot.allow_channel_members", allow_channel_members)
        self.orion.config_manager.set("connect.discord_bot.allowed_channel_ids", channel_ids)
        self.orion.config_manager.set("connect.discord_bot.allowed_role_ids", role_ids)
        self.orion.config_manager.set("connect.discord_bot.enabled", True)
        self.orion.config_manager.save()
        print("[OK] Discord bot credentials and access policy saved.")
        access = "any member in the approved channel(s)" if allow_channel_members else "owners only"
        print(
            f"[OK] Access: {access}; {len(channel_ids)} channel(s), "
            f"{len(role_ids)} role restriction(s), {len(owner_ids)} owner(s)."
        )
        print("Enable Message Content Intent in the Discord Developer Portal.")
        print("Discord will start automatically the next time Orion launches.")

    def discord_bot_status(self):
        configured = bool(self.orion.vault.store.get("discord_bot"))
        owners = self.orion.config_manager.get(
            "connect.discord_bot.owner_user_ids",
            self.orion.config_manager.get("connect.discord_bot.allowed_user_ids", []),
        )
        channels = self.orion.config_manager.get("connect.discord_bot.allowed_channel_ids", [])
        roles = self.orion.config_manager.get("connect.discord_bot.allowed_role_ids", [])
        allow_channel_members = bool(
            self.orion.config_manager.get("connect.discord_bot.allow_channel_members", False)
        )
        enabled = bool(self.orion.config_manager.get("connect.discord_bot.enabled", False))
        interface = getattr(self.orion, "discord_interface", None)
        running = bool(interface)
        print("Discord Bot Interface")
        print("-" * 62)
        print(f"Configured : {'Yes' if configured else 'No'}")
        print(f"Enabled    : {'Yes' if enabled else 'No'}")
        print(f"Running    : {'Yes' if running else 'No'}")
        print(f"Access     : {'Channel members' if allow_channel_members else 'Owners only'}")
        print(f"Owners     : {len(owners)}")
        print(f"Channels   : {len(channels)} allowed")
        print(f"Roles      : {len(roles)} required")
        if configured and enabled and not running:
            print("Restart Orion to start the Discord interface automatically.")

    def connect_debug(self):
        interface = getattr(self.orion, "discord_interface", None)
        print("Discord Gateway Diagnostics")
        print("-" * 62)
        if interface is None:
            configured = bool(self.orion.vault.store.get("discord_bot"))
            enabled = bool(self.orion.config_manager.get("connect.discord_bot.enabled", False))
            print(f"Configured       : {'Yes' if configured else 'No'}")
            print(f"Enabled          : {'Yes' if enabled else 'No'}")
            print("Runtime          : Offline")
            return
        d = interface.diagnostics
        print(f"Runtime          : {d.state}")
        print(f"Bot              : {d.bot_name or 'Connecting...'}")
        print(f"Guilds           : {', '.join(d.guilds) if d.guilds else 'None'}")
        print(f"Watching         : {', '.join(d.watching) if d.watching else 'None'}")
        print(f"Access           : {'Channel members' if interface.policy.allow_channel_members else 'Owners only'}")
        print(f"Owners           : {len(interface.policy.owner_user_ids)}")
        print(f"Messages received: {d.messages_received}")
        print(f"Replies sent     : {d.replies_sent}")
        print(f"Ignored          : {d.ignored}")
        print(f"Last user ID     : {d.last_user_id}")
        print(f"Last channel ID  : {d.last_channel_id}")
        print(f"Last route       : {d.last_route}")
        print(f"Last request     : {d.last_request}")
        print(f"Last ignore      : {d.last_ignore_reason}")
        print(f"Last error       : {d.last_error}")

    def set_discord_bot_enabled(self, enabled: bool):
        if enabled and not self.orion.vault.store.get("discord_bot"):
            print("Discord bot is not configured. Run: connect add discord bot")
            return
        self.orion.config_manager.set("connect.discord_bot.enabled", enabled)
        self.orion.config_manager.save()
        state = "enabled" if enabled else "disabled"
        print(f"[OK] Discord bot interface {state}.")
        if enabled:
            print("Restart Orion to apply the change.")

    def _mail_rows(self, query="in:inbox"):
        messages = self.orion.connect_service.gmail.list_messages(query=query, limit=10)
        self._last_email_messages = messages
        if not messages:
            print("No matching messages.")
            return []
        for index, item in enumerate(messages, start=1):
            unread = "*" if item.unread else " "
            print(f"{index:>2}. [{unread}] {item.subject}")
            print(f"    From: {item.sender}")
            if item.snippet:
                print(f"    {item.snippet[:110]}")
        return messages

    def email_inbox(self):
        print("Gmail Inbox")
        print("-" * 62)
        try:
            self._mail_rows("in:inbox")
        except Exception as exc:
            print(f"Could not read Gmail: {exc}")

    def email_unread(self):
        try:
            count = self.orion.connect_service.gmail.unread_count()
            print(f"Unread Gmail messages: {count}")
            if count:
                self._mail_rows("in:inbox is:unread")
        except Exception as exc:
            print(f"Could not read Gmail: {exc}")

    def email_search(self, query: str):
        if not query:
            print("Usage: email search <text>")
            return
        print(f"Gmail Search: {query}")
        print("-" * 62)
        try:
            self._mail_rows(query)
        except Exception as exc:
            print(f"Could not search Gmail: {exc}")

    def email_read(self, reference: str):
        if not reference:
            print("Usage: email read <number|id>")
            return
        message_id = reference
        if reference.isdigit() and hasattr(self, "_last_email_messages"):
            index = int(reference) - 1
            if 0 <= index < len(self._last_email_messages):
                message_id = self._last_email_messages[index].id
        try:
            item = self.orion.connect_service.gmail.read_message(message_id)
            print(item.subject)
            print(f"From: {item.sender}")
            print("-" * 62)
            print(item.snippet or "Message body preview unavailable.")
        except Exception as exc:
            print(f"Could not read Gmail message: {exc}")

    def email_compose(self):
        print("Compose Email")
        to = input("To: ").strip()
        subject = input("Subject: ").strip()
        print("Body (finish with a single period on its own line):")
        lines = []
        while True:
            line = input()
            if line == ".":
                break
            lines.append(line)
        body = "\n".join(lines).strip()
        if not to or not subject or not body:
            print("Email cancelled: recipient, subject, and body are required.")
            return
        print("-" * 62)
        print(f"To: {to}\nSubject: {subject}\n\n{body}")
        print("-" * 62)
        answer = input("Send this email? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Email not sent.")
            return
        try:
            self.orion.connect_service.gmail.send(to, subject, body)
            print("[OK] Email sent.")
        except Exception as exc:
            print(f"Could not send email: {exc}")

    def discord_send(self, message: str):
        if not message:
            print("Usage: discord send <message>")
            return
        print("Discord message preview")
        print("-" * 62)
        print(message)
        print("-" * 62)
        answer = input("Post this message? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Discord message not posted.")
            return
        try:
            self.orion.connect_service.discord.send(message)
            print("[OK] Discord message posted.")
        except Exception as exc:
            print(f"Could not post to Discord: {exc}")

    def show_vault(self):
        print("=" * 62)
        print(f"{'ORION VAULT':^62}")
        print("=" * 62)
        print(f"Storage: {self.orion.vault.path}")
        print("Secrets are hidden and kept outside config/default.yaml.")
        print("-" * 62)
        print("AI Providers")
        for entry in self.orion.vault.list_entries():
            state = "[OK]" if entry.configured else "[--]"
            label = entry.key.title()
            print(f"  {state} {label:<12} {entry.source}")
        print("-" * 62)
        print("Commands: vault add <gemini|openai> | vault remove <provider>")
        print("          vault health | ai provider use <provider>")

    def vault_add(self, provider: str):
        key = provider.lower().strip()
        if key not in {"openai", "gemini"}:
            print("Usage: vault add <openai|gemini>")
            return
        print(f"Add {key.title()} to Orion Vault")
        print("The key will not be displayed or saved in config/default.yaml.")
        api_key = getpass("API key: ").strip()
        if not api_key:
            print("Vault update cancelled.")
            return
        try:
            self.orion.vault.add(key, api_key)
            models = self.orion.provider_manager.models(key)
        except (ConnectionError, OSError, ValueError) as exc:
            print(f"Could not add {key.title()}: {exc}")
            return
        print(f"[OK] {key.title()} saved and verified.")
        print(f"[OK] {len(models)} compatible model(s) discovered.")
        default_model = self.orion.config_manager.get(f"providers.{key}.model", "")
        if models:
            print("Available models:")
            for index, model in enumerate(models[:12], start=1):
                marker = " [current]" if model == default_model else ""
                print(f"  {index}. {model}{marker}")
            choice = input(f"Choose a default model [Enter keeps {default_model}]: ").strip()
            if choice:
                try:
                    selected = models[int(choice)-1] if choice.isdigit() and 1 <= int(choice) <= len(models) else choice
                    self.orion.config_manager.set(f"providers.{key}.model", selected)
                    self.orion.config_manager.save()
                    print(f"[OK] Default {key.title()} model: {selected}")
                except (IndexError, ValueError):
                    print("Model selection skipped.")
        make_active = input(f"Make {key.title()} Orion's active provider? [y/N]: ").strip().lower()
        if make_active in {"y", "yes"}:
            try:
                active = self.orion.provider_manager.activate(key)
                print(f"[OK] Active AI provider: {active.name()}")
            except (ConnectionError, OSError, ValueError) as exc:
                print(f"Saved, but could not activate provider: {exc}")

    def vault_remove(self, provider: str):
        key = provider.lower().strip()
        if key not in {"openai", "gemini"}:
            print("Usage: vault remove <openai|gemini>")
            return
        answer = input(f"Remove {key.title()} credentials from Orion Vault? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Vault unchanged.")
            return
        self.orion.vault.remove(key)
        print(f"[OK] {key.title()} removed from Orion Vault.")
        if self.orion.config_manager.get("providers.default", "ollama") == "ollama":
            try:
                self.orion.provider_manager.activate("ollama")
            except (ConnectionError, OSError, ValueError):
                pass

    def vault_health(self):
        print("Vault Health")
        print("-" * 62)
        results = self.orion.vault.health(self.orion.provider_manager.models)
        healthy = 0
        configured = 0
        for result in results:
            if result.configured:
                configured += 1
            if result.healthy:
                healthy += 1
            state = "[OK]" if result.healthy else "[--]"
            print(f"  {state} {result.key.title():<12} {result.message}")
        print("-" * 62)
        print(f"Healthy: {healthy}/{len(results)} | Configured: {configured}/{len(results)}")

    def show_ai_providers(self):
        """Display configured AI engines in the Polaris federation."""
        manager = self.orion.provider_manager
        print("AI Providers")
        print("-" * 62)
        for item in manager.statuses():
            marker = "[ACTIVE]" if item.active else "[--]"
            state = "Ready" if item.enabled and item.configured else ("Needs API key" if item.enabled else "Disabled")
            print(f"{marker:<8} {item.key.title():<10} {state:<16} {item.model}")
        print("-" * 62)
        print("Commands: ai connect openai | ai test openai")
        print("          ai provider configure <openai|gemini>")
        print("          ai provider use <ollama|openai|gemini>")
        print("          ai provider models <provider>")

    def configure_ai_provider(self, provider: str):
        key = provider.lower().strip()
        if key not in {"openai", "gemini"}:
            print("Usage: ai provider configure <openai|gemini>")
            return
        print(f"Configure {key.title()}")
        print("API keys are stored separately from config/default.yaml.")
        api_key = getpass("API key: ").strip()
        if not api_key:
            print("Configuration cancelled.")
            return
        default_model = "gpt-4.1-mini" if key == "openai" else "gemini-2.5-flash"
        model = input(f"Default model [{default_model}]: ").strip() or default_model
        try:
            self.orion.provider_manager.configure(key, api_key, model)
            models = self.orion.provider_manager.models(key)
        except (ConnectionError, OSError, ValueError) as exc:
            print(f"Could not configure {key.title()}: {exc}")
            return
        print(f"[OK] {key.title()} connected. {len(models)} compatible model(s) discovered.")
        make_active = input(f"Make {key.title()} Orion's active provider? [Y/n]: ").strip().lower()
        if make_active in {"", "y", "yes"}:
            try:
                active = self.orion.provider_manager.activate(key)
            except (ConnectionError, OSError, ValueError) as exc:
                print(f"Provider saved but could not be activated: {exc}")
                return
            print(f"[OK] Active AI: {active.name()}")

    def test_ai_provider(self, provider: str):
        key = provider.lower().strip()
        try:
            models = self.orion.provider_manager.test_connection(key)
        except (ConnectionError, OSError, ValueError) as exc:
            print(f"[FAIL] {key.title()} connection failed: {exc}")
            return
        configured_model = self.orion.config_manager.get(f"providers.{key}.model", "")
        print(f"[OK] {key.title()} API connection verified.")
        print(f"     Credential source: {self.orion.vault.store.source(key)}")
        print(f"     Models visible: {len(models)}")
        if configured_model:
            state = "available" if configured_model in models else "configured (not returned by model list)"
            print(f"     Default model: {configured_model} [{state}]")

    def disconnect_ai_provider(self, provider: str):
        key = provider.lower().strip()
        if key not in {"openai", "gemini"}:
            print("Usage: ai disconnect <openai|gemini>")
            return
        answer = input(f"Disconnect {key.title()} and remove its API key from Orion Vault? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Provider unchanged.")
            return
        self.orion.vault.remove(key)
        try:
            active = self.orion.provider_manager.activate("ollama")
            print(f"[OK] {key.title()} disconnected. Active AI: {active.name()}")
        except (ConnectionError, OSError, ValueError):
            print(f"[OK] {key.title()} disconnected. Restart Orion to reload the active provider.")

    def use_ai_provider(self, provider: str):
        try:
            active = self.orion.provider_manager.activate(provider)
        except (ConnectionError, OSError, ValueError) as exc:
            print(f"Could not activate provider: {exc}")
            return
        print(f"[OK] Active AI provider: {active.name()}")
        print("This provider will be used the next time Orion starts.")

    def show_provider_models(self, provider: str):
        try:
            models = self.orion.provider_manager.models(provider)
        except (ConnectionError, OSError, ValueError) as exc:
            print(f"Could not list models: {exc}")
            return
        print(f"{provider.title()} Models")
        print("-" * 62)
        for index, model in enumerate(models, start=1):
            print(f"  {index}. {model}")
        if not models:
            print("  No compatible models found.")

    def show_ai_status(self):
        """Display Orion's AI Control Center summary."""
        provider = self.orion.ai_provider
        print("=" * 62)
        print(f"{'AI CONTROL CENTER':^62}")
        print("=" * 62)
        provider_key = self.orion.config_manager.get("providers.default", "ollama")
        print(f"Provider        : {provider.__class__.__name__.replace('Provider', '')}")
        current_model = getattr(provider, "model", "Unknown")
        default_model = self.orion.config_manager.get(f"providers.{provider_key}.model", current_model)
        print(f"Current model   : {current_model}")
        print(f"Default model   : {default_model}")
        print(f"Session override: {'Enabled' if current_model != default_model else 'None'}")
        print(f"Active profile  : {str(self.orion.config_manager.get('ai.active_profile', 'balanced')).title()}")
        print(f"Temperature     : {self.orion.config_manager.get('ai.temperature', 0.5)}")
        print("Status          : Online")
        try:
            models = self._ai_service().models()
            current = next((item for item in models if item.name == provider.model), None)
            print(f"Installed       : {len(models)} models")
            if current:
                capabilities = set(current.tags)
                print("-" * 62)
                print("Capabilities")
                for capability in ("Chat", "Coding", "Vision", "Speech", "Embeddings"):
                    available = capability == "Chat" or capability in capabilities
                    print(f"  {'[OK]' if available else '[--]'} {capability}")
                print("-" * 62)
                print("Model details")
                print(f"  Parameters    : {current.parameter_size}")
                print(f"  Context       : {current.context_label}")
                print(f"  Disk size     : {current.size_label}")
        except ConnectionError as exc:
            print(f"Ollama         : Offline ({exc})")
        print("-" * 62)
        print("Commands: ai providers | ai route status | ai use <model> | ai profiles | ai benchmark")

    def show_ai_route_status(self):
        service = self.orion.ai_routing
        state = service.status()
        print("AI Routing Engine")
        print("-" * 62)
        print(f"Enabled   : {'Yes' if state['enabled'] else 'No'}")
        print(f"Profile   : {state['profile'].title()}")
        print(f"Adaptive  : {'Yes' if state['adaptive'] else 'No'}")
        providers = ", ".join(item.title() for item in state["ready_providers"]) or "None"
        print(f"Providers : {providers}")
        last = state.get("last_decision")
        if last:
            print(f"Last route: {last['provider']}:{last['model']} ({last['task_type']})")
        else:
            print("Last route: None")
        print("-" * 62)
        print("Commands: ai route on | ai route off | ai route explain last")
        print("          ai profile <fast|balanced|coding|research>")

    def show_ai_stats(self):
        rows = self.orion.ai_routing.performance.summary()
        print("AI Performance Statistics")
        print("-" * 78)
        if not rows:
            print("No routed requests have been measured yet.")
            return
        print(f"{'Provider':<11} {'Model':<25} {'Requests':>8} {'Success':>9} {'Average':>9}")
        for row in rows:
            print(f"{str(row['provider']).title():<11} {str(row['model'])[:25]:<25} "
                  f"{int(row['requests']):>8} {float(row['success_rate_percent']):>8.1f}% "
                  f"{float(row['average_duration_seconds']):>8.3f}s")
        print("Telemetry contains aggregate timing and outcomes only; prompts are never stored.")

    def clear_ai_stats(self):
        self.orion.ai_routing.performance.clear()
        print("[OK] AI performance history cleared.")

    def show_ai_health(self):
        state = self.orion.ai_routing.status()
        print("AI Provider Health")
        print("-" * 62)
        if not state["provider_health"]:
            print("No configured providers are ready.")
            return
        for item in state["provider_health"]:
            print(f"{item['provider'].title():<11} {item['state'].title():<10} | "
                  f"{item['requests']} requests | {item['success_rate_percent']:.1f}% success | "
                  f"{item['average_duration_seconds']:.3f}s average")

    def set_ai_routing(self, enabled: bool):
        self.orion.ai_routing.set_enabled(enabled)
        print(f"[OK] Automatic AI routing {'enabled' if enabled else 'disabled'}.")

    def explain_last_ai_route(self):
        decision = self.orion.ai_routing.last_decision
        if decision is None:
            print("No routed AI request has been recorded in this session.")
            return
        print("Last AI Route")
        print("-" * 62)
        print(f"Profile   : {decision.profile.title()}")
        print(f"Task      : {decision.task_type.title()}")
        print(f"Provider  : {decision.provider.title()}")
        print(f"Model     : {decision.model}")
        print(f"Reason    : {decision.reason}")
        fallback = ", ".join(item.title() for item in decision.fallbacks) or "None"
        print(f"Fallbacks : {fallback}")
        print(f"Duration  : {decision.duration_seconds:.3f}s")
        print(f"Result    : {'Success' if decision.success else 'Failed'}")
        if decision.error:
            print(f"Error     : {decision.error}")

    def show_ai_profiles(self):
        service = self._ai_service()
        active = self.orion.config_manager.get("ai.active_profile", "balanced")
        print("AI Profiles")
        print("-" * 50)
        for name, profile in service.PROFILES.items():
            marker = " [active]" if name == active else ""
            print(f"  {name}{marker}: {profile['description']}")

    def activate_ai_profile(self, name: str):
        key = name.lower().strip()
        if key in self.orion.ai_routing.PROFILES:
            try:
                selected = self.orion.ai_routing.set_profile(key)
            except ValueError as exc:
                print(f"Could not activate AI profile: {exc}")
                return
            print(f"AI routing profile activated: {selected.title()}")
            print(self.orion.ai_routing.PROFILES[selected])
            return
        try:
            result = self._ai_service().activate_profile(name)
        except (ConnectionError, OSError, ValueError) as exc:
            print(f"Could not activate AI profile: {exc}")
            return
        print(f"AI profile activated: {result['name']}")
        print(f"Model: {result['model']} | Temperature: {result['temperature']}")

    def benchmark_ai_models(self):
        """Run an intentionally small, opt-in latency check across chat-capable models."""
        service = self._ai_service()
        try:
            models = [item for item in service.models() if "Speech" not in item.tags and "Embeddings" not in item.tags]
        except ConnectionError as exc:
            print(f"Ollama unavailable: {exc}")
            return
        if not models:
            print("No chat-capable Ollama models were found.")
            return
        print(f"Quick benchmark will load {len(models)} models and may use significant RAM/VRAM.")
        confirm = input("Continue? [y/N]: ").strip().lower()
        if confirm not in {"y", "yes"}:
            print("Benchmark cancelled.")
            return
        results = []
        for model in models:
            print(f"Testing {model.name}...")
            try:
                result = service.quick_benchmark(model.name)
                results.append(result)
                self.orion.ai_routing.performance.record("ollama", model.name, result["seconds"], True)
            except Exception as exc:
                results.append({"model": model.name, "seconds": None, "response": str(exc)})
                self.orion.ai_routing.performance.record("ollama", model.name, 0.0, False, exc)
        print("\nQuick Model Benchmark")
        print("-" * 64)
        for result in sorted(results, key=lambda item: item["seconds"] if item["seconds"] is not None else float("inf")):
            elapsed = f"{result['seconds']:.2f}s" if result["seconds"] is not None else "failed"
            print(f"  {result['model']:<36} {elapsed}")
        print("Latency is measured locally; Orion does not invent a quality score.")

    def show_agents(self):
        print("Agent Registry")
        print("-" * 62)
        try:
            agents = self.orion.agents.all()
        except (OSError, ValueError) as exc:
            print(str(exc))
            return
        if not agents:
            print("No agents are configured.")
            return
        for agent in agents:
            try:
                provider, model = self.orion.agents.resolve(agent)
                runtime = f"{provider}:{model}"
            except ValueError:
                runtime = "invalid provider/model"
            state = "enabled" if agent.enabled else "disabled"
            print(f"  {agent.agent_id:<22} {agent.name:<24} {runtime} ({state})")
        print(f"Definitions: {self.orion.agents.root}")
        print("Phase 1 stores tool permissions as metadata and grants no tools.")

    def show_agent(self, agent_id: str):
        try:
            agent = self.orion.agents.load(agent_id)
            provider, model = self.orion.agents.resolve(agent)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(str(exc))
            return
        permissions = agent.permissions
        print(f"Agent: {agent.name}")
        print("-" * 62)
        print(f"ID: {agent.agent_id}")
        print(f"Status: {'Enabled' if agent.enabled else 'Disabled'}")
        print(f"Provider: {agent.provider} -> {provider}")
        print(f"Model: {agent.model} -> {model}")
        print(f"Instructions: {agent.instructions}")
        print(f"Tools: {', '.join(agent.tools) if agent.tools else 'none'}")
        print(f"Max turns: {agent.limits.max_turns}")
        print(f"Can modify files: {agent.limits.can_modify_files}")
        print(
            "Filesystem: "
            f"read={permissions.filesystem.read}, write={permissions.filesystem.write}"
        )
        print(
            "Shell: "
            f"run_tests={permissions.shell.run_tests}, "
            f"arbitrary_commands={permissions.shell.arbitrary_commands}"
        )
        print(
            "Git: "
            f"create_branch={permissions.git.create_branch}, "
            f"commit={permissions.git.commit}, push={permissions.git.push}"
        )
        print("Phase 1 enforcement: no tools are granted during agent tests or team plans.")

    def create_agent(self):
        print("Create Agent")
        print("-" * 62)
        print("The new agent starts with read-only, planning-safe permissions.")
        agent_id = input("Agent ID (example: security-reviewer): ").strip()
        name = input("Display name: ").strip()
        provider = input(
            "Provider [configured-default|ollama|openai|gemini] (configured-default): "
        ).strip() or "configured-default"
        model = input("Model (configured-default): ").strip() or "configured-default"
        instructions = input("Instructions: ").strip()
        tools_text = input(
            "Declared tools, comma-separated (optional; e.g. read_files, inspect_diff): "
        ).strip()
        max_turns_text = input("Maximum turns (3): ").strip() or "3"
        tools = tuple(
            item.strip().lower()
            for item in tools_text.split(",")
            if item.strip()
        )
        permissions = AgentPermissions.for_tools(tools)
        try:
            max_turns = int(max_turns_text)
            agent = AgentDefinition.from_value(agent_id, {
                "name": name,
                "enabled": True,
                "provider": provider,
                "model": model,
                "instructions": instructions,
                "tools": list(tools),
                "limits": {"max_turns": max_turns, "can_modify_files": False},
                "permissions": permissions.to_dict(),
            })
            path = self.orion.agents.save(agent, overwrite=False)
        except (FileExistsError, OSError, TypeError, ValueError) as exc:
            print(f"Agent was not created: {exc}")
            return
        print(f"Created {agent.name} ({agent.agent_id}).")
        print(f"Saved to: {path}")
        print("Declared permissions remain inactive in Phase 1.")

    def set_agent_enabled(self, agent_id: str, enabled: bool):
        try:
            agent = self.orion.agents.set_enabled(agent_id, enabled)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(str(exc))
            return
        state = "enabled" if agent.enabled else "disabled"
        print(f"Agent {agent.agent_id} is now {state}.")

    def test_agent(self, agent_id: str):
        print("Running one bounded structured-output test; no tools are available...")
        try:
            result = self.orion.agents.test(agent_id)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            print(f"Agent test failed: {exc}")
            return
        print(f"Agent Test: {result.agent.name}")
        print("-" * 62)
        print(f"Runtime: {result.provider}:{result.model}")
        print(result.response.summary)
        for item in result.response.recommendations:
            print(f"  - {item}")
        if result.response.risks:
            print("Risks:")
            for risk in result.response.risks:
                print(f"  - {risk}")
        print(f"Next action: {result.response.next_action}")
        print("Result: passed strict schema validation (one turn, no tools).")

    def show_team(self):
        tasks = self.orion.team.recent(5)
        print("AI Team")
        print("-" * 62)
        print("Planning: Architect -> Engineer Review -> Awaiting Approval.")
        print("Codex Bridge: one approved workspace-confined run -> Awaiting Review.")
        print("Commits, pushes, merges, tags, and pull requests remain disabled.")
        if tasks:
            print("Recent tasks")
            for task in tasks:
                print(f"  {task.task_id} | {task.status.replace('_', ' ').title()} | {task.goal[:60]}")
        else:
            print("No team planning tasks have been created yet.")
        print('-' * 62)
        print(
            'Commands: team plan "<goal>" | team approve <task-id> | '
            "team implement <task-id> <approval-id> | team run <run-id>"
        )

    def show_team_roles(self):
        print("AI Team Roles")
        print("-" * 62)
        try:
            roles = self.orion.team.roles()
        except ValueError as exc:
            print(f"AI Team role configuration is invalid: {exc}")
            return
        for role in roles:
            state = "active in Phase 1" if role.active else "reserved for a later phase"
            print(
                f"  {role.name.title():<11} {role.agent_id} ({role.agent_name}) -> "
                f"{role.provider}:{role.model} ({state})"
            )
        print("Roles define workflow jobs; agents provide configurable workers.")

    def team_plan(self, payload: str):
        goal = payload.strip()
        if goal[:1] in {'"', "'"}:
            if len(goal) < 2 or goal[-1] != goal[0]:
                print("Could not read team goal: closing quote is missing.")
                return
            goal = goal[1:-1].strip()
        if not goal:
            print('Usage: team plan "<goal>"')
            return
        print("AI Team is preparing a bounded two-role plan...")
        try:
            task = self.orion.team.plan(goal)
        except (OSError, TeamPlanningError, ValueError) as exc:
            print(f"AI Team planning failed: {exc}")
            task_id = getattr(exc, "task_id", "")
            if task_id:
                print(f"Saved task: {task_id}")
            return
        self._render_team_task(task)

    def team_status(self, task_id: str):
        try:
            task = self.orion.team.task(task_id)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(str(exc))
            return
        self._render_team_task(task)

    def team_approve(self, task_id: str):
        try:
            approval = self.orion.codex_bridge.approve(task_id)
        except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
            print(f"Codex Bridge approval failed: {exc}")
            return
        print("\nCodex Plan Approval")
        print("-" * 72)
        print(f"AI Team Task: {approval.team_task_id}")
        print(f"Approval ID: {approval.approval_id}")
        print(f"Plan SHA-256: {approval.plan_hash}")
        print(f"Workspace: {approval.workspace_root}")
        print("Approval is immutable, workspace-bound, and valid for one execution only.")
        print(
            f"Run with: team implement {approval.team_task_id} {approval.approval_id}"
        )

    def team_implement(self, payload: str):
        parts = payload.split()
        if len(parts) != 2:
            print("Usage: team implement <team-task-id> <approval-id>")
            return
        team_task_id, approval_id = parts
        engines = getattr(self.orion, "execution_engines", None)
        if engines is not None:
            try:
                engines.require_codex()
            except ExecutionEngineUnavailable:
                self._render_no_execution_engine(engines)
                return
        print("Starting one approval-bound local Codex execution...")
        try:
            run = self.orion.codex_bridge.execute(team_task_id, approval_id)
        except ExecutionEngineUnavailable:
            self._render_no_execution_engine(engines)
            return
        except (FileNotFoundError, OSError, PermissionError, ValueError, CodexBridgeError) as exc:
            if isinstance(exc, CodexBridgeError) and exc.category == "codex_cli_unavailable" and engines is not None:
                self._render_no_execution_engine(engines)
                if exc.run_id:
                    print(f"Saved run: {exc.run_id}")
                return
            print(f"Codex Bridge execution failed: {exc}")
            run_id = getattr(exc, "run_id", "")
            if run_id:
                print(f"Saved run: {run_id}")
            return
        self._render_codex_run(run, self.orion.codex_bridge.store.run_directory(run.run_id))

    def show_execution_status(self):
        engines = self.orion.execution_engines.status()
        print("Execution Engines")
        print("=" * 50)
        for engine in engines:
            print(f"\n{engine.name}")
            print("Status:")
            print(engine.status.replace("_", " ").title())
            if engine.engine_id == "chatgpt_desktop":
                print("CLI Support:")
                print("Yes" if engine.cli_support else "No")

    @staticmethod
    def _render_no_execution_engine(engines):
        detected = {
            engine.engine_id: engine
            for engine in engines.status()
        }
        print("No execution engine is currently available.")
        print("\nDetected:\n")
        for engine_id in ("chatgpt_desktop", "codex", "claude_code", "gemini_cli"):
            engine = detected[engine_id]
            marker = "✓" if engine.installed else "✗"
            print(f"{marker} {engine.name}")
        print("\nUse:\n")
        print("  execution status")
        print("\nto configure an execution engine.")

    def team_run_status(self, run_id: str):
        try:
            run = self.orion.codex_bridge.run(run_id)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Codex Bridge Error: {exc}")
            return
        self._render_codex_run(run, self.orion.codex_bridge.store.run_directory(run.run_id))

    @staticmethod
    def _render_team_task(task):
        print("\nAI Team Plan")
        print("-" * 62)
        print(f"Task: {task.task_id}")
        print(f"Goal: {task.goal}")
        labels = {"architect": "Architect", "engineer": "Engineer Review"}
        for role in ("architect", "engineer"):
            artifact = task.artifact(role)
            if artifact is None:
                continue
            print(f"\n{labels[role]}")
            print(f"  {artifact.output.summary}")
            for item in artifact.output.recommendations:
                print(f"  - {item}")
            if artifact.output.risks:
                print("  Risks:")
                for risk in artifact.output.risks:
                    print(f"    - {risk}")

        if task.final_plan:
            print("\nFinal Plan")
            for index, item in enumerate(task.final_plan, start=1):
                print(f"  {index}. {item}")

        if task.usage:
            print("\nUsage (estimated tokens)")
            for usage in task.usage:
                cost = (
                    "not configured"
                    if usage.estimated_cost_usd is None
                    else f"${usage.estimated_cost_usd:.6f}"
                )
                print(
                    f"  {usage.role.title():<10} {usage.provider}:{usage.model} | "
                    f"{usage.input_tokens} in + {usage.output_tokens} out | Cost: {cost}"
                )
            total_cost = task.estimated_cost_usd
            total_cost_text = "not configured" if total_cost is None else f"${total_cost:.6f}"
            print(f"  Total: {task.total_tokens} tokens | Cost: {total_cost_text}")

        print(f"\nStatus: {task.status.replace('_', ' ').title()}")
        if task.error:
            print(f"Error: {task.error}")
        if task.status == "awaiting_approval":
            print("No implementation has been performed. This task is awaiting your approval.")
            print(f"Approve this exact plan with: team approve {task.task_id}")

    @staticmethod
    def _render_codex_run(run, artifact_directory):
        print("\nCodex Implementation")
        print("-" * 72)
        print(f"Run: {run.run_id}")
        print(f"AI Team Task: {run.team_task_id}")
        print(f"Approval: {run.approval_id}")
        print(f"Plan SHA-256: {run.plan_hash}")
        print(f"Workspace: {run.workspace_root}")
        print(f"Status: {run.status.replace('_', ' ').title()}")
        if run.result is not None:
            print(f"\nSummary\n  {run.result.summary}")
            print("\nFiles Changed")
            if run.result.files_changed:
                for item in run.result.files_changed:
                    print(f"  - {item.path}: {item.summary}")
            else:
                print("  none")
            print("\nTests")
            for test in run.result.tests:
                print(f"  - [{test.status.upper()}] {test.command}: {test.summary}")
            if run.result.risks:
                print("\nRisks")
                for item in run.result.risks:
                    print(f"  - {item}")
            if run.result.remaining_work:
                print("\nRemaining Work")
                for item in run.result.remaining_work:
                    print(f"  - {item}")
            if run.result.review_notes:
                print("\nReview Notes")
                for item in run.result.review_notes:
                    print(f"  - {item}")
        if run.error:
            print(f"Error category: {run.error}")
        print(f"\nArtifacts: {artifact_directory}")
        if run.status == "awaiting_review":
            print("Stopped at Awaiting Review. No Git or pull-request action was performed.")

    def show_config(self):
        """Display loaded configuration."""
        print("Loaded configuration:")
        print(self.orion.config)

    def show_profile(self):
        """Display loaded user profile."""
        print("Loaded user profile:")
        print(self.orion.profile)


    def show_workspace(self):
        """Display active workspace information."""
        details = self.orion.workspace_manager.describe()
        print(f"Active Workspace: {details['root']}")
        print(f"Top-level directories: {details['directories']}")
        print(f"Top-level files: {details['files']}")

    def set_workspace(self, path: str):
        """Select a new active workspace."""
        if not path:
            print("Usage: workspace <path>")
            return

        try:
            selected = self.orion.workspace_manager.set_workspace(path)
        except (FileNotFoundError, NotADirectoryError) as exc:
            print(f"Workspace Error: {exc}")
            return

        self.orion.project_context.bind(selected)
        self.orion.task_manager.bind(selected)
        self.orion.codex_bridge.bind(selected)
        self.orion.conversation.bind(selected)
        self.orion.knowledge_index.bind(selected)
        self.orion.action_history.bind(selected)
        self.orion.application_catalog.bind(selected)
        self.orion.companion_settings.bind(selected)
        self.orion.action_trust.bind(selected)
        self.orion.git_service.rebind(selected)
        print(f"Active workspace changed to: {selected}")
        if self.orion.project_context.initialized:
            print("Project memory recognized. Use 'project resume' to continue where you left off.")
            rules = self.orion.project_context.rules()
            if rules:
                print(f"Loaded {len(rules)} mandatory project rule(s).")

    def list_workspace(self, path: str = "."):
        """List entries inside the active workspace."""
        try:
            entries = self.orion.workspace_manager.list_entries(path or ".")
        except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
            print(f"Workspace Error: {exc}")
            return

        if not entries:
            print("Workspace directory is empty.")
            return

        for entry in entries:
            marker = "[DIR] " if entry.is_directory else "      "
            size = "" if entry.size_bytes is None else f" ({entry.size_bytes} bytes)"
            print(f"{marker}{entry.relative_path}{size}")


    def code_read(self, path: str):
        """Read a source file through the Code Skill."""
        if not path:
            print("Usage: code read <file>")
            return
        try:
            content = self.orion.code_skill.read_file(path)
        except (FileNotFoundError, IsADirectoryError, PermissionError, ValueError) as exc:
            print(f"Code Skill Error: {exc}")
            return
        print(f"--- {path} ---")
        print(content)

    def code_info(self, path: str):
        """Display source file metadata."""
        if not path:
            print("Usage: code info <file>")
            return
        try:
            info = self.orion.code_skill.inspect_file(path)
        except (FileNotFoundError, IsADirectoryError, PermissionError, ValueError) as exc:
            print(f"Code Skill Error: {exc}")
            return
        print(f"File: {info.relative_path}")
        print(f"Language: {info.language}")
        print(f"Size: {info.size_bytes} bytes")
        print(f"Lines: {info.line_count}")

    def code_tree(self, path: str = "."):
        """Display a compact workspace tree."""
        try:
            lines = self.orion.code_skill.tree(path or ".")
        except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
            print(f"Code Skill Error: {exc}")
            return
        if not lines:
            print("No files found.")
            return
        for line in lines:
            print(line)


    def remember(self, payload: str):
        """Store a key/value pair for the current process."""
        parts = payload.split(maxsplit=1)
        if len(parts) != 2:
            print("Usage: remember <key> <value>")
            return
        key, value = parts
        try:
            normalized = self.orion.session_memory.set(key, value)
        except ValueError as exc:
            print(f"Memory Error: {exc}")
            return
        print(f"Remembered: {normalized} = {value.strip()}")

    def recall(self, key: str):
        """Recall one session-memory value."""
        try:
            value = self.orion.session_memory.get(key)
        except ValueError as exc:
            print(f"Memory Error: {exc}")
            return
        if value is None:
            print(f"Memory not found: {key}")
            return
        print(f"{key.strip().lower()} = {value}")

    def show_memory(self):
        """Display a snapshot of current session memory."""
        items = self.orion.session_memory.all()
        if not items:
            print("Session memory is empty.")
            return
        print("Session Memory:")
        for key, value in items.items():
            print(f"  {key} = {value}")

    def forget(self, key: str):
        """Delete one session-memory value."""
        try:
            deleted = self.orion.session_memory.delete(key)
        except ValueError as exc:
            print(f"Memory Error: {exc}")
            return
        if deleted:
            print(f"Forgot: {key.strip().lower()}")
        else:
            print(f"Memory not found: {key}")

    def clear_memory(self):
        """Clear session memory after explicit CLI confirmation."""
        response = input("Clear session memory? (y/n): ").strip().lower()
        if response not in {"y", "yes"}:
            print("Session memory was not cleared.")
            return
        count = self.orion.session_memory.clear()
        print(f"Cleared {count} session memory item(s).")

    def task_list(self):
        """List strict project-local tasks for the active workspace."""
        try:
            tasks = self.orion.task_manager.all()
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Task Manager Error: {exc}")
            return
        print("Project Tasks")
        print("-" * 72)
        if not tasks:
            print("No tasks have been created yet.")
            print('Create one with: task create "<goal>"')
            return
        for task in tasks:
            assignment = task.assigned_agent or task.assigned_role or "unassigned"
            print(
                f"  {task.task_id:<34} {task.status.replace('_', ' ').title():<12} "
                f"{assignment} | {task.goal[:70]}"
            )

    def task_create(self, payload: str):
        goal = payload.strip()
        if goal[:1] in {'"', "'"}:
            if len(goal) < 2 or goal[-1] != goal[0]:
                print("Could not read task goal: closing quote is missing.")
                return
            goal = goal[1:-1].strip()
        if not goal:
            print('Usage: task create "<goal>"')
            return
        try:
            task = self.orion.task_manager.create(goal)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Task Manager Error: {exc}")
            return
        print(f"Task created: {task.task_id}")
        print(f"Status: {task.status.replace('_', ' ').title()}")
        print("Approval: Pending")
        print("No planning or implementation has started.")

    def task_show(self, task_id: str):
        try:
            task = self.orion.task_manager.get(task_id)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Task Manager Error: {exc}")
            return
        self._render_project_task(task)

    def task_approve(self, task_id: str):
        try:
            task = self.orion.task_manager.approve(task_id)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Task Manager Error: {exc}")
            return
        print(f"Task approved: {task.task_id}")
        print("Status: Ready")
        print("Approval does not start planning, tools, or implementation.")

    def task_cancel(self, task_id: str):
        try:
            task = self.orion.task_manager.cancel(task_id)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Task Manager Error: {exc}")
            return
        print(f"Task cancelled: {task.task_id}")
        print("No implementation was performed.")

    def task_events(self, task_id: str):
        try:
            task = self.orion.task_manager.get(task_id)
            events = self.orion.task_manager.events(task.task_id)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Task Manager Error: {exc}")
            return
        print(f"Task Events: {task.task_id}")
        print("-" * 72)
        for event in events:
            previous = event.previous_status or "none"
            print(
                f"  {event.timestamp} | {event.event_type:<16} "
                f"{previous} -> {event.status} | {event.detail}"
            )

    def task_link_plan(self, payload: str):
        parts = payload.split()
        if len(parts) != 2:
            print("Usage: task link-plan <task-id> <team-task-id>")
            return
        task_id, team_task_id = parts
        try:
            team_task = self.orion.team.task(team_task_id)
            if team_task.status != "awaiting_approval":
                raise ValueError("Only an AI Team plan awaiting approval can be linked.")
            task = self.orion.task_manager.link_team_plan(
                task_id,
                team_task.task_id,
                summary=team_task.goal[:500],
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Task Manager Error: {exc}")
            return
        print(f"Linked AI Team plan {team_task.task_id} to {task.task_id}.")
        print("The plan remains an artifact; no implementation has started.")

    @staticmethod
    def _render_project_task(task):
        print("Project Task")
        print("-" * 72)
        print(f"ID: {task.task_id}")
        print(f"Goal: {task.goal}")
        print(f"Status: {task.status.replace('_', ' ').title()}")
        print(f"Approval: {task.approval.title()}")
        print(f"Assigned Role: {task.assigned_role or 'Unassigned'}")
        print(f"Assigned Agent: {task.assigned_agent or 'Unassigned'}")
        if task.dependencies:
            print(f"Dependencies: {', '.join(task.dependencies)}")
        else:
            print("Dependencies: none")
        if task.artifacts:
            print("Artifacts:")
            for artifact in task.artifacts:
                print(
                    f"  - {artifact.kind}: {artifact.reference} | {artifact.summary}"
                )
        else:
            print("Artifacts: none")
        print(f"Created: {task.created_at}")
        print(f"Updated: {task.updated_at}")


    def project_init(self):
        """Initialize persistent metadata for the active workspace."""
        try:
            existed = self.orion.project_context.initialized
            data = self.orion.project_context.initialize(
                name=self.orion.workspace_manager.root.name,
                description="Personal AI Operating System",
                version=self.orion.version,
                phase="Intelligence Core",
                current_goal="Build File Search",
                preferred_model=self.orion.ai_provider.name(),
            )
        except (OSError, ValueError) as exc:
            print(f"Project Context Error: {exc}")
            return
        print("Project context already initialized." if existed else "Project context initialized.")
        print(f"Project: {data['name']}")
        print(f"Location: {self.orion.project_context.context_dir}")

    def project_status(self, verbose: bool = False):
        """Show persistent project metadata."""
        try:
            data = self.orion.project_context.project()
            metrics = self.orion.project_context.metrics()
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"Project Context Error: {exc}")
            return
        print("Project Status:")
        context_matches = self.orion.project_context.matches_workspace()
        print(f"  Active Workspace: {self.orion.workspace_manager.root}")
        print(f"  Context State: {'Current' if context_matches else 'Copied or stale metadata'}")
        print(f"  Name: {data.get('name', '') if context_matches else self.orion.workspace_manager.root.name}")
        print(f"  Version: {data.get('version', '')}")
        print(f"  Phase: {data.get('phase', '')}")
        print(f"  Current Goal: {data.get('current_goal', '')}")
        print(f"  Workspace: {self.orion.project_context.workspace_root}")
        print(
            f"  Tasks: {metrics.get('tasks_open', 0)} open, "
            f"{metrics.get('tasks_completed', 0)} completed, "
            f"{metrics.get('tasks_cancelled', 0)} cancelled"
        )
        print(f"  History Entries: {metrics.get('history_entries', 0)}")
        try:
            index = self.orion.knowledge_index.ensure_fresh()
            stats = index.get("stats", {})
            print(f"  Index: Fresh ({index.get('built_at', '')})")
            print(f"  Files: {stats.get('files', 0)}")
            print(f"  Python Files: {stats.get('python_files', 0)}")
            print(f"  Classes: {stats.get('classes', 0)}")
            print(f"  Functions: {stats.get('functions', 0)}")
            print(f"  Tests: {stats.get('tests', 0)}")
            print(f"  TODO/FIXME: {stats.get('todos', 0)}")
        except (OSError, ValueError) as exc:
            print(f"  Index: Unavailable ({exc})")
        if not context_matches:
            print("  Note: Run 'project init' in a clean workspace or update project fields before relying on saved project context.")
        if verbose:
            print(f"  Description: {data.get('description', '')}")
            print(f"  Preferred Model: {data.get('preferred_model', '')}")
            print(f"  Created: {data.get('created_at', '')}")
            print(f"  Updated: {data.get('updated_at', '')}")

    def project_set(self, payload: str):
        """Set one persistent project field."""
        parts = payload.split(maxsplit=1)
        if len(parts) != 2:
            print("Usage: project set <field> <value>")
            return
        field, value = parts
        try:
            data = self.orion.project_context.set_field(field, value)
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"Project Context Error: {exc}")
            return
        normalized = "current_goal" if field.lower() == "goal" else field.lower()
        print(f"Project updated: {normalized} = {data.get(normalized, value)}")

    def project_note(self, text: str):
        """Append a note to the active project's notes file."""
        try:
            self.orion.project_context.add_note(text)
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"Project Context Error: {exc}")
            return
        print("Project note added.")

    def project_checkpoint(self, summary: str):
        """Save a concise project handoff checkpoint."""
        try:
            data = self.orion.project_context.project()
            checkpoint = self.orion.project_context.add_checkpoint(
                summary,
                current_task=data.get("current_goal", ""),
                next_step=summary,
            )
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"Project Context Error: {exc}")
            return
        print(f"Project checkpoint saved: {checkpoint['summary']}")
        print(f"Location: {self.orion.project_context.database_path}")

    def project_resume(self):
        """Display the active project's latest handoff and rules."""
        try:
            data = self.orion.project_context.resume()
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"Project Context Error: {exc}")
            return
        project = data["project"]
        checkpoint = data["checkpoint"]
        print(f"Project recognized: {project.get('name', '')}")
        print(f"Current goal: {project.get('current_goal', '')}")
        if checkpoint:
            print(f"Last checkpoint: {checkpoint.get('summary', '')}")
            if checkpoint.get("current_task"):
                print(f"Current task: {checkpoint['current_task']}")
            if checkpoint.get("next_step"):
                print(f"Next step: {checkpoint['next_step']}")
        else:
            print("No checkpoint has been saved yet.")
        rules = data["rules"]
        if rules:
            print("Mandatory project rules:")
            for item in rules:
                print(f"  [{item['id']}] {item['rule']}")

    def project_rules(self):
        try:
            rules = self.orion.project_context.rules()
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"Project Context Error: {exc}")
            return
        if not rules:
            print("No project rules defined.")
            return
        print("Mandatory Project Rules:")
        for item in rules:
            print(f"  [{item['id']}] {item['rule']}")

    def project_rule_add(self, rule: str):
        try:
            item = self.orion.project_context.add_rule(rule)
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"Project Context Error: {exc}")
            return
        print(f"Project rule added [{item['id']}]: {item['rule']}")

    def project_rule_remove(self, value: str):
        try:
            rule_id = int(value)
            removed = self.orion.project_context.remove_rule(rule_id)
        except (ValueError, OSError, FileNotFoundError) as exc:
            print(f"Project Context Error: {exc}")
            return
        print(f"Project rule removed: {rule_id}" if removed else f"Project rule not found: {rule_id}")


    def index_build(self):
        """Build the active workspace knowledge index."""
        try:
            data = self.orion.knowledge_index.build()
        except (OSError, ValueError) as exc:
            print(f"Knowledge Index Error: {exc}")
            return
        stats = data["stats"]
        print("Knowledge index built.")
        print(f"  Files: {stats['files']}")
        print(f"  Python files: {stats['python_files']}")
        print(f"  Classes: {stats['classes']}")
        print(f"  Functions: {stats['functions']}")
        print(f"  Imports: {stats['imports']}")
        print(f"  TODO/FIXME items: {stats['todos']}")
        print(f"  Test files: {stats['tests']}")

    def index_status(self):
        """Show knowledge index metadata."""
        try:
            status = self.orion.knowledge_index.status()
        except (FileNotFoundError, ValueError) as exc:
            print(f"Knowledge Index Error: {exc}")
            return
        print("Knowledge Index:")
        print(f"  Built: {status['built_at']}")
        print(f"  Files: {status['files']}")
        print(f"  Classes: {status['classes']}")
        print(f"  Functions: {status['functions']}")
        print(f"  TODO/FIXME items: {status['todos']}")
        print(f"  Test files: {status['tests']}")

    def index_symbols(self, kind: str):
        try:
            items = self.orion.knowledge_index.symbols(kind)
        except (FileNotFoundError, ValueError) as exc:
            print(f"Knowledge Index Error: {exc}")
            return
        label = "Classes" if kind == "class" else "Functions"
        print(f"Indexed {label} ({len(items)}):")
        for item in items:
            print(f"  {item['name']} - {item['path']}:{item['line']}")

    def index_todos(self):
        try:
            items = self.orion.knowledge_index.todos()
        except (FileNotFoundError, ValueError) as exc:
            print(f"Knowledge Index Error: {exc}")
            return
        print(f"Indexed TODOs ({len(items)}):")
        for item in items:
            print(f"  [{item['marker']}] {item['path']}:{item['line']} - {item['text']}")

    def index_imports(self):
        try:
            items = self.orion.knowledge_index.imports()
        except (FileNotFoundError, ValueError) as exc:
            print(f"Knowledge Index Error: {exc}")
            return
        print(f"Indexed Imports ({len(items)}):")
        for item in items:
            print(f"  {item['module']} - {item['path']}:{item['line']}")

    def index_find(self, query: str):
        try:
            items = self.orion.knowledge_index.query(query)
        except (FileNotFoundError, ValueError) as exc:
            print(f"Knowledge Index Error: {exc}")
            return
        if not items:
            print(f"No indexed matches for: {query}")
            return
        print(f"Found {len(items)} indexed match(es):")
        for item in items:
            location = item.get("path", "")
            if item.get("line"):
                location += f":{item['line']}"
            name = item.get("name") or item.get("text") or location
            print(f"  [{item['type']}] {name} - {location}")


    def action_echo(self, text: str):
        if not text:
            print("Usage: action echo <text>")
            return
        result = self.orion.action_service.run("echo", {"message": text})
        print(result.output if result.success else f"Action failed: {result.error}")

    def action_request(self, text: str):
        if not text:
            print("Usage: action request <text>")
            return
        action = self.orion.action_service.create("protected_echo", {"message": text})
        print("Action awaiting approval:")
        print(f"  ID: {action.id}")
        print(f"  Type: {action.type}")
        print(f"  Message: {text}")
        print(f"Approve with: action approve {action.id}")

    def _pending_action_by_reference(self, reference: str):
        """Resolve a friendly queue number or a developer action ID."""
        pending = self.orion.action_service.pending()
        value = reference.strip()
        if value.isdigit():
            index = int(value) - 1
            if index < 0 or index >= len(pending):
                raise KeyError(f"No pending action numbered {value}.")
            return pending[index]
        return self.orion.action_service.get(value)

    def _describe_action(self, action) -> str:
        if action.type == "open_app":
            return f"Open {action.parameters.get('display_name') or action.parameters.get('name', 'application')}"
        if action.type == "protected_echo":
            return f"Protected message: {action.parameters.get('message', '')}"
        return f"{action.type}: {action.parameters}"

    def action_pending(self):
        actions = self.orion.action_service.pending()
        if not actions:
            print("Nothing is waiting for your approval.")
            return
        print("Pending Actions:")
        developer = self.orion.companion_settings.developer_mode
        for index, action in enumerate(actions, 1):
            suffix = f"  [{action.id}]" if developer else ""
            print(f"  {index}. {self._describe_action(action)}{suffix}")
        print("Use 'action approve <number>' or 'action deny <number>'.")

    def action_approve(self, reference: str):
        try:
            action = self._pending_action_by_reference(reference)
            self.orion.action_service.approve(action.id)
            result = self.orion.action_service.execute(action)
        except (KeyError, RuntimeError, PermissionError) as exc:
            print(f"Action Error: {exc}")
            return
        print(result.output if result.success else f"I couldn't complete that action: {result.error}")

    def action_deny(self, reference: str):
        try:
            action = self._pending_action_by_reference(reference)
            self.orion.action_service.deny(action.id)
        except (KeyError, RuntimeError) as exc:
            print(f"Action Error: {exc}")
            return
        print("Okay, I won't do that.")
        if self.orion.companion_settings.developer_mode:
            print(f"Action denied: {action.id}")

    def action_history(self):
        entries = self.orion.action_history.entries(limit=10)
        if not entries:
            print("Action history is empty.")
            return
        print("Recent Actions:")
        for entry in entries:
            action = entry["action"]
            print(f"  {entry['timestamp']} [{entry['event']}] {action['type']} - {action['status']}")

    def apps_scan(self):
        try:
            applications = self.orion.discovery_service.scan()
        except (OSError, ValueError) as exc:
            print(f"Discovery Error: {exc}")
            return
        print(f"Application discovery complete: {len(applications)} found.")

    def apps_list(self):
        applications = self.orion.application_catalog.applications()
        if not applications:
            print("No applications are cataloged. Run 'apps scan'.")
            return
        print(f"Discovered Applications ({len(applications)}):")
        for application in applications:
            print(f"  {application.name} [{application.source}]")

    def apps_find(self, query: str):
        matches = self.orion.application_matcher.find(query)
        if not matches:
            print(f"No catalog matches found for: {query}")
            return
        print(f"Application Matches for '{query}':")
        for index, match in enumerate(matches, 1):
            print(f"  {index}. {match.application.name} ({match.score:.0%}, {match.matched_by})")

    def app_alias(self, payload: str):
        if "=" not in payload:
            print("Usage: app alias <alias> = <application name>")
            return
        alias, query = (part.strip() for part in payload.split("=", 1))
        matches = self.orion.application_matcher.find(query, limit=2)
        if not alias or not matches:
            print(f"Could not resolve application: {query}")
            return
        if len(matches) > 1 and matches[0].score - matches[1].score < 0.08:
            print("That application name is ambiguous. Use 'apps find <name>' and provide a more specific name.")
            return
        self.orion.application_catalog.set_alias(alias, matches[0].application.path)
        print(f"Learned application alias: {alias} -> {matches[0].application.name}")

    def open_app(self, query: str):
        if not query:
            print("Usage: open <application>")
            return

        matches = self.orion.application_matcher.find(query, limit=2)
        resolved = self.orion.application_matcher.resolve(query)
        display_name = resolved.application.name if resolved else (matches[0].application.name if matches else query)
        trust_target = resolved.application.path if resolved else f"search:{query.strip().lower()}"

        action = self.orion.action_service.create(
            "open_app",
            {
                "name": query,
                "display_name": display_name,
                "trust_target": trust_target,
                "allow_search_fallback": True,
            },
        )

        if self.orion.action_trust.is_trusted("open_app", trust_target):
            self.orion.action_service.approve(action.id)
            result = self.orion.action_service.execute(action)
            print(result.output if result.success else f"I couldn't open {display_name}: {result.error}")
            return

        if resolved:
            print(f"I found {display_name}.")
        elif matches:
            print(f"The closest match I found is {display_name}.")
        else:
            print(f"I couldn't find {query} in your application library.")
            print("I can try Windows Search instead.")

        if self.orion.companion_settings.developer_mode:
            print(f"Action ID: {action.id}")
            print(f"Match confidence: {matches[0].score:.0%}" if matches else "Match confidence: Windows Search fallback")

        response = input("Open it? [Y] Yes  [N] No  [A] Always allow  [D] Details: ").strip().lower()
        if response in {"", "y", "yes"}:
            self.orion.action_service.approve(action.id)
            result = self.orion.action_service.execute(action)
            print(result.output if result.success else f"I couldn't open {display_name}: {result.error}")
            return
        if response in {"a", "always"}:
            self.orion.action_trust.trust("open_app", trust_target)
            self.orion.action_service.approve(action.id)
            result = self.orion.action_service.execute(action)
            if result.success:
                print(f"Got it. I'll open {display_name} without asking next time.")
                print(result.output)
            else:
                print(f"I couldn't open {display_name}: {result.error}")
            return
        if response in {"d", "details", "?"}:
            print(f"Action: open_app")
            print(f"Target: {query}")
            print(f"Resolved application: {display_name}")
            print(f"Internal ID: {action.id}")
            print("The action is still pending. Use 'action pending' to review it.")
            return

        self.orion.action_service.deny(action.id, "Declined in Companion prompt.")
        print("Okay, I won't open it.")

    def set_developer_mode(self, enabled: bool):
        self.orion.companion_settings.set_developer_mode(enabled)
        if enabled:
            print("Developer Mode is on. Orion will show internal action details.")
        else:
            print("Developer Mode is off. Companion Mode is active.")

    def show_companion_settings(self):
        mode = "ON" if self.orion.companion_settings.developer_mode else "OFF"
        print("Companion Settings:")
        print(f"  Developer Mode: {mode}")
        print(f"  Trusted Actions: {len(self.orion.action_trust.entries())}")
        print("  Approval Prompt: Y / N / A / Details")

    def show_trust(self):
        entries = self.orion.action_trust.entries()
        if not entries:
            print("No actions are currently trusted.")
            return
        print("Trusted Actions:")
        for index, (action_type, target) in enumerate(entries, 1):
            label = target
            if action_type == "open_app":
                app = next((a for a in self.orion.application_catalog.applications() if a.path.lower() == target.lower()), None)
                label = app.name if app else target
            print(f"  {index}. {action_type}: {label}")

    def revoke_trust(self, query: str):
        if not query:
            print("Usage: trust revoke <application>")
            return
        resolved = self.orion.application_matcher.resolve(query)
        target = resolved.application.path if resolved else f"search:{query.strip().lower()}"
        if self.orion.action_trust.revoke("open_app", target):
            print(f"I'll ask before opening {resolved.application.name if resolved else query} again.")
        else:
            print(f"No trusted application matched: {query}")

    def show_history(self):
        """Show release milestones and persistent project history."""
        print("Orion Development History")
        print("=" * 50)
        print("v0.1.0 - First Light")
        print("  Foundation: Core, Configuration, Profile, Router, Brain, AI Providers, Identity")
        print("v0.2.0 - Intelligence Core")
        print("  Workspace Manager, Code Skill, Session Memory, Service Registry")
        print("v0.2.1 - Project Memory")
        print("  Persistent Project Context, History, About, Documentation")
        print("v0.2.2 - Open Constellation")
        print("  Plugin contracts, discovery, lifecycle management, Code Plugin")
        print("v0.2.3 - Pathfinder")
        print("  Safe workspace content search, file search, regex and type filters")
        print("v0.2.4 - Continuum")
        print("  Persistent conversation history and context-aware AI requests")
        print("v0.2.5 - Waypoint")
        print("  Portable project checkpoints, SQLite memory, and mandatory project rules")
        print("v0.2.6 - Atlas (Current)")
        print("  Portable workspace knowledge index for files, symbols, imports, tests, and TODOs")
        if not self.orion.project_context.initialized:
            print("\nProject history is not initialized. Run 'project init'.")
            return
        try:
            entries = self.orion.project_context.history()
        except (ValueError, OSError) as exc:
            print(f"Project Context Error: {exc}")
            return
        print("\nProject History")
        for entry in entries:
            print(f"  {entry.get('timestamp', '')} - {entry.get('summary', '')}")


    def show_conversation(self, limit_value: str = "10"):
        """Display recent persisted conversation messages."""
        try:
            limit = int(limit_value or "10")
            messages = self.orion.conversation.recent(limit)
        except (ValueError, OSError) as exc:
            print(f"Conversation Error: {exc}")
            return
        if not messages:
            print("Conversation history is empty.")
            return
        print(f"Recent Conversation ({len(messages)} message(s)):")
        for message in messages:
            print(f"  [{message.timestamp}] {message.role.title()}: {message.content}")

    def search_conversation(self, query: str):
        """Search persisted conversation messages."""
        try:
            messages = self.orion.conversation.search(query)
        except (ValueError, OSError) as exc:
            print(f"Conversation Error: {exc}")
            return
        if not messages:
            print(f"No conversation matches found for: {query}")
            return
        print(f"Found {len(messages)} conversation match(es):")
        for message in messages:
            print(f"  [{message.timestamp}] {message.role.title()}: {message.content}")

    def clear_conversation(self):
        """Clear today's conversation after explicit confirmation."""
        response = input("Clear today's conversation? (y/n): ").strip().lower()
        if response not in {"y", "yes"}:
            print("Conversation history was not cleared.")
            return
        try:
            count = self.orion.conversation.clear_today()
        except (ValueError, OSError) as exc:
            print(f"Conversation Error: {exc}")
            return
        print(f"Cleared {count} conversation message(s).")

    def show_about(self):
        """Show Orion identity, architecture, and current capabilities."""
        print("=" * 50)
        print(f"{self.orion.name:^50}")
        print("=" * 50)
        print(f"Version: {self.orion.version}")
        print(f"Codename: {self.orion.codename}")
        print("Author: Daniel Cannady")
        print("Architecture: Modular services and skills")
        print(f"Services: {len(self.orion.services)}")
        print(f"Plugins: {self.orion.plugin_manager.loaded_count()} loaded")
        print("Skills: Code and Search supplied by plugins")
        print("Tests: verified for v0.2.5")
        print(f"Workspace: {self.orion.workspace_manager.root}")
        if self.orion.project_context.initialized:
            try:
                data = self.orion.project_context.project()
                metrics = self.orion.project_context.metrics()
                print(f"Current Project: {data.get('name', '')}")
                print(f"Current Goal: {data.get('current_goal', '')}")
                print(f"History Entries: {metrics.get('history_entries', 0)}")
            except (ValueError, OSError):
                print("Current Project: Project data needs repair")
        else:
            print("Current Project: Not initialized")
        print(f"Status: {self.orion.status}")
        print("=" * 50)

    def ask_ai(self, prompt: str):
        """Send a prompt to Orion's configured AI provider."""
        if not prompt:
            print("Usage: ask <your question>")
            return

        print("Analyzing request...")

        try:
            response = self.orion.brain.ask(prompt)
        except Exception as exc:
            print(f"AI Error: {exc}")
            return

        if response:
            print()
            print(response)
        else:
            print("No response received from AI provider.")
    def git_status(self):
        try:
            status = self.orion.git_service.status()
        except Exception as exc:
            print(f"Git unavailable: {exc}")
            return
        print("Git Status")
        print("-" * 62)
        print(f"Branch    : {status.branch}")
        print(f"Upstream  : {status.upstream or 'Not configured'}")
        print(f"Ahead     : {status.ahead}")
        print(f"Behind    : {status.behind}")
        print(f"Working   : {'Changes present' if status.dirty else 'Clean'}")
        for line in status.changes[:20]:
            print(f"  {line}")

    def git_log(self):
        try:
            print(self.orion.git_service.log() or "No commits found.")
        except Exception as exc:
            print(f"Could not read Git history: {exc}")

    def git_diff(self, staged=False):
        try:
            print(self.orion.git_service.diff(staged=staged) or "No differences.")
        except Exception as exc:
            print(f"Could not read Git differences: {exc}")

    def _confirm_sensitive_git(self, description):
        print(f"Sensitive action: {description}")
        return input("Approve? [y/N]: ").strip().lower() in {"y", "yes"}

    def git_pull(self):
        if not self._confirm_sensitive_git("fast-forward pull from origin"):
            print("Git pull cancelled.")
            return
        try:
            print(self.orion.git_service.pull() or "Already up to date.")
        except Exception as exc:
            print(f"Git pull failed: {exc}")

    def git_push(self):
        if not self._confirm_sensitive_git("push the current branch to origin"):
            print("Git push cancelled.")
            return
        try:
            print(self.orion.git_service.push() or "Push completed.")
        except Exception as exc:
            print(f"Git push failed: {exc}")

    def update_check(self, apply=False):
        try:
            check = self.orion.update_service.check(fetch=True)
        except Exception as exc:
            print(f"Update check failed: {exc}")
            return
        print("Orion Update")
        print("-" * 62)
        print(f"Current   : {check.current[:12]}")
        print(f"Latest    : {check.latest[:12]}")
        print(f"Channel   : {check.channel}")
        print(f"Available : {'Yes' if check.available else 'No'}")
        if check.published_at:
            print(f"Published : {check.published_at}")
        print("Method    : Signed-in-transit package (no Git pull)")
        if not apply:
            return
        if not check.available:
            print("Orion is already up to date.")
            return
        if not self._confirm_sensitive_git("back up and replace Orion application files"):
            print("Update cancelled.")
            return
        try:
            result = self.orion.update_service.apply(check)
            print(f"Updated   : {result.previous[:12]} -> {result.current[:12]}")
            print(f"Backup    : {result.backup}")
            print(f"SHA-256   : {result.package_sha256}")
            print("Your ~/.orion data was not modified.")
            print("Restart Orion to load the updated application.")
        except Exception as exc:
            print(f"Update failed: {exc}")

    def update_rollback(self):
        if not self._confirm_sensitive_git("restore the most recent Orion application backup"):
            print("Rollback cancelled.")
            return
        try:
            result = self.orion.update_service.rollback()
            print("Orion Rollback")
            print("-" * 62)
            print(f"Restored  : {result.current[:12]}")
            print(f"Safety backup: {result.backup}")
            print("Restart Orion to load the restored application.")
        except Exception as exc:
            print(f"Rollback failed: {exc}")
