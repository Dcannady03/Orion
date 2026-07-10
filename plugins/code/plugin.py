"""Built-in Code Skill plugin."""
from __future__ import annotations

from orion.plugins.base import OrionPlugin, PluginContext
from orion.skills.code import CodeSkill


class CodePlugin(OrionPlugin):
    name = "code"
    version = "1.0.0"
    description = "Safe, read-only source inspection inside the active workspace."

    def __init__(self):
        self.context: PluginContext | None = None
        self.skill: CodeSkill | None = None

    def activate(self, context: PluginContext) -> None:
        self.context = context
        workspace = context.services.get("workspace")
        self.skill = CodeSkill(workspace)
        context.services.register("code", self.skill)
        context.orion.code_skill = self.skill

    def handle(self, command: str) -> bool:
        raw = command.strip()
        lower = raw.lower()
        if lower == "code":
            print("Usage: code <read|info|tree> [path]")
            return True
        if lower.startswith("code read "):
            self._read(raw[len("code read "):].strip())
            return True
        if lower.startswith("code info "):
            self._info(raw[len("code info "):].strip())
            return True
        if lower == "code tree":
            self._tree(".")
            return True
        if lower.startswith("code tree "):
            self._tree(raw[len("code tree "):].strip())
            return True
        return False

    def help_lines(self) -> list[str]:
        return [
            "  code read <file>       Read a text/source file safely [plugin]",
            "  code info <file>       Show language, size, and line count [plugin]",
            "  code tree [path]       Show a compact project tree [plugin]",
        ]

    def _read(self, path: str) -> None:
        try:
            content = self.skill.read_file(path)  # type: ignore[union-attr]
        except (FileNotFoundError, IsADirectoryError, PermissionError, ValueError) as exc:
            print(f"Code Skill Error: {exc}")
            return
        print(f"--- {path} ---")
        print(content)

    def _info(self, path: str) -> None:
        try:
            info = self.skill.inspect_file(path)  # type: ignore[union-attr]
        except (FileNotFoundError, IsADirectoryError, PermissionError, ValueError) as exc:
            print(f"Code Skill Error: {exc}")
            return
        print(f"File: {info.relative_path}")
        print(f"Language: {info.language}")
        print(f"Size: {info.size_bytes} bytes")
        print(f"Lines: {info.line_count}")

    def _tree(self, path: str) -> None:
        try:
            lines = self.skill.tree(path or ".")  # type: ignore[union-attr]
        except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError) as exc:
            print(f"Code Skill Error: {exc}")
            return
        print("\n".join(lines) if lines else "No files found.")


def create_plugin() -> OrionPlugin:
    return CodePlugin()
