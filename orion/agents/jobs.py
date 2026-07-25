"""Workspace-local draft state for incremental agent team selection."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import yaml

from orion.agents.models import normalize_agent_id, utc_now


@dataclass(frozen=True)
class AgentTeamDraft:
    schema_version: int
    goal: str
    selected_agents: tuple[str, ...]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "goal": self.goal,
            "selected_agents": list(self.selected_agents),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_value(cls, value) -> "AgentTeamDraft":
        if not isinstance(value, dict):
            raise ValueError("Agent team draft must be a YAML mapping.")
        fields = {
            "schema_version", "goal", "selected_agents", "created_at", "updated_at"
        }
        missing = sorted(fields - set(value))
        unknown = sorted(set(value) - fields)
        if missing or unknown:
            raise ValueError(
                f"Agent team draft fields are invalid (missing={missing}, unknown={unknown})."
            )
        if value["schema_version"] != 1:
            raise ValueError("Unsupported agent team draft schema version.")
        goal = str(value["goal"]).strip()
        if not goal or len(goal) > 4_000:
            raise ValueError("Agent team draft goal must be 1-4,000 characters.")
        if not isinstance(value["selected_agents"], list):
            raise ValueError("Agent team draft selected_agents must be a list.")
        selected = tuple(normalize_agent_id(item) for item in value["selected_agents"])
        if len(selected) > 20 or len(set(selected)) != len(selected):
            raise ValueError("Agent team draft selections are invalid or duplicated.")
        for field_name in ("created_at", "updated_at"):
            if not isinstance(value[field_name], str) or not value[field_name].strip():
                raise ValueError(f"Agent team draft {field_name} is invalid.")
        return cls(
            1,
            goal,
            selected,
            value["created_at"],
            value["updated_at"],
        )


class WorkspaceTeamDraftStore:
    """Persist one current team draft in the active workspace metadata."""

    def __init__(self, workspace_manager) -> None:
        self.workspace_manager = workspace_manager

    @property
    def path(self) -> Path:
        root = Path(self.workspace_manager.root).resolve()
        path = root / ".orion" / "team-draft.yaml"
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise PermissionError("Agent team draft storage escapes the workspace.") from exc
        return path

    def create(self, goal: str) -> AgentTeamDraft:
        timestamp = utc_now()
        draft = AgentTeamDraft(1, str(goal).strip(), (), timestamp, timestamp)
        AgentTeamDraft.from_value(draft.to_dict())
        self.save(draft)
        return draft

    def add(self, agent_id: str) -> AgentTeamDraft:
        draft = self.load()
        identity = normalize_agent_id(agent_id)
        if identity in draft.selected_agents:
            raise ValueError(f"Agent is already selected: {identity}")
        updated = replace(
            draft,
            selected_agents=(*draft.selected_agents, identity),
            updated_at=utc_now(),
        )
        self.save(updated)
        return updated

    def load(self) -> AgentTeamDraft:
        path = self.path
        if not path.is_file():
            raise FileNotFoundError(
                'No agent team draft exists. Use team create "<goal>" first.'
            )
        if path.is_symlink():
            raise PermissionError("Agent team draft symlinks are not allowed.")
        try:
            return AgentTeamDraft.from_value(
                yaml.safe_load(path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            raise ValueError(f"Agent team draft is invalid: {exc}") from exc

    def save(self, draft: AgentTeamDraft) -> Path:
        payload = draft.to_dict()
        AgentTeamDraft.from_value(payload)
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass
        return path

    def clear(self) -> None:
        path = self.path
        if path.is_file() and not path.is_symlink():
            path.unlink()
