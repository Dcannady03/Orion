"""Serializable metadata describing Orion application capabilities."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Mapping

from orion.application.results import _freeze_json, _thaw_json


_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


@dataclass(frozen=True)
class CapabilityDefinition:
    """Metadata only; capability definitions never execute operations."""

    capability_id: str
    description: str
    mutates_state: bool
    requires_approval: bool
    required_permissions: tuple[str, ...] = ()
    input_schema: Mapping[str, object] = field(default_factory=dict)
    output_schema: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        capability_id = str(self.capability_id).strip()
        if not _CAPABILITY_ID.fullmatch(capability_id):
            raise ValueError(
                f"Invalid capability ID {capability_id!r}; use stable lowercase segments."
            )
        object.__setattr__(self, "capability_id", capability_id)
        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(
            self,
            "required_permissions",
            tuple(str(item) for item in self.required_permissions),
        )
        object.__setattr__(
            self,
            "input_schema",
            _freeze_json(dict(self.input_schema)),
        )
        object.__setattr__(
            self,
            "output_schema",
            _freeze_json(dict(self.output_schema)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "capability_id": self.capability_id,
            "description": self.description,
            "mutates_state": self.mutates_state,
            "requires_approval": self.requires_approval,
            "required_permissions": list(self.required_permissions),
            "input_schema": _thaw_json(self.input_schema),
            "output_schema": _thaw_json(self.output_schema),
        }


class CapabilityRegistry:
    """Deterministic registry of application capability definitions."""

    def __init__(
        self,
        definitions: tuple[CapabilityDefinition, ...] = (),
    ) -> None:
        self._definitions: dict[str, CapabilityDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: CapabilityDefinition) -> None:
        if not isinstance(definition, CapabilityDefinition):
            raise TypeError("Only CapabilityDefinition instances may be registered.")
        if definition.capability_id in self._definitions:
            raise ValueError(
                f"Capability {definition.capability_id!r} is already registered."
            )
        self._definitions[definition.capability_id] = definition

    def lookup(self, capability_id: str) -> CapabilityDefinition:
        try:
            return self._definitions[str(capability_id)]
        except KeyError as exc:
            raise KeyError(f"Unknown Orion capability: {capability_id}") from exc

    def list(self) -> tuple[CapabilityDefinition, ...]:
        return tuple(
            self._definitions[key]
            for key in sorted(self._definitions, key=str.casefold)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "capabilities": [
                definition.to_dict()
                for definition in self.list()
            ]
        }


def _definition(
    capability_id: str,
    description: str,
    *,
    mutates: bool = False,
    approval: bool = False,
    permissions: tuple[str, ...] = (),
    required: tuple[str, ...] = (),
) -> CapabilityDefinition:
    properties = {name: {"type": "string"} for name in required}
    return CapabilityDefinition(
        capability_id=capability_id,
        description=description,
        mutates_state=mutates,
        requires_approval=approval,
        required_permissions=permissions,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": True,
        },
        output_schema={"type": "object"},
    )


def default_capability_registry() -> CapabilityRegistry:
    """Build Orion's initial, intentionally representative capability catalog."""
    command_center = (
        _definition(
            "command_center.job.create",
            "Create an inert Command Center job.",
            mutates=True,
            permissions=("command_center.write",),
            required=("title", "goal"),
        ),
        _definition(
            "command_center.job.preview",
            "Preview a job launch without changing state or calling a provider.",
            permissions=("command_center.read", "workspace.inspect"),
            required=("job_id",),
        ),
        _definition(
            "command_center.job.launch",
            "Launch a Command Center job into AI Team planning.",
            mutates=True,
            permissions=("command_center.write", "team.plan", "workspace.inspect"),
            required=("job_id",),
        ),
        _definition(
            "command_center.job.sync",
            "Reconcile a job with authoritative Team and Codex records.",
            mutates=True,
            permissions=("command_center.write", "team.read"),
            required=("job_id",),
        ),
        _definition(
            "command_center.job.show",
            "Inspect a Command Center job and its linked workflow state.",
            permissions=("command_center.read",),
            required=("job_id",),
        ),
        _definition(
            "command_center.job.cancel",
            "Cancel a Command Center job.",
            mutates=True,
            permissions=("command_center.write",),
            required=("job_id",),
        ),
    )
    representative = (
        _definition("agent.create", "Create an Orion agent.", mutates=True),
        _definition("agent.list", "List configured Orion agents."),
        _definition("team.plan", "Create an AI Team plan.", mutates=True),
        _definition(
            "team.approve",
            "Approve an AI Team plan.",
            mutates=True,
            approval=True,
        ),
        _definition(
            "team.implement",
            "Implement an approved AI Team plan.",
            mutates=True,
            approval=True,
            permissions=("workspace.write",),
        ),
        _definition("workspace.inspect", "Inspect the active workspace."),
        _definition(
            "image.generate",
            "Generate an image through a configured provider.",
            mutates=True,
        ),
        _definition("email.search", "Search connected email accounts."),
        _definition("calendar.today", "Show today's calendar."),
        _definition(
            "application.open",
            "Open a locally discovered application.",
            mutates=True,
            approval=True,
            permissions=("application.launch",),
        ),
    )
    return CapabilityRegistry(command_center + representative)
