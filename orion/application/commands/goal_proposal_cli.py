"""Thin, interactive CLI adapter for Goal Proposals."""
from __future__ import annotations

import shlex
from typing import Callable, Mapping

from orion.application.goals import GoalRequest
from orion.application.goals.proposals.handler import (
    CreateGoalProposalRequest,
    GoalProposalReferenceRequest,
    ListGoalProposalsRequest,
)
from orion.application.goals.proposals.models import (
    GoalProposalAcceptance,
    GoalProposalRejection,
)
from orion.application.results import ApplicationResult
from orion.interfaces.cli.renderer import ApplicationResultRenderer


class GoalProposalCliAdapter:
    """Parse Goal Proposal commands and keep confirmation at the interface edge."""

    def __init__(
        self,
        runtime,
        *,
        input_provider: Callable[[str], str] | None = None,
        output_provider: Callable[[str], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.application = getattr(runtime, "goal_proposal_application", None)
        self.input = input_provider or input
        self.renderer = ApplicationResultRenderer(output_provider)

    def handle(self, payload: str) -> ApplicationResult:
        if self.application is None:
            return self._render(ApplicationResult.failure(
                "Goal Proposal application service is unavailable.",
                errors=("Goal Proposal application service is unavailable.",),
            ))
        try:
            tokens = shlex.split(str(payload), posix=True)
        except ValueError as exc:
            return self._render(ApplicationResult.failure(
                f"Goal Proposal command could not be read: {exc}",
                errors=(str(exc),),
            ))
        if not tokens:
            return self._usage()
        command = tokens[0].casefold()
        args = tokens[1:]
        try:
            if command == "create":
                result = self.application.create(self._create_request(args))
            elif command == "show":
                result = self.application.show(self._reference(args, "show"))
            elif command == "list":
                result = self.application.list(self._list_request(args))
            elif command == "validate":
                result = self.application.validate(
                    self._reference(args, "validate")
                )
            elif command == "accept":
                return self._accept(args)
            elif command == "reject":
                result = self.application.reject(self._rejection(args))
            else:
                return self._usage(
                    f"Unknown Goal Proposal command: {tokens[0]}"
                )
        except (TypeError, ValueError) as exc:
            return self._render(ApplicationResult.failure(
                f"Goal Proposal command is invalid: {exc}",
                errors=(str(exc),),
            ))
        return self._render(result)

    def _create_request(self, tokens: list[str]) -> CreateGoalProposalRequest:
        values: dict[str, str] = {}
        attachments: list[str] = []
        providers: list[str] = []
        goal_parts: list[str] = []
        allow_ai = False
        value_options = {
            "workspace",
            "department",
            "priority",
            "outcome",
            "attachment",
            "provider",
            "expires-hours",
            "supersedes",
        }
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == "--allow-ai-planning":
                allow_ai = True
                index += 1
                continue
            if token.startswith("--"):
                option = token[2:].casefold()
                if option not in value_options:
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
        expiry = (
            int(values["expires-hours"])
            if "expires-hours" in values
            else None
        )
        return CreateGoalProposalRequest(
            goal_request=GoalRequest(
                goal_text=goal,
                workspace=values.get("workspace", ""),
                department=values.get("department", ""),
                priority=values.get("priority", "normal"),
                requested_outcome=values.get("outcome", ""),
                attachments=tuple(attachments),
                provider_preferences=tuple(providers),
                allow_ai_planning=allow_ai,
            ),
            expiry_hours=expiry,
            supersedes=values.get("supersedes", ""),
        )

    @staticmethod
    def _reference(
        tokens: list[str],
        command: str,
    ) -> GoalProposalReferenceRequest:
        if len(tokens) != 1 or tokens[0].startswith("--"):
            raise ValueError(
                f"Usage: goal proposal {command} <proposal-id>"
            )
        return GoalProposalReferenceRequest(tokens[0])

    @staticmethod
    def _list_request(tokens: list[str]) -> ListGoalProposalsRequest:
        values: dict[str, str] = {}
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token not in {"--status", "--goal", "--limit"}:
                raise ValueError(
                    "Usage: goal proposal list "
                    "[--status <status>] [--goal <goal-id>] [--limit <count>]"
                )
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
                raise ValueError(f"{token} requires a value.")
            values[token[2:]] = tokens[index + 1]
            index += 2
        return ListGoalProposalsRequest(
            status=values.get("status", ""),
            goal_id=values.get("goal", ""),
            limit=int(values.get("limit", "100")),
        )

    @staticmethod
    def _rejection(tokens: list[str]) -> GoalProposalRejection:
        if not tokens or tokens[0].startswith("--"):
            raise ValueError(
                "Usage: goal proposal reject <proposal-id> [--reason <text>]"
            )
        proposal_id = tokens[0]
        reason = ""
        remaining = tokens[1:]
        if remaining:
            if len(remaining) != 2 or remaining[0] != "--reason":
                raise ValueError(
                    "Usage: goal proposal reject <proposal-id> [--reason <text>]"
                )
            reason = remaining[1]
        return GoalProposalRejection(proposal_id, reason=reason)

    def _accept(self, tokens: list[str]) -> ApplicationResult:
        reference = self._reference(tokens, "accept")
        preview = self.application.show(reference)
        self.renderer.render(preview)
        if not preview.ok:
            return preview
        validation = self.application.validate(reference)
        self.renderer.render(validation)
        if not validation.ok:
            return validation
        proposal = preview.data.get("proposal", {})
        if not isinstance(proposal, Mapping):
            return self._render(ApplicationResult.failure(
                "Goal Proposal acceptance preview is invalid.",
                errors=("Proposal preview did not contain structured data.",),
            ))
        proposal_hash = str(proposal.get("plan_hash", ""))
        while True:
            try:
                answer = self.input(
                    "Accept this proposal? [Y/N/D]: "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer in {"d", "details"}:
                self.renderer.render(self.application.show(reference))
                continue
            if answer in {"y", "yes"}:
                return self._render(self.application.accept(
                    GoalProposalAcceptance(
                        proposal_id=reference.proposal_id,
                        proposal_hash=proposal_hash,
                        confirmed=True,
                        accepted_by="user",
                    )
                ))
            if answer in {"", "n", "no"}:
                return self._render(ApplicationResult.success(
                    "Goal Proposal was not accepted. No operation was dispatched.",
                    data={
                        "proposal_id": reference.proposal_id,
                        "status": str(proposal.get("status", "")),
                        "dispatched_capability_count": 0,
                    },
                    next_actions=(
                        f"goal proposal show {reference.proposal_id}",
                    ),
                ))
            self.renderer.output("Please enter Y, N, or D.")

    def _usage(self, detail: str = "") -> ApplicationResult:
        prefix = f"{detail}\n" if detail else ""
        return self._render(ApplicationResult.failure(
            prefix + (
                "Usage: goal proposal "
                "<create|show|list|validate|accept|reject> ..."
            ),
            errors=(detail or "A Goal Proposal subcommand is required.",),
        ))

    def _render(self, result: ApplicationResult) -> ApplicationResult:
        self.renderer.render(result)
        return result
