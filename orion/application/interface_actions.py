"""Explicit interface representations for stable application capabilities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class InterfaceAction:
    """Bind one stable capability to a label and optional CLI representation."""

    capability_id: str
    label: str
    cli_command: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "capability_id": self.capability_id,
            "label": self.label,
            "cli_command": self.cli_command,
        }


_ACTION_LABELS = {
    "team.list": "List AI Team tasks",
    "team.show": "Show AI Team status",
    "team.plan": "Plan an AI Team goal",
    "team.approve": "Approve the AI Team plan",
    "team.implement": "Implement the approved AI Team plan",
    "team.validate": "Run AI Team validation",
    "team.documentation_review": "Run AI Team documentation review",
    "team.rollback": "Rollback the AI Team run",
    "team.sync": "Synchronize linked Command Center state",
    "mission.next": "Preview the next Mission operation",
    "mission.advance": "Advance the Mission by one confirmed operation",
    "mission.show": "Show Mission state",
    "mission.reconcile": "Reconcile Mission projection",
}


def cli_command_for_action(
    capability_id: str,
    context: Mapping[str, object] | None = None,
) -> str | None:
    """Return an explicitly supported CLI representation, never a derived one."""
    capability = str(capability_id).strip()
    values = context or {}
    task_id = str(values.get("team_task_id", "")).strip()
    run_id = str(values.get("run_id", "")).strip()
    approval_id = str(values.get("approval_id", "")).strip()
    mission_id = str(values.get("mission_id", "")).strip()

    if capability == "team.list":
        return "team"
    if capability == "team.show":
        if task_id:
            return f"team status {task_id}"
        if run_id:
            return f"team run {run_id}"
        return None
    if capability == "team.plan":
        return 'team plan "<goal>"'
    if capability == "team.approve":
        return f"team approve {task_id}" if task_id else None
    if capability == "team.implement":
        if task_id and approval_id:
            return f"team implement {task_id} {approval_id}"
        return None
    if capability == "team.validate":
        return f"team test {run_id}" if run_id else None
    if capability == "team.documentation_review":
        return f"team docs {run_id}" if run_id else None
    if capability == "team.rollback":
        return f"team rollback {run_id}" if run_id else None
    if capability == "team.sync":
        return None
    if capability == "mission.next":
        return f"mission next {mission_id}" if mission_id else None
    if capability == "mission.advance":
        return f"mission advance {mission_id}" if mission_id else None
    if capability == "mission.show":
        return f"mission show {mission_id}" if mission_id else None
    if capability == "mission.reconcile":
        return f"mission reconcile {mission_id}" if mission_id else None
    return None


def interface_action(
    capability_id: str,
    context: Mapping[str, object] | None = None,
) -> InterfaceAction:
    """Create one semantic action with its current CLI representation."""
    capability = str(capability_id).strip()
    return InterfaceAction(
        capability_id=capability,
        label=_ACTION_LABELS.get(capability, capability),
        cli_command=cli_command_for_action(capability, context),
    )


def cli_next_actions(actions: tuple[InterfaceAction, ...]) -> tuple[str, ...]:
    """Return only executable CLI representations, preserving action order."""
    return tuple(
        action.cli_command
        for action in actions
        if action.cli_command is not None
    )


def interface_actions_data(
    actions: tuple[InterfaceAction, ...],
) -> dict[str, object]:
    """Serialize semantic actions separately from legacy CLI next-action strings."""
    commands = cli_next_actions(actions)
    return {
        "interface_actions": [action.to_dict() for action in actions],
        "next_actions": list(commands),
    }
