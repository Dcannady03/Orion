"""Safe Git operations for Orion-managed repositories."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess


class GitError(RuntimeError):
    """Raised when a Git command fails or the workspace is not a repository."""


@dataclass(frozen=True)
class GitStatus:
    branch: str
    upstream: str
    ahead: int
    behind: int
    dirty: bool
    changes: tuple[str, ...]


class GitService:
    def __init__(self, root: str | Path, executable: str = "git") -> None:
        self.root = Path(root).resolve()
        self.executable = executable

    def rebind(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def is_repository(self) -> bool:
        if not self.available():
            return False
        result = self._run("rev-parse", "--is-inside-work-tree", check=False)
        return result.returncode == 0 and result.stdout.strip() == "true"

    def status(self) -> GitStatus:
        self._require_repository()
        branch = self._text("branch", "--show-current") or "detached"
        upstream_result = self._run(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False
        )
        upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else ""
        ahead = behind = 0
        if upstream:
            counts = self._text("rev-list", "--left-right", "--count", f"{upstream}...HEAD").split()
            if len(counts) == 2:
                behind, ahead = (int(counts[0]), int(counts[1]))
        lines = tuple(line for line in self._text("status", "--short").splitlines() if line)
        return GitStatus(branch, upstream, ahead, behind, bool(lines), lines)

    def fetch(self, remote: str = "origin") -> str:
        self._require_repository()
        return self._text("fetch", "--prune", remote)

    def pull(self, remote: str = "origin", branch: str | None = None) -> str:
        self._require_clean()
        selected = branch or self.status().branch
        if selected == "detached":
            raise GitError("Cannot pull while HEAD is detached.")
        return self._text("pull", "--ff-only", remote, selected)

    def push(self, remote: str = "origin", branch: str | None = None) -> str:
        self._require_repository()
        selected = branch or self.status().branch
        if selected == "detached":
            raise GitError("Cannot push while HEAD is detached.")
        return self._text("push", remote, selected)

    def log(self, limit: int = 8) -> str:
        self._require_repository()
        safe_limit = max(1, min(int(limit), 50))
        return self._text("log", f"-{safe_limit}", "--oneline", "--decorate")

    def diff(self, *, staged: bool = False) -> str:
        self._require_repository()
        args = ["diff"]
        if staged:
            args.append("--cached")
        return self._text(*args)

    def _require_repository(self) -> None:
        if not self.available():
            raise GitError("Git is not installed or is not available on PATH.")
        if not self.is_repository():
            raise GitError(f"Workspace is not a Git repository: {self.root}")

    def _require_clean(self) -> None:
        status = self.status()
        if status.dirty:
            raise GitError("Working tree has uncommitted changes. Commit or stash them before updating.")

    def _text(self, *args: str) -> str:
        return self._run(*args).stdout.strip()

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [self.executable, *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "Git command failed."
            raise GitError(detail)
        return result
