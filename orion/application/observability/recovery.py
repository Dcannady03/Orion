"""Transparent, queryable record of recoveries and rollbacks performed.

Every recovery Orion performs — backup imports, update rollbacks, team
rollbacks — is appended as one JSON line under the observability
directory. The record is append-only and queryable: list recent
recoveries, filter by kind or status, or show one record in detail.
Recording never blocks the recovery itself: a failed write is reported
by the caller, never raised into the recovery path.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

RECOVERY_FILE_NAME = "recovery-history.jsonl"
RECOVERY_KINDS = ("backup-import", "update-rollback", "team-rollback", "remediation", "docker", "shell", "mission-resolve", "server-auth")
RECOVERY_STATUSES = ("success", "failed")
DEFAULT_RECOVERY_LIMIT = 20
MAX_RECOVERY_LIMIT = 500


@dataclass(frozen=True)
class RecoveryRecord:
    """One performed recovery or rollback."""

    record_id: str
    occurred_at: str
    kind: str
    target: str
    status: str
    detail: str
    safety_backup: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "record_id": self.record_id,
            "occurred_at": self.occurred_at,
            "kind": self.kind,
            "target": self.target,
            "status": self.status,
            "detail": self.detail,
            "safety_backup": self.safety_backup,
        }


@dataclass(frozen=True)
class RecoveryQuery:
    """Recovery records newest first, plus load warnings."""

    records: tuple[RecoveryRecord, ...] = ()
    skipped: int = 0
    total: int = 0


class RecoveryHistoryStore:
    """Append-only JSONL storage for recovery records."""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._root = Path(root)
        self._path = self._root / RECOVERY_FILE_NAME
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def path(self) -> Path:
        return self._path

    def record(
        self,
        kind: str,
        target: str,
        *,
        status: str = "success",
        detail: str = "",
        safety_backup: str = "",
    ) -> RecoveryRecord:
        """Append one recovery record. Raises ValueError for bad kind/status."""
        if kind not in RECOVERY_KINDS:
            raise ValueError(
                f"Recovery kind must be one of: {', '.join(RECOVERY_KINDS)}."
            )
        if status not in RECOVERY_STATUSES:
            raise ValueError(
                f"Recovery status must be one of: {', '.join(RECOVERY_STATUSES)}."
            )
        entry = RecoveryRecord(
            record_id=f"rec-{uuid.uuid4().hex[:12]}",
            occurred_at=self._clock().isoformat(),
            kind=kind,
            target=str(target),
            status=status,
            detail=str(detail),
            safety_backup=str(safety_backup),
        )
        self._root.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry.to_dict(), sort_keys=True) + "\n"
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(line)
        return entry

    def query(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        limit: int = DEFAULT_RECOVERY_LIMIT,
    ) -> RecoveryQuery:
        """Return matching records, newest first, bounded by the limit."""
        if kind is not None and kind not in RECOVERY_KINDS:
            raise ValueError(
                f"Recovery kind must be one of: {', '.join(RECOVERY_KINDS)}."
            )
        if status is not None and status not in RECOVERY_STATUSES:
            raise ValueError(
                f"Recovery status must be one of: {', '.join(RECOVERY_STATUSES)}."
            )
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("Recovery limit must be an integer.")
        limit = max(1, min(limit, MAX_RECOVERY_LIMIT))
        records, skipped = self._load()
        total = len(records)
        filtered = [
            entry
            for entry in records
            if (kind is None or entry.kind == kind)
            and (status is None or entry.status == status)
        ]
        filtered.reverse()
        return RecoveryQuery(tuple(filtered[:limit]), skipped, total)

    def get(self, record_id: str) -> RecoveryRecord | None:
        """Return one record by id, or None when it does not exist."""
        records, _ = self._load()
        for entry in records:
            if entry.record_id == record_id:
                return entry
        return None

    def _load(self) -> tuple[list[RecoveryRecord], int]:
        if not self._path.is_file():
            return [], 0
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return [], 0
        records: list[RecoveryRecord] = []
        skipped = 0
        for line in lines:
            entry = _parse_record(line)
            if entry is None:
                skipped += 1
                continue
            records.append(entry)
        return records, skipped


def _parse_record(line: str) -> RecoveryRecord | None:
    """Parse one history line, returning None when it is malformed."""
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    required = ("record_id", "occurred_at", "kind", "status")
    if any(not isinstance(data.get(key), str) or not data.get(key) for key in required):
        return None
    if data["kind"] not in RECOVERY_KINDS or data["status"] not in RECOVERY_STATUSES:
        return None
    for key in ("target", "detail", "safety_backup"):
        if key in data and not isinstance(data.get(key), str):
            return None
    return RecoveryRecord(
        record_id=data["record_id"],
        occurred_at=data["occurred_at"],
        kind=data["kind"],
        target=data.get("target", ""),
        status=data["status"],
        detail=data.get("detail", ""),
        safety_backup=data.get("safety_backup", ""),
    )
