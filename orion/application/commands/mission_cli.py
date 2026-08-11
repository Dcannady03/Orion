"""Thin CLI adapter for Mission Engine Phase 1."""
from __future__ import annotations

import shlex
from typing import Callable

from orion.application.missions import (
    MissionApplicationHandler,
    MissionHistoryRequest,
    MissionListRequest,
    MissionReferenceRequest,
)
from orion.application.results import ApplicationResult
from orion.interfaces.cli.renderer import ApplicationResultRenderer


class MissionCliAdapter:
    """Parse Mission syntax and delegate all behavior to the application handler."""

    def __init__(
        self,
        runtime,
        *,
        output_provider: Callable[[str], None] | None = None,
    ) -> None:
        application = getattr(runtime, "mission_application", None)
        if application is None:
            raise ValueError("Mission application is not available.")
        self.application: MissionApplicationHandler = application
        self.renderer = ApplicationResultRenderer(output_provider)

    def handle(self, payload: str) -> ApplicationResult:
        try:
            tokens = shlex.split(str(payload), posix=True)
        except ValueError as exc:
            return self._render(ApplicationResult.failure(
                f"Mission command could not be read: {exc}",
                errors=(str(exc),),
            ))
        if not tokens:
            return self._render(self.application.list(MissionListRequest()))
        command = tokens[0].lower()
        args = tokens[1:]
        if command == "create":
            if len(args) != 1:
                return self._usage("Usage: mission create <proposal-id>")
            return self._render(self.application.create(args[0]))
        if command == "show":
            return self._reference(command, args, self.application.show)
        if command == "validate":
            return self._reference(command, args, self.application.validate)
        if command == "reconcile":
            return self._reference(command, args, self.application.reconcile)
        if command == "history":
            try:
                options, positional = self._options(args, {"limit"})
                if len(positional) != 1:
                    raise ValueError("Usage: mission history <mission-id> [--limit <n>]")
                limit = int(options.get("limit", "100"))
            except ValueError as exc:
                return self._usage(str(exc))
            return self._render(self.application.history(MissionHistoryRequest(
                positional[0],
                limit=limit,
            )))
        if command == "list":
            try:
                options, positional = self._options(
                    args,
                    {"status", "goal", "proposal", "limit"},
                )
                if positional:
                    raise ValueError(
                        "Usage: mission list [--status <status>] [--goal <goal-id>] "
                        "[--proposal <proposal-id>] [--limit <n>]"
                    )
                limit = int(options.get("limit", "100"))
            except ValueError as exc:
                return self._usage(str(exc))
            return self._render(self.application.list(MissionListRequest(
                status=options.get("status", ""),
                goal_id=options.get("goal", ""),
                proposal_id=options.get("proposal", ""),
                limit=limit,
            )))
        return self._usage(
            "Mission command not recognized. Use: mission create | show | list | "
            "history | validate | reconcile"
        )

    def _reference(self, command: str, args: list[str], handler) -> ApplicationResult:
        if len(args) != 1:
            return self._usage(f"Usage: mission {command} <mission-id>")
        return self._render(handler(MissionReferenceRequest(args[0])))

    @staticmethod
    def _options(
        args: list[str],
        allowed: set[str],
    ) -> tuple[dict[str, str], list[str]]:
        options: dict[str, str] = {}
        positional: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if not token.startswith("--"):
                positional.append(token)
                index += 1
                continue
            name = token[2:].lower()
            if name not in allowed:
                raise ValueError(f"Unknown Mission option: {token}")
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                raise ValueError(f"Mission option requires a value: {token}")
            options[name] = args[index + 1]
            index += 2
        return options, positional

    def _usage(self, message: str) -> ApplicationResult:
        return self._render(ApplicationResult.failure(message, errors=(message,)))

    def _render(self, result: ApplicationResult) -> ApplicationResult:
        self.renderer.render(result)
        return result


def dispatch_mission(runtime, raw_command: str) -> bool:
    """Recognize only the Mission command family and delegate it."""
    normalized = str(raw_command).strip().lower()
    if normalized != "mission" and not normalized.startswith("mission "):
        return False
    MissionCliAdapter(runtime).handle(
        str(raw_command).strip()[len("mission"):].strip()
    )
    return True
