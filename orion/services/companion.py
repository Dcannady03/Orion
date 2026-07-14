"""Friendly-mode settings and persistent action trust for Orion Companion."""
from __future__ import annotations

import json
from pathlib import Path


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


class CompanionSettings:
    """Workspace-local presentation settings for Orion's CLI."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.developer_mode = False
        self.bind(workspace_root)

    def bind(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.path = self.workspace_root / ".orion" / "companion-settings.json"
        self.load()

    def load(self) -> None:
        self.developer_mode = False
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.developer_mode = bool(data.get("developer_mode", False))

    def set_developer_mode(self, enabled: bool) -> None:
        self.developer_mode = bool(enabled)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"developer_mode": self.developer_mode}, indent=2),
            encoding="utf-8",
        )


class ActionTrustStore:
    """Persists narrowly scoped, user-approved trust decisions."""

    def __init__(self, workspace_root: str | Path) -> None:
        self._trusted: dict[str, set[str]] = {}
        self.bind(workspace_root)

    def bind(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.path = self.workspace_root / ".orion" / "trusted-actions.json"
        self.load()

    def load(self) -> None:
        self._trusted = {}
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for action_type, targets in data.get("trusted", {}).items():
            self._trusted[_normalize(action_type)] = {_normalize(str(item)) for item in targets}

    def trust(self, action_type: str, target: str) -> None:
        action_key = _normalize(action_type)
        target_key = _normalize(target)
        if not action_key or not target_key:
            raise ValueError("Action type and trust target cannot be empty.")
        self._trusted.setdefault(action_key, set()).add(target_key)
        self._save()

    def revoke(self, action_type: str, target: str) -> bool:
        targets = self._trusted.get(_normalize(action_type), set())
        key = _normalize(target)
        if key not in targets:
            return False
        targets.remove(key)
        if not targets:
            self._trusted.pop(_normalize(action_type), None)
        self._save()
        return True

    def is_trusted(self, action_type: str, target: str) -> bool:
        return _normalize(target) in self._trusted.get(_normalize(action_type), set())

    def entries(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (action_type, target)
            for action_type in sorted(self._trusted)
            for target in sorted(self._trusted[action_type])
        )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "trusted": {
                action_type: sorted(targets)
                for action_type, targets in sorted(self._trusted.items())
            }
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
