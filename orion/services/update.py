"""Safe self-update workflow for Git-based Orion installations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import shutil

from orion.services.git_service import GitError, GitService


@dataclass(frozen=True)
class UpdateCheck:
    current: str
    available: bool
    behind: int
    ahead: int
    branch: str
    upstream: str


class UpdateService:
    def __init__(self, git: GitService, runtime_root: str | Path = ".orion") -> None:
        self.git = git
        self.runtime_root = Path(runtime_root)

    def check(self, *, fetch: bool = True) -> UpdateCheck:
        if fetch:
            self.git.fetch()
        status = self.git.status()
        current = self.git._text("rev-parse", "--short", "HEAD")
        return UpdateCheck(current, status.behind > 0, status.behind, status.ahead, status.branch, status.upstream)

    def backup(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # Keep backups outside the Git working tree. Creating a backup under
        # .orion/backups before pulling would make an otherwise clean repository
        # dirty and cause GitService.pull() to cancel the update.
        backup_root = self.git.root.parent / f"{self.git.root.name}-backups" / f"update-{timestamp}"
        backup_root.mkdir(parents=True, exist_ok=False)
        for relative in (Path("config"), Path(".orion")):
            source = self.git.root / relative
            if not source.exists():
                continue
            destination = backup_root / relative
            if relative == Path(".orion"):
                self._copy_runtime(source, destination, backup_root.parent)
            elif source.is_dir():
                shutil.copytree(source, destination)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        return backup_root

    def apply(self) -> tuple[Path, str]:
        status = self.git.status()
        if status.dirty:
            raise GitError("Update cancelled: working tree has uncommitted changes.")
        backup_path = self.backup()
        output = self.git.pull(branch=status.branch)
        return backup_path, output

    @staticmethod
    def _copy_runtime(source: Path, destination: Path, backup_parent: Path) -> None:
        def ignore(path: str, names: list[str]) -> set[str]:
            current = Path(path).resolve()
            ignored: set[str] = set()
            if current == source.resolve() and "backups" in names:
                ignored.add("backups")
            return ignored
        shutil.copytree(source, destination, ignore=ignore)
