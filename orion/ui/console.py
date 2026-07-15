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
    Completion = None
    FileHistory = None


BASE_COMMANDS = (
    "help", "home", "status", "briefing", "connect", "connect status", "connect health", "connect add gmail", "connect add discord", "email", "email inbox", "email unread", "email search", "email read", "email compose", "discord send", "ai", "ai status", "ai models", "ai profiles", "ai profile coding", "ai profile creative", "ai profile lightweight", "ai profile vision", "ai benchmark", "change ollama model", "ollama model", "weather", "weather tomorrow", "calendar", "calendar today", "calendar tomorrow", "calendar next", "calendar providers", "calendar enable google", "calendar enable microsoft", "calendar disable google", "calendar disable microsoft", "calendar configure google", "calendar configure microsoft", "calendar connect google", "calendar connect microsoft", "settings", "about", "profile", "config", "services",
    "plugins", "workspace", "files", "ls", "remember", "recall", "memory",
    "forget", "clear memory", "project init", "project status", "project info",
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
        if Completion is None:
            return
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
    def render_home(orion, briefing) -> None:
        """Render Orion Home as a compact, provider-neutral command center."""
        from datetime import datetime

        now = datetime.now()
        hour = now.hour
        greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        location = orion.profile_manager.get("location", "") or "Location not set"
        print("=" * 62)
        print(f"{'ORION':^62}")
        print(f"{'Personal AI Operating System':^62}")
        print("=" * 62)
        print(f"{greeting}, {orion.user_name}.")
        print(f"{now.strftime('%A, %B %d, %Y  |  %I:%M %p')}")
        print(f"{location}")
        print("-" * 62)
        if not briefing.items:
            print("[i] No Home cards are available yet.")
        for item in briefing.items:
            print(f"{item.icon:<6} {item.title:<14} {item.message}")
        if orion.companion_settings.developer_mode and briefing.errors:
            print("-" * 62)
            print("Provider diagnostics")
            for error in briefing.errors:
                print(f"[X] {error.provider}: {error.message}")
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
