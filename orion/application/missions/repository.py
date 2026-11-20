"""Safe external JSON persistence for Mission records."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
from threading import RLock
import time
from typing import Iterator
from uuid import uuid4

from orion.application.missions.models import (
    MISSION_ID_PATTERN,
    Mission,
    MissionStatus,
)


DEFAULT_MAX_MISSION_BYTES = 1_048_576
MAX_MISSION_LIST_LIMIT = 500


class MissionRepository:
    """Store strict Mission JSON outside Orion's source repository."""

    def __init__(
        self,
        root: str | Path,
        *,
        forbidden_root: str | Path | None = None,
        max_record_bytes: int = DEFAULT_MAX_MISSION_BYTES,
        lock_timeout_seconds: float = 2.0,
    ) -> None:
        candidate = Path(root).expanduser()
        self.root = candidate if candidate.is_absolute() else candidate.absolute()
        self.forbidden_root = (
            Path(forbidden_root).expanduser().resolve()
            if forbidden_root is not None else None
        )
        if self.forbidden_root is not None and self._within(
            self.root.resolve(), self.forbidden_root
        ):
            raise ValueError("Mission storage cannot be inside the application repository.")
        if (
            isinstance(max_record_bytes, bool)
            or not isinstance(max_record_bytes, int)
            or not 16_384 <= max_record_bytes <= 8_388_608
        ):
            raise ValueError("Mission record limit must be between 16384 and 8388608 bytes.")
        if not 0.05 <= float(lock_timeout_seconds) <= 30.0:
            raise ValueError("Mission lock timeout must be between 0.05 and 30 seconds.")
        self.max_record_bytes = max_record_bytes
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self._lock = RLock()

    def create(self, mission: Mission) -> Path:
        validated = Mission.from_value(mission.to_dict())
        with self._lock:
            self._ensure_root()
            with self._storage_lock():
                duplicate = self._find_by_proposal_unlocked(validated.proposal_id)
                if duplicate is not None:
                    raise FileExistsError(
                        "A Mission already exists for Goal Proposal "
                        f"{validated.proposal_id}: {duplicate.mission_id}"
                    )
                path = self._path(validated.mission_id)
                self._validate_existing_file(path, optional=True)
                if path.exists():
                    raise FileExistsError(f"Mission already exists: {validated.mission_id}")
                self._atomic_write(path, validated)
        return path

    def get(self, mission_id: str) -> Mission:
        normalized = self._normalize_id(mission_id)
        path = self._path(normalized)
        with self._lock:
            if self.root.exists() or self.root.is_symlink():
                self._validate_directory(self.root)
            mission = self._read(path)
        if mission.mission_id != normalized:
            raise ValueError("Mission identity does not match its filename.")
        return mission

    def find_by_proposal(self, proposal_id: str) -> Mission | None:
        proposal = str(proposal_id).strip()
        if not proposal:
            raise ValueError("Goal Proposal ID is required.")
        with self._lock:
            if not self.root.exists():
                return None
            self._validate_directory(self.root)
            return self._find_by_proposal_unlocked(proposal)

    def list(
        self,
        *,
        status: MissionStatus | str | None = None,
        goal_id: str = "",
        proposal_id: str = "",
        limit: int = 100,
    ) -> tuple[Mission, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("Mission list limit must be an integer.")
        if not 1 <= limit <= MAX_MISSION_LIST_LIMIT:
            raise ValueError(
                f"Mission list limit must be between 1 and {MAX_MISSION_LIST_LIMIT}."
            )
        selected_status = MissionStatus.parse(status) if status else None
        selected_goal = str(goal_id).strip()
        selected_proposal = str(proposal_id).strip()
        with self._lock:
            if not self.root.exists():
                return ()
            self._validate_directory(self.root)
            missions = tuple(self._read(path) for path in self._record_paths())
        filtered = [
            mission for mission in missions
            if (selected_status is None or mission.status is selected_status)
            and (not selected_goal or mission.goal_id == selected_goal)
            and (not selected_proposal or mission.proposal_id == selected_proposal)
        ]
        filtered.sort(
            key=lambda item: (item.created_at, item.mission_id),
            reverse=True,
        )
        return tuple(filtered[:limit])

    def replace(self, mission: Mission, *, expected_updated_at: str) -> Path:
        validated = Mission.from_value(mission.to_dict())
        path = self._path(validated.mission_id)
        with self._lock:
            self._ensure_root()
            with self._storage_lock():
                current = self._read(path)
                if current.updated_at != str(expected_updated_at).strip():
                    raise PermissionError(
                        "Mission changed during reconciliation; reload before writing."
                    )
                if (
                    current.mission_id != validated.mission_id
                    or current.goal_id != validated.goal_id
                    or current.proposal_id != validated.proposal_id
                    or current.proposal_version != validated.proposal_version
                    or current.created_at != validated.created_at
                ):
                    raise ValueError("Mission immutable identity fields cannot change.")
                self._atomic_write(path, validated)
        return path

    def validate_location(self, mission_id: str) -> bool:
        path = self._path(self._normalize_id(mission_id))
        if self.forbidden_root is not None and self._within(
            path.resolve(), self.forbidden_root
        ):
            return False
        if self.root.is_symlink() or path.is_symlink():
            return False
        return True

    def _find_by_proposal_unlocked(self, proposal_id: str) -> Mission | None:
        for path in self._record_paths():
            mission = self._read(path)
            if mission.proposal_id == proposal_id:
                return mission
        return None

    def _read(self, path: Path) -> Mission:
        self._validate_existing_file(path)
        size = path.stat().st_size
        if size > self.max_record_bytes:
            raise ValueError(f"Mission record exceeds {self.max_record_bytes} bytes.")
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Mission record is malformed: {path.name}") from exc
        return Mission.from_value(value)

    def _atomic_write(self, path: Path, mission: Mission) -> None:
        encoded = (mission.to_json(indent=2) + "\n").encode("utf-8")
        if len(encoded) > self.max_record_bytes:
            raise ValueError(f"Mission record exceeds {self.max_record_bytes} bytes.")
        temporary = self.root / f".{mission.mission_id}.{uuid4().hex}.tmp"
        descriptor = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._restrict_permissions(temporary)
            self._validate_existing_file(path, optional=True)
            os.replace(temporary, path)
            self._restrict_permissions(path)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()

    def _ensure_root(self) -> None:
        if self.root.is_symlink():
            raise PermissionError("Mission storage cannot be a symlink.")
        if self.forbidden_root is not None and self._within(
            self.root.resolve(), self.forbidden_root
        ):
            raise PermissionError("Mission storage cannot be inside the application repository.")
        self.root.mkdir(parents=True, exist_ok=True)
        self._validate_directory(self.root)
        self._restrict_permissions(self.root, directory=True)

    @contextmanager
    def _storage_lock(self) -> Iterator[None]:
        lock_path = self.root / ".mission.lock"
        deadline = time.monotonic() + self.lock_timeout_seconds
        descriptor = None
        while descriptor is None:
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                if lock_path.is_symlink():
                    raise PermissionError("Mission storage lock cannot be a symlink.")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Mission storage lock timed out.")
                time.sleep(0.02)
        try:
            yield
        finally:
            os.close(descriptor)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def _record_paths(self) -> tuple[Path, ...]:
        paths = tuple(self.root.glob("mission-*.json"))
        for path in paths:
            self._validate_existing_file(path)
        return tuple(sorted(paths, key=lambda item: item.name))

    def _path(self, mission_id: str) -> Path:
        return self.root / f"{mission_id}.json"

    @staticmethod
    def _normalize_id(value: object) -> str:
        normalized = str(value).strip().lower()
        if not MISSION_ID_PATTERN.fullmatch(normalized):
            raise ValueError("Mission ID has an invalid format.")
        return normalized

    @staticmethod
    def _validate_directory(path: Path) -> None:
        if path.is_symlink():
            raise PermissionError("Mission storage cannot be a symlink.")
        if not path.is_dir():
            raise NotADirectoryError("Mission storage path is not a directory.")

    @staticmethod
    def _validate_existing_file(path: Path, *, optional: bool = False) -> None:
        if path.is_symlink():
            raise PermissionError("Mission record symlinks are not allowed.")
        if not path.exists():
            if optional:
                return
            raise FileNotFoundError(f"Mission not found: {path.stem}")
        if not path.is_file():
            raise ValueError("Mission record path is not a file.")
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise ValueError("Mission record must be a regular file.")

    @staticmethod
    def _restrict_permissions(path: Path, *, directory: bool = False) -> None:
        try:
            path.chmod(0o700 if directory else 0o600)
        except OSError:
            pass

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False
