"""Orion Code Skill.

Provides safe, read-only source inspection inside the active workspace.
Write and execution capabilities will be added later behind explicit approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orion.services.workspace import WorkspaceManager


@dataclass(frozen=True)
class CodeFileInfo:
    """Metadata about a source file inside the workspace."""

    relative_path: str
    language: str
    size_bytes: int
    line_count: int


class CodeSkill:
    """Read and inspect code without escaping the active workspace."""

    LANGUAGE_BY_SUFFIX = {
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".tsx": "TypeScript React",
        ".jsx": "JavaScript React",
        ".json": "JSON",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".toml": "TOML",
        ".md": "Markdown",
        ".html": "HTML",
        ".css": "CSS",
        ".scss": "SCSS",
        ".sh": "Shell",
        ".ps1": "PowerShell",
        ".sql": "SQL",
        ".java": "Java",
        ".cs": "C#",
        ".cpp": "C++",
        ".c": "C",
        ".h": "C/C++ Header",
        ".rs": "Rust",
        ".go": "Go",
    }

    def __init__(self, workspace_manager: WorkspaceManager, max_read_bytes: int = 200_000):
        self.workspace_manager = workspace_manager
        self.max_read_bytes = max_read_bytes

    def read_file(self, relative_path: str | Path) -> str:
        """Read a UTF-8 text file inside the workspace."""
        path = self._require_file(relative_path)
        size = path.stat().st_size
        if size > self.max_read_bytes:
            raise ValueError(
                f"File is too large to read safely ({size} bytes; limit {self.max_read_bytes})."
            )

        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"File is not readable UTF-8 text: {relative_path}") from exc

    def inspect_file(self, relative_path: str | Path) -> CodeFileInfo:
        """Return basic metadata for a source or text file."""
        path = self._require_file(relative_path)
        content = self.read_file(relative_path)
        relative = path.relative_to(self.workspace_manager.root)
        return CodeFileInfo(
            relative_path=str(relative),
            language=self.detect_language(path),
            size_bytes=path.stat().st_size,
            line_count=len(content.splitlines()),
        )

    def tree(self, relative_path: str | Path = ".", max_depth: int = 3) -> list[str]:
        """Return a compact directory tree for a workspace path."""
        if max_depth < 0 or max_depth > 8:
            raise ValueError("Tree depth must be between 0 and 8.")

        root = self.workspace_manager.resolve(relative_path)
        if not root.exists():
            raise FileNotFoundError(f"Workspace path not found: {relative_path}")
        if not root.is_dir():
            raise NotADirectoryError(f"Not a directory: {relative_path}")

        lines: list[str] = []
        base_depth = len(root.parts)
        for item in sorted(root.rglob("*"), key=lambda value: str(value).lower()):
            depth = len(item.parts) - base_depth
            if depth > max_depth:
                continue
            if any(part in {".git", "__pycache__", ".venv", "venv", "node_modules"} for part in item.relative_to(root).parts):
                continue
            indent = "  " * (depth - 1)
            marker = "[D]" if item.is_dir() else "[F]"
            lines.append(f"{indent}{marker} {item.relative_to(root)}")
        return lines

    def detect_language(self, path: str | Path) -> str:
        """Guess a language from the file extension."""
        suffix = Path(path).suffix.lower()
        return self.LANGUAGE_BY_SUFFIX.get(suffix, "Text/Unknown")

    def _require_file(self, relative_path: str | Path) -> Path:
        path = self.workspace_manager.resolve(relative_path)
        if not path.exists():
            raise FileNotFoundError(f"Workspace file not found: {relative_path}")
        if not path.is_file():
            raise IsADirectoryError(f"Not a file: {relative_path}")
        return path
