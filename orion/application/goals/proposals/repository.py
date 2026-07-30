"""Atomic external persistence for Goal Proposal records."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
from threading import RLock
from typing import Iterator
from uuid import uuid4

from orion.application.goals.proposals.models import (
    PROPOSAL_ID_PATTERN,
    GoalProposal,
    GoalProposalStatus,
)


MAX_PROPOSAL_BYTES = 512_000
MAX_PROPOSAL_LIST_LIMIT = 500


class GoalProposalRepository:
    """Store strict JSON proposals outside Orion's source repository."""

    def __init__(
        self,
        root: str | Path,
        *,
        forbidden_root: str | Path | None = None,
    ) -> None:
        candidate = Path(root).expanduser()
        self.root = candidate if candidate.is_absolute() else candidate.absolute()
        self.forbidden_root = (
            Path(forbidden_root).expanduser().resolve()
            if forbidden_root is not None
            else None
        )
        if (
            self.forbidden_root is not None
            and self._within(self.root.resolve(), self.forbidden_root)
        ):
            raise ValueError(
                "Goal Proposal storage cannot be inside the application repository."
            )
        self._lock = RLock()

    def save(self, proposal: GoalProposal) -> Path:
        validated = GoalProposal.from_value(proposal.to_dict())
        path = self._path(validated.proposal_id)
        with self._lock:
            with self._record_locks(path):
                if path.exists():
                    raise FileExistsError(
                        f"Goal Proposal already exists: {validated.proposal_id}"
                    )
                self._atomic_write(path, validated)
        return path

    def get(self, proposal_id: str) -> GoalProposal:
        normalized = self._normalize_id(proposal_id)
        path = self._path(normalized)
        with self._lock:
            proposal = self._read(path)
        if proposal.proposal_id != normalized:
            raise ValueError("Goal Proposal identity does not match its filename.")
        return proposal

    def list(
        self,
        *,
        status: GoalProposalStatus | str | None = None,
        goal_id: str = "",
        limit: int = 100,
    ) -> tuple[GoalProposal, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("Goal Proposal list limit must be an integer.")
        if not 1 <= limit <= MAX_PROPOSAL_LIST_LIMIT:
            raise ValueError(
                f"Goal Proposal list limit must be between 1 and "
                f"{MAX_PROPOSAL_LIST_LIMIT}."
            )
        selected_status = (
            GoalProposalStatus.parse(status)
            if status not in {None, ""}
            else None
        )
        selected_goal = str(goal_id).strip()
        with self._lock:
            if not self.root.exists():
                return ()
            self._validate_directory(self.root)
            paths = sorted(
                self.root.glob("proposal-*.json"),
                key=lambda item: item.name.casefold(),
            )
            proposals = tuple(self._read(path) for path in paths)
        filtered = [
            item
            for item in proposals
            if (
                (selected_status is None or item.status is selected_status)
                and (not selected_goal or item.goal_id == selected_goal)
            )
        ]
        filtered.sort(
            key=lambda item: (item.created_at, item.proposal_id),
            reverse=True,
        )
        return tuple(filtered[:limit])

    def replace(
        self,
        proposal: GoalProposal,
        *,
        expected_status: GoalProposalStatus | tuple[GoalProposalStatus, ...],
    ) -> Path:
        validated = GoalProposal.from_value(proposal.to_dict())
        expected = (
            (expected_status,)
            if isinstance(expected_status, GoalProposalStatus)
            else tuple(expected_status)
        )
        if not expected:
            raise ValueError("Proposal replacement requires an expected status.")
        path = self._path(validated.proposal_id)
        with self._lock:
            with self._record_locks(path):
                current = self._read(path)
                if current.status not in expected:
                    choices = ", ".join(item.value for item in expected)
                    raise PermissionError(
                        f"Goal Proposal state changed; expected {choices}, "
                        f"found {current.status.value}."
                    )
                self._validate_same_immutable_record(current, validated)
                self._atomic_write(path, validated)
        return path

    def save_supersession(
        self,
        previous: GoalProposal,
        replacement: GoalProposal,
    ) -> tuple[Path, Path]:
        """Safely block the old proposal before publishing its replacement."""
        old = GoalProposal.from_value(previous.to_dict())
        new = GoalProposal.from_value(replacement.to_dict())
        if old.status is not GoalProposalStatus.SUPERSEDED:
            raise ValueError("Previous proposal must already be marked superseded.")
        if new.status is not GoalProposalStatus.PENDING:
            raise ValueError("Replacement proposal must be pending.")
        if old.superseded_by != new.proposal_id or new.supersedes != old.proposal_id:
            raise ValueError("Proposal supersession links do not match.")
        old_path = self._path(old.proposal_id)
        new_path = self._path(new.proposal_id)
        with self._lock:
            with self._record_locks(old_path, new_path):
                current = self._read(old_path)
                if current.status is not GoalProposalStatus.PENDING:
                    raise PermissionError(
                        "Only a pending Goal Proposal may be superseded."
                    )
                if new_path.exists():
                    raise FileExistsError(
                        f"Goal Proposal already exists: {new.proposal_id}"
                    )
                self._validate_same_immutable_record(current, old)
                # Writing the old record first is fail-closed: a partial storage
                # failure can block an old proposal but can never leave both usable.
                self._atomic_write(old_path, old)
                self._atomic_write(new_path, new)
        return old_path, new_path

    @staticmethod
    def _validate_same_immutable_record(
        current: GoalProposal,
        replacement: GoalProposal,
    ) -> None:
        if (
            current.proposal_id != replacement.proposal_id
            or current.created_at != replacement.created_at
            or current.plan_hash != replacement.plan_hash
            or current.snapshot().to_dict() != replacement.snapshot().to_dict()
        ):
            raise PermissionError(
                "Goal Proposal immutable content cannot be replaced."
            )

    def _atomic_write(self, path: Path, proposal: GoalProposal) -> None:
        self._ensure_root()
        self._validate_existing_file(path, optional=True)
        content = json.dumps(
            proposal.to_dict(),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n"
        if len(content.encode("utf-8")) > MAX_PROPOSAL_BYTES:
            raise ValueError("Goal Proposal record exceeds the size limit.")
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            self._restrict_permissions(temporary)
            os.replace(temporary, path)
            self._restrict_permissions(path)
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass

    def _read(self, path: Path) -> GoalProposal:
        self._validate_existing_file(path)
        if path.stat().st_size > MAX_PROPOSAL_BYTES:
            raise ValueError("Goal Proposal record exceeds the size limit.")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError) as exc:
            raise ValueError(
                f"Goal Proposal record is malformed: {path.name}"
            ) from exc
        try:
            proposal = GoalProposal.from_value(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Goal Proposal record is invalid: {path.name}"
            ) from exc
        if path.stem != proposal.proposal_id:
            raise ValueError("Goal Proposal identity does not match its filename.")
        return proposal

    def _path(self, proposal_id: str) -> Path:
        return self.root / f"{self._normalize_id(proposal_id)}.json"

    @staticmethod
    def _normalize_id(value: object) -> str:
        normalized = str(value).strip().lower()
        if not PROPOSAL_ID_PATTERN.fullmatch(normalized):
            raise ValueError("Goal Proposal ID has an invalid format.")
        return normalized

    def _ensure_root(self) -> None:
        if self.root.is_symlink():
            raise PermissionError("Goal Proposal storage cannot be a symlink.")
        if (
            self.forbidden_root is not None
            and self._within(self.root.resolve(), self.forbidden_root)
        ):
            raise PermissionError(
                "Goal Proposal storage cannot be inside the application repository."
            )
        self.root.mkdir(parents=True, exist_ok=True)
        self._validate_directory(self.root)
        self._restrict_permissions(self.root)

    @contextmanager
    def _record_locks(self, *paths: Path) -> Iterator[None]:
        """Serialize record transitions across Orion processes.

        A stale lock left by a terminated process intentionally fails closed and
        requires operator inspection; silently breaking it could permit replay.
        """
        self._ensure_root()
        handles = []
        lock_paths: list[Path] = []
        try:
            for path in sorted(set(paths), key=lambda item: item.name.casefold()):
                lock_path = path.with_name(f".{path.name}.lock")
                try:
                    handle = lock_path.open("x", encoding="utf-8", newline="\n")
                except FileExistsError as exc:
                    raise PermissionError(
                        f"Goal Proposal is already being updated: {path.stem}"
                    ) from exc
                handles.append(handle)
                lock_paths.append(lock_path)
                handle.write(f"{os.getpid()}\n")
                handle.flush()
                os.fsync(handle.fileno())
                self._restrict_permissions(lock_path)
            yield
        finally:
            for handle in handles:
                try:
                    handle.close()
                except OSError:
                    pass
            for lock_path in reversed(lock_paths):
                try:
                    lock_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _validate_directory(path: Path) -> None:
        if path.is_symlink():
            raise PermissionError("Goal Proposal storage cannot be a symlink.")
        if not path.is_dir():
            raise NotADirectoryError(
                "Goal Proposal storage path is not a directory."
            )

    @staticmethod
    def _validate_existing_file(path: Path, *, optional: bool = False) -> None:
        if not path.exists():
            if optional:
                return
            raise FileNotFoundError(f"Goal Proposal not found: {path.stem}")
        if path.is_symlink():
            raise PermissionError("Goal Proposal record symlinks are not allowed.")
        if not path.is_file():
            raise ValueError("Goal Proposal record path is not a file.")

    @staticmethod
    def _restrict_permissions(path: Path) -> None:
        try:
            mode = (
                stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
                if path.is_dir()
                else stat.S_IRUSR | stat.S_IWUSR
            )
            os.chmod(path, mode)
        except OSError:
            pass

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False
