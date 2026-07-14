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
    "help", "status", "briefing", "settings", "about", "profile", "config", "services",
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
