"""Process-lifetime singleton lock: one Orion writer per user-data root.

Orion's stores are guarded by process-local locks only, so two Orion
processes writing the same user-data root would silently corrupt state.
The singleton lock makes that impossible: the first process to start
holds an operating-system file lock for its entire lifetime, and any
second process fails fast with a clear message instead of starting.

The lock is held by the operating system, not by a lock file's contents:
``msvcrt.locking`` (stdlib, LockFileEx) on Windows, ``fcntl.flock``
(stdlib) on POSIX. The OS releases the lock automatically when the
process exits, including abrupt termination (crash, TerminateProcess),
so there is no stale file to take over and no takeover protocol to
implement.

Two files are involved. The lock file itself (``server.lock``) is empty
and is never read or written after creation: on Windows a locked byte
range cannot be read by another handle, so holder metadata cannot live
in it. Diagnostic metadata (PID, mode, started_at, hostname) lives in a
sibling file (``server.lock.json``), written by the holder just after
acquiring. The metadata is diagnostic only and is never consulted for
ownership decisions.

This is not the Mission Coordinator's operation lock: that one guards a
single mission advance and is content-match-guarded; this one guards the
whole process lifetime.
"""
from __future__ import annotations

import json
import os
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path


try:  # Windows: LockFileEx via the C runtime.
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows
    msvcrt = None  # type: ignore[assignment]

try:  # POSIX: flock.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]


class SingletonLockHeld(RuntimeError):
    """Raised when another Orion process already holds the singleton lock."""

    def __init__(self, lock_path: Path, holder: dict) -> None:
        self.lock_path = Path(lock_path)
        self.holder = dict(holder)
        pid = holder.get("pid", "unknown")
        mode = holder.get("mode", "unknown")
        started = holder.get("started_at", "unknown")
        host = holder.get("hostname", "")
        detail = f"PID {pid}, mode {mode}, started {started}"
        if host:
            detail += f", host {host}"
        super().__init__(
            "Another Orion process already owns this user-data root "
            f"({detail}). Stop that process before starting another Orion. "
            f"Lock file: {lock_path}"
        )


class SingletonLock:
    """A lifetime-held OS file lock on one user-data root.

    Acquire once at process startup, hold for the process lifetime,
    release on graceful shutdown. On abrupt termination the operating
    system releases the lock; the next start acquires it immediately
    with no manual cleanup.
    """

    _held_paths: set[str] = set()
    _held_guard = threading.Lock()

    def __init__(self, lock_path: str | Path, *, mode: str) -> None:
        self.lock_path = Path(lock_path)
        self.mode = str(mode).strip() or "unknown"
        self._fd: int | None = None
        self._key = str(self.lock_path.resolve()) if self._resolvable() else str(self.lock_path)

    @property
    def metadata_path(self) -> Path:
        """Sibling file carrying diagnostic holder metadata."""
        return Path(f"{self.lock_path}.json")

    def _resolvable(self) -> bool:
        try:
            self.lock_path.resolve()
        except (OSError, RuntimeError):
            return False
        return True

    def acquire(self) -> "SingletonLock":
        """Acquire the lock (non-blocking) or raise SingletonLockHeld."""
        if self._fd is not None:
            raise RuntimeError("Singleton lock is already held by this instance.")
        if msvcrt is None and fcntl is None:
            raise RuntimeError(
                "No operating-system file locking is available on this platform."
            )
        with self._held_guard:
            if self._key in self._held_paths:
                raise SingletonLockHeld(
                    self.lock_path,
                    {"pid": os.getpid(), "mode": self.mode, "started_at": "this process"},
                )
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                self._lock_fd(fd)
            except OSError as exc:
                os.close(fd)
                raise SingletonLockHeld(
                    self.lock_path, self.holder_info(self.lock_path)
                ) from exc
            self._fd = fd
            self._held_paths.add(self._key)
        self._write_metadata()
        return self

    def release(self) -> None:
        """Release the lock. Safe to call more than once."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        with self._held_guard:
            self._held_paths.discard(self._key)
        try:
            self._unlock_fd(fd)
        finally:
            os.close(fd)

    @property
    def held(self) -> bool:
        return self._fd is not None

    def __enter__(self) -> "SingletonLock":
        return self.acquire()

    def __exit__(self, *exc_info) -> None:
        self.release()

    @staticmethod
    def metadata_path_for(lock_path: str | Path) -> Path:
        return Path(f"{Path(lock_path)}.json")

    @classmethod
    def holder_info(cls, lock_path: str | Path) -> dict:
        """Read the diagnostic metadata for a lock file, if any.

        Best-effort only: a missing, partial, or malformed file yields an
        empty dict. The metadata never decides ownership.
        """
        try:
            raw = cls.metadata_path_for(lock_path).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return {}
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            key: value[key]
            for key in ("pid", "mode", "started_at", "hostname")
            if key in value
        }

    @staticmethod
    def _lock_fd(fd: int) -> None:
        if msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        elif fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:  # pragma: no cover - guarded in acquire()
            raise RuntimeError("No operating-system file locking is available.")

    @staticmethod
    def _unlock_fd(fd: int) -> None:
        try:
            if msvcrt is not None:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            elif fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            # Releasing a lock must never fail shutdown; the OS releases
            # it when the descriptor closes regardless.
            pass

    def _write_metadata(self) -> None:
        """Write diagnostic holder metadata to the sibling file.

        Best-effort and atomic (temp file + replace): a reader sees a
        complete record or none at all, never a torn one.
        """
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "mode": self.mode,
                "started_at": datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                ),
                "hostname": socket.gethostname(),
            },
            sort_keys=True,
        )
        target = self.metadata_path
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, target)
        except OSError:
            # Metadata is diagnostic only; a failed write must not fail
            # the lock acquisition.
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass
