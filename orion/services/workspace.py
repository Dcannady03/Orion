"""Central workspace boundaries and capability detection for Orion."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


WORKSPACE_MODE_STANDARD = "standard"
WORKSPACE_MODE_GIT = "git"
WORKSPACE_MODES = frozenset({WORKSPACE_MODE_STANDARD, WORKSPACE_MODE_GIT})

DEFAULT_WORKSPACE_IGNORED_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", ".orion", ".agents", ".codex", ".idea", ".vscode",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    ".venv", "venv", "env", "node_modules", "bower_components", "build", "dist",
    "target", "coverage", "htmlcov",
})


@dataclass(frozen=True)
class WorkspaceCapabilities:
    """One immutable capability snapshot for an active workspace binding."""

    root: str
    mode: str
    is_git_repository: bool
    git_root: str
    branch: str
    commit: str
    supports_git_diff: bool
    supports_git_commands: bool

    @classmethod
    def detect(
        cls,
        root: str | Path,
        *,
        git_executable: str = "git",
        which: Callable[[str], str | None] = shutil.which,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> "WorkspaceCapabilities":
        workspace = Path(root).expanduser().resolve()
        if not workspace.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {workspace}")

        executable = which(git_executable)
        if executable:
            repository = cls._git_text(
                runner,
                executable,
                workspace,
                "rev-parse",
                "--show-toplevel",
            )
            if repository:
                git_root = Path(repository).expanduser().resolve()
                branch = cls._git_text(
                    runner,
                    executable,
                    workspace,
                    "branch",
                    "--show-current",
                ) or "detached"
                commit = cls._git_text(
                    runner,
                    executable,
                    workspace,
                    "rev-parse",
                    "HEAD",
                )
                return cls(
                    root=str(workspace),
                    mode=WORKSPACE_MODE_GIT,
                    is_git_repository=True,
                    git_root=str(git_root),
                    branch=branch,
                    commit=commit,
                    supports_git_diff=True,
                    supports_git_commands=True,
                )

        # A repository remains a Git workspace even when Git is unavailable or
        # temporarily cannot inspect it (for example, a safe-directory policy).
        # In that degraded state Orion records the boundary but does not expose
        # Git-backed commands or diffs.
        marker_root = cls._find_git_marker(workspace)
        if marker_root is not None:
            return cls(
                root=str(workspace),
                mode=WORKSPACE_MODE_GIT,
                is_git_repository=True,
                git_root=str(marker_root),
                branch="",
                commit="",
                supports_git_diff=False,
                supports_git_commands=False,
            )
        return cls(
            root=str(workspace),
            mode=WORKSPACE_MODE_STANDARD,
            is_git_repository=False,
            git_root="",
            branch="",
            commit="",
            supports_git_diff=False,
            supports_git_commands=False,
        )

    @classmethod
    def from_value(cls, value: Any) -> "WorkspaceCapabilities":
        fields = {
            "root", "mode", "is_git_repository", "git_root", "branch", "commit",
            "supports_git_diff", "supports_git_commands",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Workspace capabilities have an invalid schema.")
        root = Path(str(value["root"])).expanduser()
        if not root.is_absolute():
            raise ValueError("Workspace capability root must be absolute.")
        mode = str(value["mode"]).strip().lower()
        if mode not in WORKSPACE_MODES:
            raise ValueError("Workspace capability mode is not supported.")
        boolean_fields = (
            "is_git_repository", "supports_git_diff", "supports_git_commands",
        )
        if any(not isinstance(value[field], bool) for field in boolean_fields):
            raise ValueError("Workspace capability flags must be booleans.")
        is_git = value["is_git_repository"]
        if any(not isinstance(value[field], str) for field in ("git_root", "branch", "commit")):
            raise ValueError("Workspace Git metadata must be strings.")
        git_root_text = value["git_root"].strip()
        if mode == WORKSPACE_MODE_GIT:
            if not is_git or not git_root_text or not Path(git_root_text).is_absolute():
                raise ValueError("Git workspace capabilities require an absolute repository root.")
            try:
                root.resolve().relative_to(Path(git_root_text).resolve())
            except ValueError as exc:
                raise ValueError("Active workspace must remain inside its Git repository root.") from exc
        elif is_git or git_root_text or value["supports_git_diff"] or value["supports_git_commands"]:
            raise ValueError("Standard workspace capabilities cannot expose Git features.")
        return cls(
            root=str(root.resolve()),
            mode=mode,
            is_git_repository=is_git,
            git_root=str(Path(git_root_text).resolve()) if git_root_text else "",
            branch=value["branch"].strip()[:500],
            commit=value["commit"].strip()[:200],
            supports_git_diff=value["supports_git_diff"],
            supports_git_commands=value["supports_git_commands"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "mode": self.mode,
            "is_git_repository": self.is_git_repository,
            "git_root": self.git_root,
            "branch": self.branch,
            "commit": self.commit,
            "supports_git_diff": self.supports_git_diff,
            "supports_git_commands": self.supports_git_commands,
        }

    @staticmethod
    def _git_text(runner, executable: str, workspace: Path, *args: str) -> str:
        try:
            result = runner(
                [executable, "-C", str(workspace), *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if result.returncode != 0:
            return ""
        return str(result.stdout).strip()

    @staticmethod
    def _find_git_marker(workspace: Path) -> Path | None:
        for candidate in (workspace, *workspace.parents):
            if (candidate / ".git").exists():
                return candidate.resolve()
        return None


@dataclass(frozen=True)
class WorkspaceEntry:
    """A file or directory visible inside the active workspace."""

    name: str
    relative_path: str
    is_directory: bool
    size_bytes: int | None


class WorkspaceManager:
    """Manage Orion's active workspace and enforce path boundaries."""

    def __init__(
        self,
        root_path: str | Path = ".",
        *,
        capability_detector: Callable[[str | Path], WorkspaceCapabilities] | None = None,
    ):
        self._capability_detector = capability_detector or WorkspaceCapabilities.detect
        self._root = self._validate_workspace(root_path)
        self._capabilities = self._capability_detector(self._root)

    @property
    def root(self) -> Path:
        """Return the absolute active workspace path."""
        return self._root

    @property
    def capabilities(self) -> WorkspaceCapabilities:
        return self._capabilities

    def set_workspace(self, path: str | Path) -> Path:
        """Change the active workspace to an existing directory."""
        root = self._validate_workspace(path)
        capabilities = self._capability_detector(root)
        self._root = root
        self._capabilities = capabilities
        return self._root

    def refresh_capabilities(self) -> WorkspaceCapabilities:
        """Refresh Git metadata once at an approval or execution boundary."""
        capabilities = self._capability_detector(self._root)
        self._capabilities = capabilities
        return capabilities

    def create_workspace(self, path: str | Path) -> Path:
        """Create an explicitly approved workspace without initializing project metadata."""
        workspace = Path(path).expanduser().resolve()
        if workspace.exists():
            return self.set_workspace(workspace)
        self._require_safe_creation_path(workspace)
        workspace.mkdir(parents=True, exist_ok=False)
        return self.set_workspace(workspace)

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
            "capabilities": self._capabilities,
        }

    @staticmethod
    def _validate_workspace(path: str | Path) -> Path:
        workspace = Path(path).expanduser().resolve()
        if not workspace.exists():
            raise FileNotFoundError(f"Workspace does not exist: {workspace}")
        if not workspace.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {workspace}")
        return workspace

    @staticmethod
    def _require_safe_creation_path(workspace: Path) -> None:
        if not workspace.is_absolute() or workspace == Path(workspace.anchor):
            raise PermissionError("Protected filesystem roots cannot be created as workspaces.")
        protected: set[Path] = set()
        if os.name == "nt":
            for name in ("SYSTEMROOT", "WINDIR", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"):
                value = os.environ.get(name)
                if value:
                    protected.add(Path(value).expanduser().resolve())
        else:
            protected.update(Path(item) for item in (
                "/bin", "/boot", "/dev", "/etc", "/proc", "/root", "/sbin", "/sys", "/usr", "/var",
            ))
        for root in protected:
            try:
                workspace.relative_to(root)
            except ValueError:
                continue
            raise PermissionError(f"Protected system path cannot be created as a workspace: {workspace}")
