"""Cross-platform interactive console for Orion Companion."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

try:
    from colorama import Fore, Style, init as colorama_init
except ImportError:  # pragma: no cover - graceful fallback
    class _Blank:
        def __getattr__(self, _name: str) -> str:
            return ""
    Fore = Style = _Blank()
    def colorama_init(*_args, **_kwargs):
        return None

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory
except ImportError:  # pragma: no cover - graceful fallback
    PromptSession = None
    Completer = object
    FileHistory = None

    class Completion:
        """Small prompt-toolkit-compatible result used without the optional UI dependency."""

        def __init__(self, text: str, start_position: int = 0) -> None:
            self.text = text
            self.start_position = start_position


BASE_COMMANDS = (
    "help", "home", "status", "briefing", "connect", "connect status", "connect health", "connect add gmail", "connect add microsoft", "connect add discord", "email", "email status", "email providers", "email connect gmail", "email connect microsoft", "email disconnect gmail", "email disconnect microsoft", "email configure gmail", "email configure microsoft", "email accounts", "email inbox", "email inbox gmail", "email inbox microsoft", "email unread", "email unread gmail", "email unread microsoft", "email search", "email read", "email thread", "email summarize", "email use gmail", "email use microsoft", "email draft", "email send", "email reply", "email forward", "email archive", "email trash", "email mark read", "email mark unread", "discord send", "ai", "ai status", "ai connect openai", "ai test openai", "ai disconnect openai", "ai providers", "ai provider configure openai", "ai provider configure gemini", "ai provider use ollama", "ai provider use openai", "ai provider use gemini", "ai models", "ai profiles", "ai profile fast", "ai profile balanced", "ai profile coding", "ai profile research", "ai profile creative", "ai profile lightweight", "ai profile vision", "ai benchmark", "ai stats", "ai stats clear", "ai health", "ai route status", "ai route explain last", "change ollama model", "ollama model", "weather", "weather tomorrow", "network", "network status", "network watch", "network report", "network stop", "network config", "calendar", "calendar today", "calendar tomorrow", "calendar next", "calendar providers", "calendar enable google", "calendar enable microsoft", "calendar disable google", "calendar disable microsoft", "calendar configure google", "calendar configure microsoft", "calendar connect google", "calendar connect microsoft", "settings", "about", "profile", "config", "services",
    "plugins", "workspace", "files", "ls", "remember", "recall", "memory",
    "forget", "clear memory", "agent", "agent list", "agent show", "agent create",
    "agent enable", "agent disable", "agent test", "team", "team roles", "team role show", "team role set", "team role reset", "team plan", "team plan --manual", "team status",
    "team approve", "team implement", "team run", "team rollback",
    "execution", "execution status",
    "task", "task list", "task create", "task show", "task approve", "task cancel",
    "task events", "task link-plan", "project init", "project status", "project info",
    "project resume", "project rules", "index build", "index status", "index find",
    "index classes", "index functions", "index todos", "index imports",
    "action pending", "action history", "apps scan", "apps list", "apps find",
    "app alias", "open", "launch", "developer on", "developer off", "trust list",
    "trust revoke", "history", "conversation", "conversation recent",
    "conversation search", "ask", "exit", "quit",
)


class OrionCompleter(Completer):
    """Complete Orion commands and discovered application names."""

    def __init__(self, orion) -> None:
        self.orion = orion

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        lowered = text.lower()
        candidates: Iterable[str]
        if lowered.startswith(("open ", "launch ", "apps find ", "trust revoke ")):
            prefix, _, fragment = text.rpartition(" ")
            apps = sorted({app.name for app in self.orion.application_catalog.applications()})
            candidates = (f"{prefix} {name}" for name in apps if name.lower().startswith(fragment.lower()))
        else:
            candidates = (item for item in BASE_COMMANDS if item.startswith(lowered))
        for candidate in candidates:
            yield Completion(candidate, start_position=-len(text))


class Console:
    """Friendly console with history, completion, and semantic output helpers."""

    def __init__(self, orion) -> None:
        self.orion = orion
        colorama_init(autoreset=True)
        self.session = None
        if PromptSession is not None:
            history_path = Path.home() / ".orion" / "command-history.txt"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            self.session = PromptSession(
                history=FileHistory(str(history_path)),
                completer=OrionCompleter(orion),
                complete_while_typing=False,
            )

    def prompt(self, label: str) -> str:
        if self.session is not None:
            return self.session.prompt(f"{label}> ")
        return input(f"{label}> ")



    @staticmethod
    def render_home(snapshot, *, developer_mode: bool = False) -> None:
        """Render a reusable Home Center snapshot."""
        print("=" * 62)
        print(f"{'ORION':^62}")
        print(f"{'Personal AI Operating System':^62}")
        print("=" * 62)
        print(f"{snapshot.greeting}, {snapshot.user_name}.")
        print(snapshot.generated_at.strftime("%A, %B %d, %Y  |  %I:%M %p"))
        print(snapshot.location)
        print("-" * 62)
        if not snapshot.cards:
            print("[i] No Home cards are available yet.")
        for card in snapshot.cards:
            print(f"{card.icon:<6} {card.title:<14} {card.message}")
        if developer_mode and snapshot.provider_errors:
            print("-" * 62)
            print("Provider diagnostics")
            for provider, message in snapshot.provider_errors:
                print(f"[X] {provider}: {message}")
        print("-" * 62)
        print("Try: ask <question> | connect | ai status | calendar today | weather")
        print("I'm online and ready to help.")
        print("=" * 62)

    @staticmethod
    def render_briefing(briefing, *, developer_mode: bool = False) -> None:
        """Render a provider-neutral Morning Star briefing."""
        print("Today's Briefing")
        print("-" * 50)
        if not briefing.items:
            print("  No briefing items are available yet.")
        for item in briefing.items:
            print(f"  {item.icon} {item.title}: {item.message}")
        if developer_mode and briefing.errors:
            print("  Provider diagnostics:")
            for error in briefing.errors:
                print(f"    [X] {error.provider}: {error.message}")

    @staticmethod
    def success(message: str) -> None:
        print(f"{Fore.GREEN}[OK]{Style.RESET_ALL} {message}")

    @staticmethod
    def info(message: str) -> None:
        print(f"{Fore.CYAN}[i]{Style.RESET_ALL} {message}")

    @staticmethod
    def warning(message: str) -> None:
        print(f"{Fore.YELLOW}[!]{Style.RESET_ALL} {message}")

    @staticmethod
    def error(message: str) -> None:
        print(f"{Fore.RED}[X]{Style.RESET_ALL} {message}")
