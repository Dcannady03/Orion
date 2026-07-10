"""Orion Workspace Manager.

Provides a safe, centralized view of the project workspace. Other Phase 2
components should use this service instead of accessing arbitrary paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceEntry:
    """A file or directory visible inside the active workspace."""

    name: str
    relative_path: str
    is_directory: bool
    size_bytes: int | None


class WorkspaceManager:
    """Manage Orion's active workspace and enforce path boundaries."""

    def __init__(self, root_path: str | Path = "."):
        self._root = self._validate_workspace(root_path)

    @property
    def root(self) -> Path:
        """Return the absolute active workspace path."""
        return self._root

    def set_workspace(self, path: str | Path) -> Path:
        """Change the active workspace to an existing directory."""
        self._root = self._validate_workspace(path)
        return self._root

    def resolve(self, relative_path: str | Path = ".") -> Path:
        """Resolve a path while preventing escape from the workspace root."""
        candidate = (self._root / relative_path).resolve()

        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise PermissionError(
                f"Path is outside the active workspace: {relative_path}"
            ) from exc

        return candidate

    def list_entries(self, relative_path: str | Path = ".") -> list[WorkspaceEntry]:
        """List files and directories at a location inside the workspace."""
        directory = self.resolve(relative_path)

        if not directory.exists():
            raise FileNotFoundError(f"Workspace path not found: {relative_path}")
        if not directory.is_dir():
            raise NotADirectoryError(f"Not a directory: {relative_path}")

        entries: list[WorkspaceEntry] = []
        for item in sorted(directory.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower())):
            relative = item.relative_to(self._root)
            entries.append(
                WorkspaceEntry(
                    name=item.name,
                    relative_path=str(relative),
                    is_directory=item.is_dir(),
                    size_bytes=None if item.is_dir() else item.stat().st_size,
                )
            )

        return entries

    def describe(self) -> dict[str, object]:
        """Return a small status summary for the active workspace."""
        top_level = self.list_entries()
        return {
            "root": str(self._root),
            "directories": sum(entry.is_directory for entry in top_level),
            "files": sum(not entry.is_directory for entry in top_level),
        }

    @staticmethod
    def _validate_workspace(path: str | Path) -> Path:
        workspace = Path(path).expanduser().resolve()

        if not workspace.exists():
            raise FileNotFoundError(f"Workspace does not exist: {workspace}")
        if not workspace.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {workspace}")

        return workspace
