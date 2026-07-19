"""
Orion Command Router

Responsible for:
- Receiving user commands
- Routing commands to the correct Orion subsystem
- Keeping command handling out of main.py
"""

from getpass import getpass
from pathlib import Path
import shlex

from orion.agents import AgentDefinition, AgentPermissions
from orion.services.team import TeamPlanningError
from orion.services.codex_bridge import CodexBridgeError, PlanSnapshot
from orion.services.execution_engines import ExecutionEngineUnavailable
from orion.services.workspace_snapshot import WorkspaceRollbackError, WorkspaceSnapshotError
from orion.services.email import redact_email_text


class CommandRouter:
    """Routes commands entered into Orion's CLI."""

    def __init__(
        self,
        orion,
        *,
        interactive_team_approval: bool = False,
        team_approval_input=None,
    ):
        self.orion = orion
        self.interactive_team_approval = bool(interactive_team_approval)
        self._team_approval_input = team_approval_input

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

        elif command_lower == "connect add gmail":
            self.email_connect("gmail")

        elif command_lower in {"connect add microsoft", "connect add outlook"}:
            self.email_connect("microsoft")

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

        elif command_lower in {"email", "email status"}:
            self.email_status()

        elif command_lower == "email providers":
            self.email_providers()

        elif command_lower.startswith("email connect "):
            self.email_connect(raw_command[len("email connect "):].strip())

        elif command_lower.startswith("email disconnect "):
            self.email_disconnect(raw_command[len("email disconnect "):].strip())

        elif command_lower.startswith("email configure "):
            self.email_configure(raw_command[len("email configure "):].strip())

        elif command_lower == "email accounts":
            self.email_accounts()

        elif command_lower == "email inbox" or command_lower.startswith("email inbox "):
            self.email_inbox(raw_command[len("email inbox"):].strip())

        elif command_lower == "email unread" or command_lower.startswith("email unread "):
            self.email_unread(raw_command[len("email unread"):].strip())

        elif command_lower.startswith("email search "):
            self.email_search(raw_command[len("email search "):].strip())

        elif command_lower.startswith("email read "):
            self.email_read(raw_command[len("email read "):].strip())

        elif command_lower.startswith("email thread "):
            self.email_thread(raw_command[len("email thread "):].strip())

        elif command_lower == "email summarize" or command_lower.startswith("email summarize "):
            self.email_summarize(raw_command[len("email summarize"):].strip())

        elif command_lower == "email use" or command_lower.startswith("email use "):
            self.email_use(raw_command[len("email use"):].strip())

        elif command_lower in {"email draft", "email compose", "email send"} or command_lower.startswith((
            "email reply ", "email forward ", "email archive ", "email trash ",
            "email mark read ", "email mark unread ", "email attachment ",
        )):
            self.email_write_unavailable()

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

        elif command_lower == "team role show":
            print("Usage: team role show <role>")

        elif command_lower.startswith("team role show "):
            self.show_team_role(raw_command[len("team role show "):].strip())

        elif command_lower == "team role set":
            print("Usage: team role set <role> <provider:model|engine>")

        elif command_lower.startswith("team role set "):
            self.set_team_role(raw_command[len("team role set "):].strip())

        elif command_lower == "team role reset":
            print("Usage: team role reset <role>")

        elif command_lower.startswith("team role reset "):
            self.reset_team_role(raw_command[len("team role reset "):].strip())

        elif command_lower == "team plan":
            print('Usage: team plan [--manual] "<goal>"')

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

        elif command_lower == "team test":
            print("Usage: team test <run-id|last>")

        elif command_lower == "team test last":
            self.team_test("last")

        elif command_lower.startswith("team test "):
            self.team_test(raw_command[len("team test "):].strip())

        elif command_lower == "team rollback":
            print("Usage: team rollback <run-id>")

        elif command_lower.startswith("team rollback "):
            self.team_rollback(raw_command[len("team rollback "):].strip())

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

        elif self._looks_like_email(raw_command):
            self.show_email_request(raw_command)

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
        print('    team plan "<goal>"         Plan, then offer interactive approval')
        print('    team plan --manual "<goal>" Plan without an approval prompt')
        print("    team roles                 Show all model and engine assignments")
        print("    team role show <role>      Inspect one role assignment")
        print("    team role set <role> <provider:model|engine> Persist an assignment")
        print("    team role reset <role>     Restore the dynamic default")
        print("    team status <task-id>      Reopen a persisted team plan")
        print("    team approve <task-id>     Bind approval to this plan and workspace")
        print("    team implement <id> <approval> Run one bounded local Codex execution")
        print("    team run <run-id>          Show implementation and validation results")
        print("    team test <run-id>         Rerun bounded automatic validation only")
        print("    team test last             Validate the newest eligible workspace run")
        print("    team rollback <run-id>     Safely restore one reviewed workspace run")
        print("    execution status           Diagnose CLI and desktop execution engines")
        print()
        print("  Email & Connect")
        print("    email status|providers     Show Gmail and Microsoft Mail health")
        print("    email connect <provider>   Approve read-only Mail access")
        print("    email disconnect <provider> Remove local Mail authorization")
        print("    email accounts             Show connected account identities")
        print("    email inbox [provider]     List a bounded recent inbox")
        print("    email unread [provider]    List bounded unread mail")
        print('    email search "<query>" [provider] Search connected mail')
        print("    email read <provider:id>   Read safe plain text and attachment metadata")
        print("    email thread <provider:id> Read a bounded conversation")
        print("    email summarize [provider] Summarize bounded unread mail locally")
        print("    connect                    Open the unified Connect Center")
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
        print("    ai provider configure ...  Verify and Vault a cloud AI provider")
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
            ("Email", self.orion.email_service.get_status().message),
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

    @staticmethod
    def _looks_like_email(text: str) -> bool:
        value = text.strip().lower()
        email_phrases = (
            "email", "emails", "inbox", "unread mail", "message from",
            "latest message in this thread",
        )
        return any(phrase in value for phrase in email_phrases)

    def show_email_request(self, request: str):
        """Answer bounded mail questions through EmailService before the AI."""
        result = self.orion.email_service.handle_request(request)
        print(result.output if result.success else f"Email unavailable: {result.error}")

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
        for item in self.orion.connect_service.statuses(refresh_email=True):
            state = "[OK]" if item.healthy else ("[!]" if item.configured else "[--]")
            print(f"  {state} {item.name:<12} {item.detail}")
        print("-" * 62)
        print("Commands: email status | email connect gmail | email connect microsoft")
        print("          email inbox [provider] | email unread [provider]")
        print("          email search <text> [provider] | email read <provider:id>")
        print("          connect add discord | connect health")
        print("          discord send <message> | connect add discord bot")
        print("          Start two-way Discord with: python -m orion.main --discord")

    def connect_health(self):
        print("Connect Health")
        print("-" * 62)
        for item in self.orion.connect_service.statuses(refresh_email=True):
            state = "[OK]" if item.healthy else "[--]"
            print(f"  {state} {item.name:<12} {item.detail}")

        print()
        print(self.orion.email_service.provider_summary(refresh=False))

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

    @staticmethod
    def _email_query_provider(value: str) -> tuple[str, str]:
        try:
            parts = shlex.split(value)
        except ValueError:
            parts = value.split()
        provider = ""
        if len(parts) > 1 and parts[-1].lower() in {
            "gmail", "google", "microsoft", "outlook", "m365", "office365",
        }:
            provider = parts.pop()
        return " ".join(parts).strip(), provider

    def _email_rows(self, page):
        messages = list(page.messages)
        self._last_email_messages = messages
        if not messages:
            print("No matching messages.")
            return messages
        for index, item in enumerate(messages, start=1):
            unread = "*" if item.unread else " "
            important = "!" if item.importance == "high" else " "
            sender = item.sender.address or item.sender.name or "Unknown sender"
            print(
                f"{index:>2}. [{unread}{important}] [{item.provider}] "
                f"{redact_email_text(item.subject, limit=1000)}"
            )
            print(f"    From: {sender}  Date: {item.received_at or 'Unknown'}")
            if item.preview:
                print(f"    {redact_email_text(item.preview, limit=160)}")
            if item.has_attachments:
                print("    Attachments: metadata available; nothing downloaded")
            print(f"    ID: {item.reference}")
        if page.next_page_token:
            print("More messages are available from the provider; Orion displayed the bounded first page.")
        return messages

    def email_status(self):
        print("Email Status")
        print("-" * 62)
        try:
            print(self.orion.email_service.provider_summary(refresh=True))
        except Exception:
            print("Email status could not be refreshed safely. Try again or reconnect the provider.")
        print("Mail access is read-only. Sending and mailbox changes are not enabled.")

    def email_providers(self):
        print(self.orion.email_service.provider_summary(refresh=False))

    def email_connect(self, provider_name: str):
        try:
            key = self.orion.email_service.normalize_provider(provider_name)
            provider = self.orion.email_service.providers[key]
        except (KeyError, ValueError) as exc:
            print(str(exc))
            print("Usage: email connect gmail | microsoft")
            return
        print(f"Connect {provider.display_name}")
        print("Orion will request read-only mail access.")
        if key == "gmail":
            print("Permission: Gmail read-only")
            print(f"OAuth client: {provider.adapter.credentials_path}")
        else:
            print("Permissions: User.Read, Mail.Read, offline_access")
            print("The Outlook desktop application does not authorize Orion.")
            if not provider.adapter.configured:
                print("Microsoft client ID is missing. Run: email configure microsoft")
                return
        answer = input("Open the provider authorization flow? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Email connection cancelled. Existing connections were preserved.")
            return
        try:
            account = self.orion.email_service.connect(key)
            print(f"[OK] {provider.display_name} connected read-only: {account.email_address}")
        except Exception as exc:
            print(f"Could not connect {provider.display_name}: {exc}")
            print("Existing Calendar and Email authorizations were preserved.")

    def email_disconnect(self, provider_name: str):
        try:
            key = self.orion.email_service.normalize_provider(provider_name)
            provider = self.orion.email_service.providers[key]
        except (KeyError, ValueError) as exc:
            print(str(exc))
            return
        answer = input(
            f"Remove Orion's local {provider.display_name} Mail authorization? [y/N]: "
        ).strip().lower()
        if answer not in {"y", "yes"}:
            print("Email disconnect cancelled.")
            return
        try:
            self.orion.email_service.disconnect(key)
            print(f"[OK] {provider.display_name} Mail disconnected locally.")
            print("Calendar authorization was not changed.")
        except Exception as exc:
            print(f"Could not disconnect {provider.display_name}: {exc}")

    def email_configure(self, provider_name: str):
        try:
            key = self.orion.email_service.normalize_provider(provider_name)
            provider = self.orion.email_service.providers[key]
        except (KeyError, ValueError) as exc:
            print(str(exc))
            return
        try:
            if key == "gmail":
                current = str(provider.adapter.credentials_path)
                entered = input(f"Google Desktop OAuth client file [{current}]: ").strip()
                self.orion.email_service.configure_provider(
                    key, credentials_path=entered or current
                )
            else:
                current_id = provider.adapter.client_id
                client_id = input(f"Microsoft Application (client) ID [{current_id}]: ").strip() or current_id
                tenant = input(f"Microsoft tenant [{provider.adapter.oauth.tenant}]: ").strip() or provider.adapter.oauth.tenant
                self.orion.email_service.configure_provider(key, client_id=client_id, tenant=tenant)
            print(f"[OK] {provider.display_name} Mail configuration saved.")
            print(f"Run: email connect {key}")
        except (OSError, ValueError) as exc:
            print(f"Email configuration was not changed: {exc}")

    def email_accounts(self):
        print("Email Accounts")
        print("-" * 62)
        try:
            accounts = self.orion.email_service.accounts(refresh=True)
        except Exception as exc:
            print(f"Could not read connected accounts: {exc}")
            return
        if not accounts:
            print("No email accounts are connected.")
            return
        for account in accounts:
            label = account.display_name or account.email_address
            print(f"  [{account.provider}] {label} <{account.email_address}>")

    def email_inbox(self, provider: str = ""):
        print("Email Inbox")
        print("-" * 62)
        try:
            self._email_rows(self.orion.email_service.inbox(provider))
        except Exception as exc:
            print(f"Could not read email: {exc}")

    def email_unread(self, provider: str = ""):
        print("Unread Email")
        print("-" * 62)
        try:
            count = self.orion.email_service.unread_count(provider, refresh=True)
            print(f"Unread messages: {count}")
            if count:
                self._email_rows(self.orion.email_service.unread(provider))
        except Exception as exc:
            print(f"Could not read unread email: {exc}")

    def email_search(self, query: str):
        query, provider = self._email_query_provider(query)
        if not query:
            print('Usage: email search "<query>" [gmail|microsoft]')
            return
        print(f"Email Search: {query}")
        print("-" * 62)
        try:
            self._email_rows(self.orion.email_service.search(query, provider))
        except Exception as exc:
            print(f"Could not search email: {exc}")

    def _email_reference(self, reference: str) -> str:
        value = reference.strip()
        if value.isdigit() and hasattr(self, "_last_email_messages"):
            index = int(value) - 1
            if 0 <= index < len(self._last_email_messages):
                return self._last_email_messages[index].reference
        return value

    @staticmethod
    def _render_email_message(item):
        summary = item.summary
        sender = summary.sender.address or summary.sender.name or "Unknown sender"
        recipients = ", ".join(address.address for address in summary.recipients) or "Not provided"
        print(redact_email_text(summary.subject, limit=1000))
        print(f"Provider: {summary.provider}  Account: {summary.account_id}")
        print(f"From: {sender}")
        print(f"To: {recipients}")
        if summary.cc:
            print(f"CC: {', '.join(address.address for address in summary.cc)}")
        print(f"Date: {summary.received_at or 'Unknown'}  Importance: {summary.importance}")
        print("-" * 62)
        print(redact_email_text(
            item.body_text or summary.preview or "Plain-text message body unavailable."
        ))
        if item.html_available:
            print("\n[HTML was converted to safe plain text; raw HTML was not rendered.]")
        if item.attachments:
            print("\nAttachments (metadata only; not downloaded):")
            for attachment in item.attachments:
                print(
                    f"  - {attachment.filename} ({attachment.content_type}, "
                    f"{attachment.size_bytes} bytes)"
                )

    def email_read(self, reference: str):
        reference = self._email_reference(reference)
        if not reference:
            print("Usage: email read <number|provider:id>")
            return
        try:
            self._render_email_message(self.orion.email_service.read(reference))
        except Exception as exc:
            print(f"Could not read email message: {exc}")

    def email_thread(self, reference: str):
        reference = self._email_reference(reference)
        if not reference:
            print("Usage: email thread <number|provider:id>")
            return
        try:
            thread = self.orion.email_service.thread(reference)
            print(f"Email Thread: {thread.conversation_id}")
            print("-" * 62)
            for index, message in enumerate(thread.messages, start=1):
                print(f"\nMessage {index} of {len(thread.messages)}")
                self._render_email_message(message)
        except Exception as exc:
            print(f"Could not read email thread: {exc}")

    def email_summarize(self, provider: str = ""):
        try:
            print(self.orion.email_service.summarize(provider, unread_only=True))
        except Exception as exc:
            print(f"Could not summarize email: {exc}")

    def email_use(self, provider: str):
        if not provider:
            print("Usage: email use <gmail|microsoft>")
            return
        try:
            self.orion.email_service.set_default(provider)
            print(f"[OK] Default email provider: {self.orion.email_service.default_provider}")
        except (OSError, ValueError) as exc:
            print(f"Could not change the default email provider: {exc}")

    @staticmethod
    def email_write_unavailable():
        print("Email write actions are not enabled in this read-only release.")
        print("Drafting does not authorize sending. Send, reply, forward, archive, trash,")
        print("mark-state, and attachment download require the Phase B immutable approval workflow.")

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
        self._configure_cloud_provider(key, make_active_default=False)

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
        self._configure_cloud_provider(key, make_active_default=True)

    def _configure_cloud_provider(self, key: str, *, make_active_default: bool) -> None:
        print(f"Configure {key.title()}")
        print("API keys are verified first and saved only through Orion Vault.")
        api_key = getpass("API key: ").strip()
        if not api_key:
            print("Configuration cancelled.")
            return
        try:
            verified = self.orion.vault.verify_provider(key, api_key)
        except (ConnectionError, OSError, ValueError) as exc:
            print(f"Could not configure {key.title()}: {exc}")
            print("Existing credentials and active provider were preserved.")
            return
        models = list(verified.models)
        default_model = self.orion.config_manager.get(
            f"providers.{key}.model",
            "gpt-4.1-mini" if key == "openai" else "gemini-2.5-flash",
        )
        if models:
            print(f"Verified. {len(models)} compatible model(s) discovered.")
            for index, model_name in enumerate(models[:12], start=1):
                marker = " [current]" if model_name == default_model else ""
                print(f"  {index}. {model_name}{marker}")
        choice = input(f"Default model [Enter keeps {default_model}]: ").strip()
        model = default_model
        if choice:
            model = (
                models[int(choice) - 1]
                if choice.isdigit() and 1 <= int(choice) <= len(models)
                else choice
            )
        try:
            self.orion.vault.commit_provider(verified, model=model)
        except (OSError, TypeError, ValueError) as exc:
            print(f"Could not save {key.title()} in Orion Vault: {exc}")
            print("Existing credentials and active provider were preserved.")
            return
        print(f"[OK] {key.title()} connected and saved in Orion Vault.")
        print(f"[OK] Default {key.title()} model: {model}")
        hint = "Y/n" if make_active_default else "y/N"
        make_active = input(f"Make {key.title()} Orion's active provider? [{hint}]: ").strip().lower()
        activate = make_active in {"y", "yes"} or (
            make_active_default and make_active == ""
        )
        if activate:
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
        print("Planning: Architect -> Engineering Reviewer -> explicit Y/N/D approval.")
        print("Implementation Engine -> read-only Automatic Tester -> Awaiting Review.")
        print("Commits, pushes, merges, tags, and pull requests remain disabled.")
        if tasks:
            print("Recent tasks")
            for task in tasks:
                print(f"  {task.task_id} | {task.status.replace('_', ' ').title()} | {task.goal[:60]}")
        else:
            print("No team planning tasks have been created yet.")
        print('-' * 62)
        print(
            'Commands: team plan "<goal>" | team plan --manual "<goal>" | '
            'team roles | team role show/set/reset | '
            'team approve <task-id> | '
            "team implement <task-id> <approval-id> | team run <run-id> | "
            "team test <run-id|last> | team rollback <run-id>"
        )

    def show_team_roles(self):
        print("AI Team Roles")
        print("-" * 96)
        try:
            roles = self.orion.team.roles()
        except (ConnectionError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            print(f"AI Team role configuration is invalid: {exc}")
            return
        print(
            f"{'Role':<23} {'Assignment':<30} {'Availability':<13} "
            f"{'Source':<15} Capability"
        )
        for role in roles:
            availability = "Ready" if role.available else "Unavailable"
            print(
                f"{role.display_name:<23} {role.actual_assignment:<30} "
                f"{availability:<13} {role.source:<15} {role.capability}"
            )
            print(f"  Type: {role.category} | Requested: {role.requested_assignment}")
            print(f"  Fallback: {role.fallback}")
            if role.fallback_reason:
                print(f"  Fallback reason: {role.fallback_reason}")
            if role.availability_reason:
                print(f"  Availability detail: {role.availability_reason}")
            if role.agent_id:
                print(f"  Agent: {role.agent_id} ({role.agent_name})")
        print("Orion owns every prompt, handoff, artifact, approval, and user-facing result.")

    def show_team_role(self, role_name: str):
        registry = self._team_role_registry()
        if registry is None:
            print("AI Team role registry is not available.")
            return None
        try:
            role = registry.show(role_name)
        except (ConnectionError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            print(f"AI Team role configuration is invalid: {exc}")
            return None
        print(f"AI Team Role: {role.display_name}")
        print("-" * 72)
        print(f"Role ID: {role.role}")
        print(f"Type: {role.category}")
        print(f"Requested Assignment: {role.requested_assignment}")
        print(f"Actual Assignment: {role.actual_assignment}")
        print(f"Availability: {'Ready' if role.available else 'Unavailable'}")
        if role.availability_reason:
            print(f"Availability Detail: {role.availability_reason}")
        print(f"Capability: {role.capability}")
        print(f"Fallback: {role.fallback}")
        if role.fallback_reason:
            print(f"Fallback Reason: {role.fallback_reason}")
        print(f"Source: {role.source}")
        if role.agent_id:
            print(f"Agent: {role.agent_id} ({role.agent_name})")
        return role

    def set_team_role(self, payload: str):
        parts = payload.split(maxsplit=1)
        if len(parts) != 2:
            print("Usage: team role set <role> <provider:model|engine>")
            return None
        registry = self._team_role_registry()
        if registry is None:
            print("AI Team role registry is not available.")
            return None
        try:
            role = registry.set(parts[0], parts[1])
        except (ConnectionError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            print(f"AI Team role assignment was not saved: {exc}")
            return None
        print(
            f"[OK] {role.display_name} -> {role.actual_assignment} "
            "(user-configured)"
        )
        return role

    def reset_team_role(self, role_name: str):
        if not role_name.strip():
            print("Usage: team role reset <role>")
            return None
        registry = self._team_role_registry()
        if registry is None:
            print("AI Team role registry is not available.")
            return None
        try:
            role = registry.reset(role_name)
        except (ConnectionError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            print(f"AI Team role assignment was not reset: {exc}")
            return None
        print(
            f"[OK] {role.display_name} reset to {role.requested_assignment} "
            f"({role.actual_assignment})"
        )
        return role

    def _team_role_registry(self):
        registry = getattr(self.orion, "team_roles", None)
        if registry is not None:
            return registry
        team = getattr(self.orion, "team", None)
        attributes = getattr(team, "__dict__", {})
        return attributes.get("role_registry") if isinstance(attributes, dict) else None

    def team_plan(self, payload: str):
        goal = payload.strip()
        manual_mode = False
        if goal.lower() == "--manual":
            manual_mode = True
            goal = ""
        elif goal.lower().startswith("--manual "):
            manual_mode = True
            goal = goal[len("--manual "):].strip()
        if goal[:1] in {'"', "'"}:
            if len(goal) < 2 or goal[-1] != goal[0]:
                print("Could not read team goal: closing quote is missing.")
                return
            goal = goal[1:-1].strip()
        if not goal:
            print('Usage: team plan [--manual] "<goal>"')
            return
        print("AI Team is preparing an Architect and Engineering Reviewer plan...")
        try:
            task = self.orion.team.plan(goal)
        except (OSError, TeamPlanningError, ValueError) as exc:
            print(f"AI Team planning failed: {exc}")
            task_id = getattr(exc, "task_id", "")
            if task_id:
                print(f"Saved task: {task_id}")
            return
        self._render_team_task(
            task,
            show_manual_approval=manual_mode or not self.interactive_team_approval,
        )
        if (
            task.status == "awaiting_approval"
            and self.interactive_team_approval
            and not manual_mode
        ):
            self._prompt_team_approval(task)

    def team_status(self, task_id: str):
        try:
            task = self.orion.team.task(task_id)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(str(exc))
            return
        self._render_team_task(task)

    def team_approve(self, task_id: str):
        approval = self._create_team_approval(task_id)
        if approval is None:
            return None
        self._render_team_approval(approval, show_manual_command=True)
        return approval

    def _create_team_approval(self, task_id: str):
        try:
            workspace_manager = getattr(self.orion, "workspace_manager", None)
            if workspace_manager is not None:
                capabilities = workspace_manager.refresh_capabilities()
                self.orion.codex_bridge.bind(workspace_manager.root, capabilities)
            approval = self.orion.codex_bridge.approve(task_id)
        except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
            print(f"Codex Bridge approval failed: {exc}")
            return None
        return approval

    @staticmethod
    def _render_team_approval(approval, *, show_manual_command: bool):
        print("\nCodex Plan Approval")
        print("-" * 72)
        print(f"AI Team Task: {approval.team_task_id}")
        print(f"Approval ID: {approval.approval_id}")
        print(f"Plan SHA-256: {approval.plan_hash}")
        print(f"Workspace: {approval.workspace_root}")
        print(f"Workspace Mode: {approval.workspace.mode.title()}")
        print(f"Execution Engine: Codex CLI ({approval.execution_engine})")
        if approval.workspace.is_git_repository:
            print(f"Repository Root: {approval.workspace.git_root}")
            if approval.workspace.branch:
                print(f"Branch: {approval.workspace.branch}")
        print(
            "Approval is immutable and bound to this plan, workspace capability, "
            "Codex engine, active-workspace scope, and one implementation."
        )
        if show_manual_command:
            print(
                f"Run with: team implement {approval.team_task_id} {approval.approval_id}"
            )

    def team_implement(self, payload: str):
        parts = payload.split()
        if len(parts) != 2:
            print("Usage: team implement <team-task-id> <approval-id>")
            return None
        team_task_id, approval_id = parts
        return self._implement_approved_team_plan(team_task_id, approval_id)

    def _implement_approved_team_plan(self, team_task_id: str, approval_id: str):
        engines = getattr(self.orion, "execution_engines", None) or getattr(
            self.orion.codex_bridge, "execution_engines", None
        )
        execution_engine = None
        if engines is None:
            print("No execution engine service is available.")
            return None
        try:
            role_registry = self._team_role_registry()
            if role_registry is None:
                execution_engine = engines.require_codex()
            else:
                execution_engine = role_registry.engine("implementation")
                if execution_engine is None:
                    execution_engine = engines.require_codex()
        except (ConnectionError, OSError, RuntimeError, ValueError, ExecutionEngineUnavailable) as exc:
            print(f"AI Team execution role validation failed: {exc}")
            self._render_no_execution_engine(engines)
            return None
        try:
            workspace_manager = getattr(self.orion, "workspace_manager", None)
            capabilities = (
                workspace_manager.refresh_capabilities()
                if workspace_manager is not None
                else self.orion.codex_bridge.workspace_capabilities
            )
            if workspace_manager is not None:
                self.orion.codex_bridge.bind(workspace_manager.root, capabilities)
            context = self.orion.codex_bridge.execution_context(
                team_task_id,
                approval_id,
                execution_engine,
                capabilities,
            )
        except (OSError, PermissionError, TypeError, ValueError, ExecutionEngineUnavailable) as exc:
            print(f"Codex Bridge execution failed: {exc}")
            return None
        print("Starting one approval-bound local Codex execution...")
        try:
            run = self.orion.codex_bridge.execute(context)
        except ExecutionEngineUnavailable:
            self._render_no_execution_engine(engines)
            return None
        except (
            FileNotFoundError, OSError, PermissionError, ValueError,
            WorkspaceSnapshotError, CodexBridgeError,
        ) as exc:
            if isinstance(exc, CodexBridgeError) and exc.category == "codex_cli_unavailable" and engines is not None:
                self._render_no_execution_engine(engines)
                if exc.run_id:
                    print(f"Saved run: {exc.run_id}")
                return None
            print(f"Codex Bridge execution failed: {exc}")
            run_id = getattr(exc, "run_id", "")
            if run_id:
                print(f"Saved run: {run_id}")
            return None
        self._render_codex_run(run, self.orion.codex_bridge.store.run_directory(run.run_id))
        return run

    def _prompt_team_approval(self, task):
        while True:
            print("\nApprove this exact plan?")
            print("[Y] Yes  [N] No  [D] Details")
            try:
                answer = self._read_team_approval().strip().lower()
            except KeyboardInterrupt:
                print("\nApproval cancelled. The plan remains Awaiting Approval.")
                return None
            if not answer:
                print("No approval recorded. The plan remains Awaiting Approval.")
                return None
            if answer in {"n", "no"}:
                print("Plan not approved. No implementation was performed.")
                return None
            if answer in {"d", "details"}:
                self._render_team_approval_details(task)
                continue
            if answer in {"y", "yes"}:
                approval = self._create_team_approval(task.task_id)
                if approval is None:
                    return None
                self._render_team_approval(approval, show_manual_command=False)
                return self._implement_approved_team_plan(
                    approval.team_task_id,
                    approval.approval_id,
                )
            print("Please enter Y, N, or D. No approval has been recorded.")

    def _read_team_approval(self) -> str:
        if self._team_approval_input is not None:
            return self._team_approval_input("> ")
        return input("> ")

    def _render_team_approval_details(self, task) -> None:
        bridge = self.orion.codex_bridge
        workspace_manager = getattr(self.orion, "workspace_manager", None)
        try:
            capabilities = (
                workspace_manager.refresh_capabilities()
                if workspace_manager is not None
                else bridge.workspace_capabilities
            )
        except (OSError, ValueError):
            capabilities = bridge.workspace_capabilities
        engine_label = "Codex CLI (codex)"
        engines = getattr(self.orion, "execution_engines", None) or getattr(
            bridge, "execution_engines", None
        )
        if engines is not None:
            try:
                detected = {engine.engine_id: engine for engine in engines.status()}
                engine = detected.get("codex")
                if engine is None or not engine.ready_for_implementation:
                    engine_label = "Codex CLI (not currently available)"
                elif engine.version:
                    engine_label = f"Codex CLI {engine.version} (codex)"
            except (OSError, TypeError, ValueError):
                pass

        print("\nAI Team Approval Details")
        print("-" * 72)
        print(f"Task: {task.task_id}")
        print(f"Plan SHA-256: {PlanSnapshot.from_team_task(task).hash}")
        print(f"Workspace: {capabilities.root}")
        print(f"Workspace Mode: {capabilities.mode.title()}")
        print(f"Execution Engine: {engine_label}")
        print("Sandbox Mode: workspace-write")
        print("Expected Permissions:")
        print("  - Read and write only inside the exact approved workspace")
        print("  - Network and web search disabled")
        print("  - Temporary, parent, profile, and unrelated writable roots excluded")
        print("  - .git, .codex, and .agents protected; no commit, push, merge, or PR")
        print("Final Plan:")
        for index, item in enumerate(task.final_plan, start=1):
            print(f"  {index}. {item}")
        print("Risks:")
        risks = []
        for role in ("architect", "engineer_reviewer"):
            artifact = task.artifact(role)
            if artifact is not None:
                for risk in artifact.output.risks:
                    if risk not in risks:
                        risks.append(risk)
        if risks:
            for risk in risks:
                print(f"  - {risk}")
        else:
            print("  none reported")

    def show_execution_status(self):
        engines = self.orion.execution_engines.status()
        detected = {engine.engine_id: engine for engine in engines}
        codex_ready = detected.get("codex") is not None and detected["codex"].ready_for_implementation
        print("Execution Engines")
        print("=" * 50)
        for engine in engines:
            print(f"\n{engine.name}")
            print("Status:")
            print(self._execution_status_label(engine))
            if engine.engine_id in {"codex_desktop", "chatgpt_desktop"}:
                print("CLI Support:")
                if engine.engine_id == "codex_desktop" and codex_ready:
                    print("Separate CLI detected")
                else:
                    print("No")
            if engine.executable:
                print("Executable:")
                print(engine.executable)
            if engine.cli_support and engine.engine_id != "python":
                print("PATH Visibility:")
                print("Yes" if engine.path_visible else "No")
            if engine.discovery_source:
                print("Discovery Source:")
                print(engine.discovery_source)
            if engine.version:
                print("Version:")
                print(engine.version)
            if engine.version_probe:
                print("Version Probe:")
                print(engine.version_probe)
            if engine.reason and engine.status in {
                "installed_not_executable", "detection_error"
            }:
                print("Diagnostic:")
                print(engine.reason.replace("_", " ").title())

    @staticmethod
    def _execution_status_label(engine):
        labels = {
            "ready": "Ready",
            "installed": "Installed",
            "installed_not_executable": "Installed but not executable",
            "not_installed": "Not Installed",
            "detection_error": "Detection Error",
            "unsupported_as_cli": "Unsupported as CLI",
        }
        return labels.get(engine.status, engine.status.replace("_", " ").title())

    @staticmethod
    def _render_no_execution_engine(engines):
        detected = {
            engine.engine_id: engine
            for engine in engines.status()
        }
        print("No execution engine is currently available.")
        print("\nDetected:\n")
        for engine_id in (
            "codex_desktop", "chatgpt_desktop", "codex", "claude_code", "gemini_cli"
        ):
            engine = detected.get(engine_id)
            if engine is None:
                continue
            marker = "✓" if engine.ready_for_implementation or (
                not engine.cli_support and engine.installed
            ) else "!" if engine.installed else "✗"
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

    def team_test(self, run_id: str):
        if not run_id:
            print("Usage: team test <run-id|last>")
            return None
        try:
            workspace_manager = getattr(self.orion, "workspace_manager", None)
            if workspace_manager is not None:
                capabilities = workspace_manager.refresh_capabilities()
                self.orion.codex_bridge.bind(workspace_manager.root, capabilities)
            selected = (
                self.orion.codex_bridge.latest_validatable_run()
                if run_id.strip().lower() == "last"
                else self.orion.codex_bridge.run(run_id.strip())
            )
            run = self.orion.codex_bridge.validate(selected.run_id)
        except (FileNotFoundError, OSError, PermissionError, RuntimeError, ValueError) as exc:
            print(f"Automatic validation refused: {exc}")
            return None
        self._render_codex_run(run, self.orion.codex_bridge.store.run_directory(run.run_id))
        return run

    def team_rollback(self, run_id: str):
        if not run_id:
            print("Usage: team rollback <run-id>")
            return
        try:
            run = self.orion.codex_bridge.run(run_id)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"Codex Bridge Error: {exc}")
            return
        print(f"Rollback workspace changes from {run.run_id}?")
        print("This removes files created by the run and restores its saved preimages.")
        if input("Approve rollback? [y/N]: ").strip().lower() not in {"y", "yes"}:
            print("Team rollback cancelled.")
            return
        try:
            rolled_back = self.orion.codex_bridge.rollback(run.run_id)
        except (FileNotFoundError, OSError, PermissionError, ValueError, WorkspaceRollbackError) as exc:
            print(f"Team rollback refused: {exc}")
            return
        print(f"[OK] Run {rolled_back.run_id} was rolled back without Git reset or checkout.")

    @staticmethod
    def _render_team_task(task, *, show_manual_approval: bool = True):
        print("\nAI Team Plan")
        print("-" * 62)
        print(f"Task: {task.task_id}")
        print(f"Goal: {task.goal}")
        role_assignments = getattr(task, "role_assignments", None)
        if isinstance(role_assignments, (list, tuple)) and role_assignments:
            print("\nWorkflow Role Assignments")
            for assignment in role_assignments:
                availability = "Ready" if assignment.available else "Unavailable"
                print(
                    f"  {assignment.display_name:<23} {assignment.actual_assignment} "
                    f"[{availability}; {assignment.source}]"
                )
                if assignment.fallback_reason:
                    print(f"    Fallback: {assignment.fallback_reason}")

        labels = {
            "architect": "Architect",
            "engineer_reviewer": "Engineering Reviewer",
        }
        for role in ("architect", "engineer_reviewer"):
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
            metadata = getattr(artifact, "role_metadata", None)
            if metadata is not None:
                print(
                    f"  Assignment: {metadata.requested_assignment} -> "
                    f"{metadata.actual_assignment}"
                )
                if metadata.fallback_reason:
                    print(f"  Fallback: {metadata.fallback_reason}")
                print(f"  Duration: {metadata.duration_seconds:.3f}s")

        if task.final_plan:
            print("\nFinal Plan")
            for index, item in enumerate(task.final_plan, start=1):
                print(f"  {index}. {item}")

        if task.usage:
            print("\nUsage (estimated tokens)")
            usage_labels = {
                "architect": "Architect",
                "engineer": "Engineering Reviewer",
                "engineer_reviewer": "Engineering Reviewer",
                "implementation": "Implementation Engine",
                "tester": "Tester",
                "documentation": "Documentation Reviewer",
            }
            for usage in task.usage:
                cost = (
                    "not configured"
                    if usage.estimated_cost_usd is None
                    else f"${usage.estimated_cost_usd:.6f}"
                )
                print(
                    f"  {usage_labels.get(usage.role, usage.role.title()):<23} "
                    f"{usage.provider}:{usage.model} | "
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
            if show_manual_approval:
                print(f"Approve this exact plan with: team approve {task.task_id}")

    @staticmethod
    def _render_codex_run(run, artifact_directory):
        print("\nImplementation Result")
        print("-" * 72)
        print(f"Run: {run.run_id}")
        print(f"AI Team Task: {run.team_task_id}")
        print(f"Approval: {run.approval_id}")
        print(f"Plan SHA-256: {run.plan_hash}")
        print(f"Workspace: {run.workspace_root}")
        print(f"Workspace Mode: {run.workspace.mode.title()}")
        if run.workspace.is_git_repository:
            print(f"Repository Root: {run.workspace.git_root}")
            if run.workspace.branch:
                print(f"Branch: {run.workspace.branch}")
            if run.workspace.commit:
                print(f"Commit: {run.workspace.commit[:12]}")
        print(f"Status: {run.status.replace('_', ' ').title()}")
        if run.result is not None:
            print(f"Implementation: Complete\n\nSummary\n  {run.result.summary}")
            print("\nFiles Changed")
            if run.changes is not None:
                print(f"  Created:  {len(run.changes.by_kind('created'))}")
                print(f"  Modified: {len(run.changes.by_kind('modified'))}")
                print(f"  Deleted:  {len(run.changes.by_kind('deleted'))}")
            elif run.result.files_changed:
                print(f"  Reported: {len(run.result.files_changed)}")
            else:
                print("  none")
            print("\nImplementation-Reported Tests")
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
        if run.changes is not None:
            labels = (("created", "Created"), ("modified", "Modified"), ("deleted", "Deleted"))
            print("\nWorkspace Review")
            for kind, label in labels:
                items = run.changes.by_kind(kind)
                print(f"  {label}:")
                if not items:
                    print("    none")
                for item in items:
                    suffix = " (binary metadata only)" if item.binary else ""
                    print(f"    - {item.path}{suffix}")
            if run.changes.diff_truncated:
                print("  Text diff was truncated at the configured safety limit.")

        validation = getattr(run, "validation", None)
        print("\nAutomatic Validation")
        print("-" * 72)
        if validation is None:
            print("NOT RUN  No automatic validation attempt is recorded.")
        else:
            print(
                f"Tester: {validation.tester_requested} -> "
                f"{validation.tester_resolved or 'unavailable'}"
            )
            if validation.fallback_reason:
                print(f"Fallback: {validation.fallback_reason}")
            marker = {
                "passed": "PASS",
                "warning": "WARN",
                "failed": "FAIL",
                "skipped": "SKIP",
                "error": "ERROR",
            }
            for check in validation.checks:
                print(f"{marker.get(check.status, check.status.upper()):5} {check.name}: {check.summary}")
            for diagnostic in validation.safe_diagnostics:
                print(f"INFO  {diagnostic}")
            print("\nValidation Summary")
            print(f"  Checks:   {len(validation.checks)}")
            print(f"  Passed:   {len(validation.checks_passed)}")
            print(f"  Warnings: {len(validation.warnings)}")
            print(f"  Failed:   {len(validation.checks_failed)}")
            print(f"  Skipped:  {len(validation.skipped_checks)}")
            print(f"  Attempts: {len(getattr(run, 'validation_history', ())) or 1}")
        if run.error:
            print(f"Error category: {run.error}")
        print(f"\nArtifacts: {artifact_directory}")
        if run.status == "awaiting_review":
            print("\nReview Status")
            print("-" * 72)
            print(validation.review_status if validation is not None else "Awaiting Review — Validation Not Run")
            print("Validation never accepts or rolls back implementation changes.")
            print("No Git or pull-request action was performed.")
            print(f"Review the bounded diff at: {artifact_directory / 'workspace.diff'}")
            print(f"Rerun validation with: team test {run.run_id}")
            print(f"Rollback with: team rollback {run.run_id}")
        elif run.status == "rolled_back":
            print("Workspace changes from this run have been safely rolled back.")
            if validation is not None:
                print("Validation artifacts were retained as audit history for this rolled-back run.")

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
        self.orion.workspace_manager.refresh_capabilities()
        details = self.orion.workspace_manager.describe()
        print(f"Active Workspace: {details['root']}")
        print(f"Top-level directories: {details['directories']}")
        print(f"Top-level files: {details['files']}")
        capabilities = details["capabilities"]
        print(f"Workspace Mode: {capabilities.mode.title()}")
        if capabilities.is_git_repository:
            print(f"Repository Root: {capabilities.git_root}")
            if capabilities.branch:
                print(f"Branch: {capabilities.branch}")
            if not capabilities.supports_git_commands:
                print("Git metadata was detected, but Git commands are unavailable on this host.")
        else:
            print("Git Repository: No")
            print("Team execution is available.")
            print("Git-specific commands such as status, history, diff, pull, and push are unavailable.")

    def set_workspace(self, path: str):
        """Select a new active workspace."""
        if not path:
            print("Usage: workspace <path>")
            return

        try:
            selected = self.orion.workspace_manager.set_workspace(path)
        except FileNotFoundError:
            requested = Path(path).expanduser().resolve()
            print("The directory does not exist:")
            print(requested)
            if input("Would you like Orion to create it? [Y/N]: ").strip().lower() not in {"y", "yes"}:
                print("Workspace creation cancelled. The active workspace was not changed.")
                return
            try:
                selected = self.orion.workspace_manager.create_workspace(requested)
            except (FileExistsError, NotADirectoryError, OSError, PermissionError) as exc:
                print(f"Workspace Error: {exc}")
                return
            print("Created the workspace. Git was not initialized.")
        except (NotADirectoryError, PermissionError) as exc:
            print(f"Workspace Error: {exc}")
            return

        self.orion.project_context.bind(selected)
        self.orion.task_manager.bind(selected)
        self.orion.codex_bridge.bind(selected, self.orion.workspace_manager.capabilities)
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
        if not self._git_command_available("Git status"):
            return
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
        if not self._git_command_available("Git history"):
            return
        try:
            print(self.orion.git_service.log() or "No commits found.")
        except Exception as exc:
            print(f"Could not read Git history: {exc}")

    def git_diff(self, staged=False):
        if not self._git_command_available("Git diff"):
            return
        try:
            print(self.orion.git_service.diff(staged=staged) or "No differences.")
        except Exception as exc:
            print(f"Could not read Git differences: {exc}")

    def _confirm_sensitive_git(self, description):
        print(f"Sensitive action: {description}")
        return input("Approve? [y/N]: ").strip().lower() in {"y", "yes"}

    def git_pull(self):
        if not self._git_command_available("Git pull"):
            return
        if not self._confirm_sensitive_git("fast-forward pull from origin"):
            print("Git pull cancelled.")
            return
        try:
            print(self.orion.git_service.pull() or "Already up to date.")
        except Exception as exc:
            print(f"Git pull failed: {exc}")

    def git_push(self):
        if not self._git_command_available("Git push"):
            return
        if not self._confirm_sensitive_git("push the current branch to origin"):
            print("Git push cancelled.")
            return
        try:
            print(self.orion.git_service.push() or "Push completed.")
        except Exception as exc:
            print(f"Git push failed: {exc}")

    def _git_command_available(self, command_name: str) -> bool:
        capabilities = self.orion.workspace_manager.refresh_capabilities()
        if not capabilities.is_git_repository:
            print(
                f"{command_name} requires Git Workspace Mode. "
                "Team planning and execution remain available in this Standard workspace."
            )
            return False
        if not capabilities.supports_git_commands:
            print(f"{command_name} is unavailable because Git is not installed or could not inspect this repository.")
            return False
        return True

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
