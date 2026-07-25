"""Application-owned starter templates for Orion agents."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml

from orion.agents.models import (
    AgentMetadata,
    ManagedAgentDefinition,
    normalize_agent_id,
    utc_now,
)


class AgentTemplateRegistry:
    """Load immutable starter profiles shipped with Orion."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or Path(__file__).with_name("templates"))

    def all(self) -> tuple[ManagedAgentDefinition, ...]:
        templates = [self._load_path(path) for path in sorted(self.root.glob("*.yaml"))]
        return tuple(sorted(templates, key=lambda item: item.agent_id))

    def load(self, template_id: str) -> ManagedAgentDefinition:
        normalized = normalize_agent_id(template_id)
        path = self.root / f"{normalized}.yaml"
        if not path.is_file():
            raise FileNotFoundError(f"Agent template not found: {normalized}")
        return self._load_path(path)

    def instantiate(
        self,
        template_id: str,
        *,
        scope: str,
        name: str | None = None,
        agent_id: str | None = None,
        timestamp: str | None = None,
    ) -> ManagedAgentDefinition:
        template = self.load(template_id)
        created = timestamp or utc_now()
        identity = normalize_agent_id(agent_id or name or template.agent_id)
        return replace(
            template,
            agent_id=identity,
            name=str(name).strip() if name else template.name,
            scope=scope,
            metadata=AgentMetadata(created, created),
            extensions={},
        )

    @staticmethod
    def _load_path(path: Path) -> ManagedAgentDefinition:
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
            return ManagedAgentDefinition.from_value(value, expected_id=path.stem)
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            raise ValueError(f"Agent template is invalid ({path.name}): {exc}") from exc
