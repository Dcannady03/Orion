"""Atomic scoped persistence for Orion agent definitions."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from uuid import uuid4

import yaml

from orion.agents.models import ManagedAgentDefinition, normalize_agent_id


MAX_AGENT_FILE_BYTES = 256_000


class AgentRepository:
    """Store one scope of YAML agent definitions beneath a confined root."""

    def __init__(
        self,
        root: str | Path,
        scope: str,
        *,
        workspace_boundary: str | Path | None = None,
    ) -> None:
        if scope not in {"permanent", "workspace"}:
            raise ValueError("Agent repository scope must be permanent or workspace.")
        self.root = Path(root)
        self.scope = scope
        self.workspace_boundary = (
            Path(workspace_boundary).resolve() if workspace_boundary is not None else None
        )
        self._validate_root()

    def save(
        self,
        agent: ManagedAgentDefinition,
        *,
        overwrite: bool = False,
    ) -> Path:
        if agent.scope != self.scope:
            raise ValueError(
                f"Cannot save a {agent.scope} agent in the {self.scope} repository."
            )
        path = self._path(agent.agent_id)
        if path.exists() and not overwrite:
            raise FileExistsError(f"Agent already exists: {agent.agent_id}")
        payload = agent.to_dict()
        ManagedAgentDefinition.from_value(
            payload,
            expected_id=agent.agent_id,
            expected_scope=self.scope,
        )
        self._ensure_root()
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                yaml.safe_dump(
                    payload,
                    handle,
                    sort_keys=False,
                    allow_unicode=True,
                    default_flow_style=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            os.replace(temporary, path)
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass
        return path

    def load(self, agent_id: str) -> ManagedAgentDefinition:
        normalized = normalize_agent_id(agent_id)
        path = self._path(normalized)
        if not path.is_file():
            raise FileNotFoundError(f"Agent not found: {normalized}")
        self._validate_existing_path(path)
        if path.stat().st_size > MAX_AGENT_FILE_BYTES:
            raise ValueError(f"Agent definition is too large: {normalized}")
        try:
            raw = path.read_text(encoding="utf-8")
            value = yaml.safe_load(raw)
            timestamp = path.stat().st_mtime
            from datetime import datetime, timezone
            legacy_timestamp = datetime.fromtimestamp(
                timestamp, tz=timezone.utc
            ).isoformat()
            return ManagedAgentDefinition.from_value(
                value,
                expected_id=normalized,
                expected_scope=self.scope,
                legacy_timestamp=legacy_timestamp,
            )
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            raise ValueError(
                f"Agent definition is invalid ({normalized}): {detail}"
            ) from exc

    def all(self) -> tuple[ManagedAgentDefinition, ...]:
        if not self.root.exists():
            return ()
        self._validate_root()
        agents = [self.load(path.stem) for path in sorted(self.root.glob("*.yaml"))]
        return tuple(sorted(agents, key=lambda item: item.agent_id))

    def delete(self, agent_id: str) -> Path:
        path = self._path(agent_id)
        if not path.is_file():
            raise FileNotFoundError(f"Agent not found: {normalize_agent_id(agent_id)}")
        self._validate_existing_path(path)
        path.unlink()
        return path

    def exists(self, agent_id: str) -> bool:
        return self._path(agent_id).is_file()

    def _ensure_root(self) -> None:
        self._validate_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self._validate_root()

    def _validate_root(self) -> None:
        if self.workspace_boundary is None:
            return
        resolved = self.root.resolve()
        try:
            resolved.relative_to(self.workspace_boundary)
        except ValueError as exc:
            raise PermissionError(
                "Workspace agent storage escapes the active workspace."
            ) from exc

    def _validate_existing_path(self, path: Path) -> None:
        if path.is_symlink():
            raise PermissionError("Agent definition symlinks are not allowed.")
        if self.workspace_boundary is not None:
            try:
                path.resolve().relative_to(self.workspace_boundary)
            except ValueError as exc:
                raise PermissionError(
                    "Workspace agent definition escapes the active workspace."
                ) from exc

    def _path(self, agent_id: str) -> Path:
        normalized = normalize_agent_id(agent_id)
        self._validate_root()
        return self.root / f"{normalized}.yaml"
