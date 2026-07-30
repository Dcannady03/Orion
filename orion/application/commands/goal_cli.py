"""Thin CLI adapter for Orion's planning-only Goal Engine."""
from __future__ import annotations

import shlex
from typing import Callable

from orion.application.capabilities import default_capability_registry
from orion.application.goals import (
    GoalApplicationHandler,
    GoalEngine,
    GoalRequest,
)
from orion.application.results import ApplicationResult
from orion.interfaces.cli.renderer import ApplicationResultRenderer


_COMMANDS = frozenset({
    "plan",
    "explain",
    "preview",
    "capabilities",
    "classify",
    "validate",
})
_VALUE_OPTIONS = frozenset({
    "workspace",
    "department",
    "priority",
    "outcome",
    "attachment",
    "provider",
    "execution-mode",
})


class GoalCliAdapter:
    """Parse Goal syntax, call the application boundary, and render its result."""

    def __init__(
        self,
        runtime,
        *,
        output_provider: Callable[[str], None] | None = None,
    ) -> None:
        self.runtime = runtime
        application = getattr(runtime, "goal_application", None)
        if application is None:
            registry = (
                getattr(runtime, "capability_registry", None)
                or default_capability_registry()
            )
            application = GoalApplicationHandler(GoalEngine(
                registry,
                workspace_manager=getattr(runtime, "workspace_manager", None),
                project_context=getattr(runtime, "project_context", None),
                command_center=getattr(runtime, "command_center", None),
            ))
        self.application = application
        self.renderer = ApplicationResultRenderer(output_provider)

    def handle(self, payload: str) -> ApplicationResult:
        try:
            tokens = shlex.split(str(payload), posix=True)
        except ValueError as exc:
            return self._render(ApplicationResult.failure(
                f"Goal command could not be read: {exc}",
                errors=(str(exc),),
            ))
        if not tokens:
            return self._usage()
        command = tokens[0].casefold()
        if command not in _COMMANDS:
            return self._usage(f"Unknown Goal command: {tokens[0]}")
        try:
            request = self._request(tokens[1:])
        except ValueError as exc:
            return self._render(ApplicationResult.failure(
                f"Goal command is invalid: {exc}",
                errors=(str(exc),),
                next_actions=(f'goal {command} "<goal>"',),
            ))
        result = getattr(self.application, command)(request)
        return self._render(result)

    def _request(self, tokens: list[str]) -> GoalRequest:
        values: dict[str, str] = {}
        attachments: list[str] = []
        providers: list[str] = []
        goal_parts: list[str] = []
        allow_ai_planning = False
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == "--allow-ai-planning":
                allow_ai_planning = True
                index += 1
                continue
            if token.startswith("--"):
                option = token[2:].casefold()
                if option not in _VALUE_OPTIONS:
                    raise ValueError(f"unknown option {token}.")
                if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
                    raise ValueError(f"{token} requires a value.")
                value = tokens[index + 1]
                if option == "attachment":
                    attachments.append(value)
                elif option == "provider":
                    providers.append(value)
                else:
                    if option in values:
                        raise ValueError(f"{token} may only be supplied once.")
                    values[option] = value
                index += 2
                continue
            goal_parts.append(token)
            index += 1
        goal = " ".join(goal_parts).strip()
        if not goal:
            raise ValueError("goal text is required.")
        return GoalRequest(
            goal_text=goal,
            workspace=values.get("workspace", ""),
            department=values.get("department", ""),
            priority=values.get("priority", "normal"),
            requested_outcome=values.get("outcome", ""),
            attachments=tuple(attachments),
            provider_preferences=tuple(providers),
            execution_mode=values.get("execution-mode", "plan"),
            allow_ai_planning=allow_ai_planning,
        )

    def _usage(self, detail: str = "") -> ApplicationResult:
        message = (
            f"{detail}\n" if detail else ""
        ) + (
            'Usage: goal <plan|explain|preview|capabilities|classify|validate> '
            '"<goal>" [--workspace <path>] [--department <name>] '
            "[--priority <level>]"
        )
        return self._render(ApplicationResult.failure(
            message,
            errors=(detail or "A Goal subcommand and goal text are required.",),
        ))

    def _render(self, result: ApplicationResult) -> ApplicationResult:
        self.renderer.render(result)
        return result


def dispatch_goal(runtime, raw_command: str) -> bool:
    """Recognize the Goal command family and delegate to its CLI adapter."""
    normalized = str(raw_command).strip().casefold()
    if normalized != "goal" and not normalized.startswith("goal "):
        return False
    payload = str(raw_command).strip()[len("goal"):].strip()
    if normalized == "goal proposal" or normalized.startswith("goal proposal "):
        from orion.application.commands.goal_proposal_cli import (
            GoalProposalCliAdapter,
        )
        GoalProposalCliAdapter(runtime).handle(
            payload[len("proposal"):].strip()
        )
        return True
    GoalCliAdapter(runtime).handle(
        payload
    )
    return True
