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

        elif command_lower == "profile":
            self.show_profile()

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
            self.ask_ai(prompt)

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
        """Display available commands."""
        print("""
Available commands:
  help     Show this help menu
  status   Show Orion system status
  config   Show loaded configuration
  profile  Show loaded user profile
  services Show registered Orion services
  plugins  Show discovered Orion plugins
  plugins info <name> Show plugin details
  workspace              Show the active workspace
  workspace <path>       Change the active workspace
  files [path]           List workspace files (alias: ls)
  remember <key> <value> Store a value for this Orion session
  recall <key>           Recall a session-memory value
  memory                 Show all session-memory values
  forget <key>           Remove one session-memory value
  clear memory           Clear session memory after confirmation
  project init           Initialize persistent project memory
  project status         Show persistent project status
  project info           Show detailed project metadata
  project set <field> <value>  Update goal, phase, version, model, etc.
  project note <text>    Append a timestamped project note
  project checkpoint <summary> Save a portable handoff checkpoint
  project resume         Show where this project left off
  project rules          List mandatory project rules
  project rule add <rule> Add a workspace-specific rule
  project rule remove <id> Remove a project rule
  index build            Build the workspace knowledge index
  index status           Show index statistics
  index find <text>      Find indexed symbols, files, and TODOs
  index classes          List indexed classes
  index functions        List indexed functions
  index todos            List TODO/FIXME/HACK items
  index imports          List Python imports
  history                Show Orion and project history
  conversation           Show recent conversation context
  conversation recent [n] Show the most recent messages
  conversation search <text> Search saved conversation messages
  conversation clear     Clear today's conversation after confirmation
  about                  Show Orion identity and capabilities
  ask                    Ask Orion's configured AI provider
  exit     Shut down Orion
""")
        plugin_lines = self.orion.plugin_manager.help_lines()
        if plugin_lines:
            print("Plugin commands:")
            for line in plugin_lines:
                print(line)

    def show_status(self):
        """Display Orion status."""
        print(f"System Status: {self.orion.status}")
        print("Core: Online")
        print("Command Router: Online")
        print(f"AI Provider: {self.orion.ai_provider.name()}")
        print(f"Brain: {self.orion.brain.name()}")
        print(f"User Profile: {self.orion.profile_manager.name}")
        print(f"Workspace: {self.orion.workspace_manager.root}")
        code_state = "Online (plugin)" if self.orion.services.find("code") else "Offline"
        search_state = "Online (plugin)" if self.orion.services.find("search") else "Offline"
        print(f"Code Skill: {code_state}")
        print(f"Search Skill: {search_state}")
        print(f"Session Memory: Online ({len(self.orion.session_memory)} items)")
        print(f"Conversation Context: Online ({self.orion.conversation.count()} messages)")
        index_state = "Built" if self.orion.knowledge_index.exists() else "Not built"
        print(f"Knowledge Index: Online ({index_state})")
        print(f"Service Registry: Online ({len(self.orion.services)} registered)")
        state = "Initialized" if self.orion.project_context.initialized else "Not initialized"
        print(f"Project Context: Online ({state})")
        print(f"Plugin Manager: Online ({self.orion.plugin_manager.loaded_count()} loaded, {self.orion.plugin_manager.failed_count()} failed)")

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
        self.orion.conversation.bind(selected)
        self.orion.knowledge_index.bind(selected)
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
        print("Project Context:")
        print(f"  Name: {data.get('name', '')}")
        print(f"  Version: {data.get('version', '')}")
        print(f"  Phase: {data.get('phase', '')}")
        print(f"  Current Goal: {data.get('current_goal', '')}")
        print(f"  Workspace: {self.orion.project_context.workspace_root}")
        print(f"  Tasks: {metrics.get('tasks_open', 0)} open, {metrics.get('tasks_completed', 0)} completed")
        print(f"  History Entries: {metrics.get('history_entries', 0)}")
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
