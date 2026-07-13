"""Built-in workspace Search plugin."""
from __future__ import annotations

import shlex

from orion.plugins.base import OrionPlugin, PluginContext
from orion.skills.search import SearchReport, SearchSkill


class SearchPlugin(OrionPlugin):
    name = "search"
    version = "1.0.0"
    description = "Safe file-name and text search inside the active workspace."

    def __init__(self):
        self.context: PluginContext | None = None
        self.skill: SearchSkill | None = None

    def activate(self, context: PluginContext) -> None:
        self.context = context
        workspace = context.services.get("workspace")
        self.skill = SearchSkill(workspace)
        context.services.register("search", self.skill)
        context.orion.search_skill = self.skill

    def handle(self, command: str) -> bool:
        raw = command.strip()
        lower = raw.lower()
        if lower in {"search", "find"}:
            self._usage()
            return True
        if lower.startswith("search "):
            self._run(raw[len("search "):])
            return True
        if lower.startswith("find "):
            self._run(raw[len("find "):])
            return True
        return False

    def help_lines(self) -> list[str]:
        return [
            "  search <text>          Search workspace file contents [plugin]",
            "  search --files <text>  Search file names and paths [plugin]",
            "  search --regex <expr>  Search contents with a regular expression [plugin]",
            "  search --type <ext> <text> Limit search by file type [plugin]",
            "  find <text>            Alias for search [plugin]",
        ]

    def _run(self, payload: str) -> None:
        try:
            args = shlex.split(payload)
        except ValueError as exc:
            print(f"Search Error: {exc}")
            return

        files_only = False
        regex = False
        case_sensitive = False
        file_type: str | None = None
        relative_path = "."
        query_parts: list[str] = []
        index = 0

        while index < len(args):
            arg = args[index]
            if arg in {"--files", "-f"}:
                files_only = True
            elif arg in {"--regex", "-r"}:
                regex = True
            elif arg in {"--case-sensitive", "-c"}:
                case_sensitive = True
            elif arg in {"--type", "-t"}:
                index += 1
                if index >= len(args):
                    print("Search Error: --type requires a file extension.")
                    return
                file_type = args[index]
            elif arg in {"--path", "-p"}:
                index += 1
                if index >= len(args):
                    print("Search Error: --path requires a workspace path.")
                    return
                relative_path = args[index]
            elif arg.startswith("-"):
                print(f"Search Error: unknown option {arg}")
                return
            else:
                query_parts.append(arg)
            index += 1

        query = " ".join(query_parts).strip()
        if not query:
            self._usage()
            return

        try:
            if files_only:
                matches = self.skill.search_files(  # type: ignore[union-attr]
                    query,
                    relative_path=relative_path,
                    regex=regex,
                    case_sensitive=case_sensitive,
                    file_type=file_type,
                )
                self._print_files(matches)
                return

            report = self.skill.search_text(  # type: ignore[union-attr]
                query,
                relative_path=relative_path,
                regex=regex,
                case_sensitive=case_sensitive,
                file_type=file_type,
            )
            self._print_report(report)
        except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
            print(f"Search Error: {exc}")

    @staticmethod
    def _print_files(matches: tuple[str, ...]) -> None:
        if not matches:
            print("No matching files found.")
            return
        print(f"Found {len(matches)} matching file(s):")
        for path in matches:
            print(f"  {path}")

    @staticmethod
    def _print_report(report: SearchReport) -> None:
        if not report.matches:
            print(
                f"No matches found. Scanned {report.files_scanned} file(s); "
                f"skipped {report.files_skipped}."
            )
            return
        print(f"Found {len(report.matches)} match(es):")
        current_path = None
        for match in report.matches:
            if match.relative_path != current_path:
                current_path = match.relative_path
                print(f"\n{current_path}")
            print(f"  {match.line_number}: {match.line}")
        print(
            f"\nScanned {report.files_scanned} file(s); "
            f"skipped {report.files_skipped}."
        )
        if report.truncated:
            print("Results truncated at the configured safety limit.")

    @staticmethod
    def _usage() -> None:
        print(
            "Usage: search [--files] [--regex] [--case-sensitive] "
            "[--type <ext>] [--path <path>] <query>"
        )


def create_plugin() -> OrionPlugin:
    return SearchPlugin()
