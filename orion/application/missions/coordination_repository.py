"""Safe audit persistence and cross-process locks for Mission advancement."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from threading import RLock
import time
from typing import Iterator
from uuid import uuid4

from orion.application.missions.coordination_models import MissionAdvanceAudit
from orion.application.missions.models import MISSION_ID_PATTERN


DEFAULT_MAX_COORDINATION_BYTES = 65_536


class MissionCoordinationRepository:
    """Persist one bounded coordination audit per Mission outside source."""

    def __init__(
        self,
        root: str | Path,
        *,
        forbidden_root: str | Path | None = None,
        max_record_bytes: int = DEFAULT_MAX_COORDINATION_BYTES,
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
            raise ValueError(
                "Mission coordination storage cannot be inside the application repository."
            )
        if (
            isinstance(max_record_bytes, bool)
            or not isinstance(max_record_bytes, int)
            or not 4_096 <= max_record_bytes <= 262_144
        ):
            raise ValueError(
                "Mission coordination record limit must be between 4096 and 262144 bytes."
            )
        if not 0.05 <= float(lock_timeout_seconds) <= 30.0:
            raise ValueError(
                "Mission coordination lock timeout must be between 0.05 and 30 seconds."
            )
        self.max_record_bytes = max_record_bytes
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self._lock = RLock()

    def get(self, mission_id: str) -> MissionAdvanceAudit | None:
        path = self._path(self._normalize_id(mission_id))
        with self._lock:
            if self.root.exists() or self.root.is_symlink():
                self._validate_directory(self.root)
            if path.is_symlink():
                raise PermissionError("Mission coordination audit symlinks are not allowed.")
            if not path.exists():
                return None
            return self._read(path)

    def write(
        self,
        audit: MissionAdvanceAudit,
        *,
        expected_attempt_id: str | None,
    ) -> Path:
        validated = MissionAdvanceAudit.from_value(audit.to_dict())
        path = self._path(validated.mission_id)
        with self._lock:
            self._ensure_root()
            current = self._read(path) if path.exists() or path.is_symlink() else None
            if current is None:
                if expected_attempt_id is not None:
                    raise PermissionError("Mission coordination audit changed before write.")
            elif current.attempt_id != str(expected_attempt_id or ""):
                raise PermissionError("Mission coordination audit changed before write.")
            self._atomic_write(path, validated)
        return path

    def lock_exists(self, mission_id: str) -> bool:
        path = self._lock_path(self._normalize_id(mission_id))
        if path.is_symlink():
            raise PermissionError("Mission coordination lock cannot be a symlink.")
        return path.exists()

    @contextmanager
    def advance_lock(self, mission_id: str) -> Iterator[None]:
        selected = self._normalize_id(mission_id)
        with self._lock:
            self._ensure_root()
        lock_path = self._lock_path(selected)
        deadline = time.monotonic() + self.lock_timeout_seconds
        descriptor = None
        while descriptor is None:
            if lock_path.is_symlink():
                raise PermissionError("Mission coordination lock cannot be a symlink.")
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Mission coordination lock timed out; another advance may "
                        "be active or its outcome may be uncertain."
                    )
                time.sleep(0.02)
        try:
            self._restrict_permissions(lock_path)
            yield
        finally:
            os.close(descriptor)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def _read(self, path: Path) -> MissionAdvanceAudit:
        self._validate_file(path)
        if path.stat().st_size > self.max_record_bytes:
            raise ValueError("Mission coordination audit exceeds its size limit.")
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError("Mission coordination audit is malformed.") from exc
        return MissionAdvanceAudit.from_value(value)

    def _atomic_write(self, path: Path, audit: MissionAdvanceAudit) -> None:
        encoded = (
            json.dumps(
                audit.to_dict(),
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            ) + "\n"
        ).encode("utf-8")
        if len(encoded) > self.max_record_bytes:
            raise ValueError("Mission coordination audit exceeds its size limit.")
        temporary = self.root / f".{audit.mission_id}.{uuid4().hex}.tmp"
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
            if path.is_symlink():
                raise PermissionError(
                    "Mission coordination audit symlinks are not allowed."
                )
            os.replace(temporary, path)
            self._restrict_permissions(path)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()

    def _ensure_root(self) -> None:
        if self.root.is_symlink():
            raise PermissionError("Mission coordination storage cannot be a symlink.")
        if self.forbidden_root is not None and self._within(
            self.root.resolve(), self.forbidden_root
        ):
            raise PermissionError(
                "Mission coordination storage cannot be inside the application repository."
            )
        self.root.mkdir(parents=True, exist_ok=True)
        self._validate_directory(self.root)
        self._restrict_permissions(self.root, directory=True)

    def _path(self, mission_id: str) -> Path:
        return self.root / f"{mission_id}.json"

    def _lock_path(self, mission_id: str) -> Path:
        return self.root / f".{mission_id}.advance.lock"

    @staticmethod
    def _normalize_id(value: object) -> str:
        selected = str(value).strip().lower()
        if not MISSION_ID_PATTERN.fullmatch(selected):
            raise ValueError("Mission ID has an invalid format.")
        return selected

    @staticmethod
    def _validate_directory(path: Path) -> None:
        if path.is_symlink():
            raise PermissionError("Mission coordination storage cannot be a symlink.")
        if not path.is_dir():
            raise NotADirectoryError(
                "Mission coordination storage path is not a directory."
            )

    @staticmethod
    def _validate_file(path: Path) -> None:
        if path.is_symlink():
            raise PermissionError("Mission coordination audit symlinks are not allowed.")
        if not path.is_file():
            raise FileNotFoundError("Mission coordination audit was not found.")

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
