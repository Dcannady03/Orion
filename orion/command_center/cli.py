"""Backward-compatible terminal adapter for Orion Command Center."""
from __future__ import annotations

from typing import Callable

from orion.application.commands.command_center_commands import (
    CommandCenterApplicationHandler,
    synchronize_command_center_team as synchronize_team_state,
)
from orion.application.results import ApplicationResult
from orion.interfaces.cli.renderer import ApplicationResultRenderer


class CommandCenterCommandHandler:
    """Preserve the existing CLI contract over the application handler."""

    def __init__(
        self,
        service,
        *,
        integration=None,
        input_provider: Callable[[str], str] | None = None,
        output_provider: Callable[[str], None] | None = None,
    ) -> None:
        self.application = CommandCenterApplicationHandler(
            service,
            integration=integration,
            input_provider=input_provider,
        )
        self.renderer = ApplicationResultRenderer(output_provider)

    def handle(self, payload: str) -> ApplicationResult:
        result = self.application.handle(payload)
        self.renderer.render(result)
        return result


def dispatch_command_center(orion, raw_command: str) -> bool:
    """Render a Command Center command when the current Orion instance supports it."""
    service = getattr(orion, "command_center", None)
    if service is None:
        return False
    normalized = raw_command.strip().lower()
    if not (
        normalized in {"command-center", "cc"}
        or normalized.startswith(("command-center ", "cc "))
    ):
        return False
    prefix = "cc" if normalized == "cc" or normalized.startswith("cc ") else "command-center"
    CommandCenterCommandHandler(
        service,
        integration=getattr(orion, "command_center_team", None),
    ).handle(raw_command.strip()[len(prefix):].strip())
    return True


def synchronize_command_center_team(
    orion,
    *,
    team_task_id: str = "",
    run_id: str = "",
) -> ApplicationResult:
    """Reconcile linked Team state and render only actionable warnings."""
    result = synchronize_team_state(
        getattr(orion, "command_center_team", None),
        team_task_id=team_task_id,
        run_id=run_id,
    )
    if result.message or result.warnings or result.errors:
        ApplicationResultRenderer().render(result)
    return result
