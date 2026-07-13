"""Append-only, project-local action audit history."""
from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any

from orion.actions.models import Action, ActionResult, utc_now


class ActionHistory:
    def __init__(self, workspace_root: str | Path) -> None:
        self._lock = RLock()
        self.bind(workspace_root)

    def bind(self, workspace_root: str | Path) -> None:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {root}")
        self._root = root
        self._path = root / ".orion" / "action-history.jsonl"

    @property
    def path(self) -> Path:
        return self._path

    def record(self, event: str, action: Action, result: ActionResult | None = None, detail: str = "") -> None:
        entry: dict[str, Any] = {
            "timestamp": utc_now(),
            "event": event,
            "action": action.to_dict(),
            "detail": detail,
        }
        if result is not None:
            entry["result"] = result.to_dict()
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def entries(self, limit: int | None = None) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows[-limit:] if limit else rows
