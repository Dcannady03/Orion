"""Backward-compatible CLI adapter for Orion AI Team."""
from __future__ import annotations

import shlex
from typing import Callable, Mapping

from orion.application.commands.ai_team_commands import (
    AiTeamApplicationHandler,
    TeamApprovalRequest,
    TeamImplementationRequest,
    TeamPlanRequest,
    TeamRoleAssignmentRequest,
    TeamRollbackRequest,
    TeamRunRequest,
    TeamTaskRequest,
)
from orion.application.results import ApplicationResult
from orion.interfaces.cli.renderer import ApplicationResultRenderer


class AiTeamCliAdapter:
    """Parse legacy Team syntax and keep all interactive input at the CLI edge."""

    def __init__(
        self,
        runtime,
        *,
        interactive_approval: bool = False,
        approval_input: Callable[[str], str] | None = None,
        input_provider: Callable[[str], str] | None = None,
        output_provider: Callable[[str], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.application = (
            getattr(runtime, "team_application", None)
            or AiTeamApplicationHandler(runtime)
        )
        self.interactive_approval = bool(interactive_approval)
        self.approval_input = approval_input
        self.input = input_provider or input
        self.renderer = ApplicationResultRenderer(output_provider)

    def handle(self, payload: str) -> ApplicationResult:
        value = str(payload).strip()
        lowered = value.lower()
        if not value:
            return self._render(self.application.list())
        if lowered == "roles":
            return self._render(self.application.roles())
        if lowered == "role show":
            return self._usage("Usage: team role show <role>")
        if lowered.startswith("role show "):
            return self._render(self.application.show_role(value[len("role show "):]))
        if lowered == "role set":
            return self._usage("Usage: team role set <role> <provider:model|engine>")
        if lowered.startswith("role set "):
            rest = value[len("role set "):].strip().split(maxsplit=1)
            if len(rest) != 2:
                return self._usage(
                    "Usage: team role set <role> <provider:model|engine>"
                )
            return self._render(self.application.set_role(
                TeamRoleAssignmentRequest(rest[0], rest[1])
            ))
        if lowered == "role reset":
            return self._usage("Usage: team role reset <role>")
        if lowered.startswith("role reset "):
            return self._render(
                self.application.reset_role(value[len("role reset "):])
            )
        if lowered == "create":
            return self._usage('Usage: team create "<goal>"')
        if lowered.startswith("create "):
            try:
                goal = self._unquote(value[len("create "):])
            except ValueError as exc:
                return self._render(ApplicationResult.failure(
                    f"Could not read team goal: {exc}",
                    errors=(str(exc),),
                ))
            return self._render(self.application.create_agent_draft(goal))
        if lowered == "agents add":
            return self._usage("Usage: team agents add <agent>")
        if lowered.startswith("agents add "):
            return self._render(
                self.application.add_agent_to_draft(value[len("agents add "):])
            )
        if lowered == "plan":
            return self._usage('Usage: team plan [--manual] "<goal>"')
        if lowered.startswith("plan "):
            return self._plan(value[len("plan "):])
        if lowered == "status":
            return self._usage("Usage: team status <task-id>")
        if lowered.startswith("status "):
            return self._render(self.application.show_task(
                TeamTaskRequest(value[len("status "):].strip())
            ))
        if lowered == "approve":
            return self._usage("Usage: team approve <team-task-id>")
        if lowered.startswith("approve "):
            return self._render(self.application.approve(
                TeamApprovalRequest(value[len("approve "):].strip())
            ))
        if lowered == "implement":
            return self._usage(
                "Usage: team implement <team-task-id> <approval-id>"
            )
        if lowered.startswith("implement "):
            parts = value[len("implement "):].split()
            if len(parts) != 2:
                return self._usage(
                    "Usage: team implement <team-task-id> <approval-id>"
                )
            return self._render(self.application.implement(
                TeamImplementationRequest(parts[0], parts[1])
            ))
        if lowered == "run":
            return self._agent_run("")
        if lowered.startswith("run "):
            rest = value[len("run "):].strip()
            agents = getattr(self.runtime, "agents", None)
            if (
                getattr(agents, "is_production_agent_manager", False)
                and (rest[:1] in {'"', "'"} or "--agents" in rest)
            ):
                return self._agent_run(rest)
            return self._render(
                self.application.show_run(TeamRunRequest(rest))
            )
        if lowered == "test":
            return self._usage("Usage: team test <run-id|last>")
        if lowered.startswith("test "):
            return self._render(self.application.validate(
                TeamRunRequest(value[len("test "):].strip())
            ))
        if lowered == "docs":
            return self._usage("Usage: team docs <run-id|last>")
        if lowered == "docs show":
            return self._usage("Usage: team docs show <run-id>")
        if lowered.startswith("docs show "):
            return self._render(self.application.documentation_status(
                TeamRunRequest(value[len("docs show "):].strip())
            ))
        if lowered.startswith("docs "):
            return self._render(self.application.documentation_review(
                TeamRunRequest(value[len("docs "):].strip())
            ))
        if lowered == "rollback":
            return self._usage("Usage: team rollback <run-id>")
        if lowered.startswith("rollback "):
            return self.rollback(value[len("rollback "):].strip())
        return self._usage(
            "AI Team command not recognized. Use: team | team plan | "
            "team status | team approve | team implement | team run | "
            "team test | team docs | team rollback"
        )

    def rollback(self, run_id: str) -> ApplicationResult:
        preview = self.application.rollback_preview(TeamRunRequest(run_id))
        self.renderer.render(preview)
        if not preview.ok:
            return preview
        if self.input("Approve rollback? [y/N]: ").strip().lower() not in {"y", "yes"}:
            return self._render(ApplicationResult.success(
                "Team rollback cancelled.",
                data=preview.data,
                next_actions=(f"team.show {run_id}",),
            ))
        return self._render(self.application.rollback(
            TeamRollbackRequest(run_id, confirmed=True)
        ))

    def _plan(self, payload: str) -> ApplicationResult:
        goal = payload.strip()
        manual = False
        if goal.lower() == "--manual":
            manual = True
            goal = ""
        elif goal.lower().startswith("--manual "):
            manual = True
            goal = goal[len("--manual "):].strip()
        try:
            goal = self._unquote(goal)
        except ValueError as exc:
            return self._render(ApplicationResult.failure(
                f"Could not read team goal: {exc}",
                errors=(str(exc),),
            ))
        if not goal:
            return self._usage('Usage: team plan [--manual] "<goal>"')
        self.renderer.render(ApplicationResult.success(
            "AI Team is preparing an Architect and Engineering Reviewer plan..."
        ))
        result = self.application.plan(TeamPlanRequest(goal))
        self.renderer.render(result)
        if (
            result.ok
            and result.data.get("status") == "awaiting_approval"
            and self.interactive_approval
            and not manual
        ):
            return self._prompt_approval(
                str(result.data.get("team_task_id", ""))
            ) or result
        return result

    def _prompt_approval(self, task_id: str) -> ApplicationResult | None:
        while True:
            self.renderer.output("\nApprove this exact plan?")
            self.renderer.output("[Y] Yes  [N] No  [D] Details")
            try:
                reader = self.approval_input or self.input
                answer = reader("> ").strip().lower()
            except KeyboardInterrupt:
                return self._render(ApplicationResult.success(
                    "\nApproval cancelled. The plan remains Awaiting Approval.",
                    data={"team_task_id": task_id, "status": "awaiting_approval"},
                    next_actions=(f"team.approve {task_id}",),
                ))
            if not answer:
                return self._render(ApplicationResult.success(
                    "No approval recorded. The plan remains Awaiting Approval.",
                    data={"team_task_id": task_id, "status": "awaiting_approval"},
                    next_actions=(f"team.approve {task_id}",),
                ))
            if answer in {"n", "no"}:
                return self._render(ApplicationResult.success(
                    "Plan not approved. No implementation was performed.",
                    data={"team_task_id": task_id, "status": "awaiting_approval"},
                    next_actions=(f"team.approve {task_id}",),
                ))
            if answer in {"d", "details"}:
                self.renderer.render(self.application.approval_details(
                    TeamTaskRequest(task_id)
                ))
                continue
            if answer in {"y", "yes"}:
                approval = self.application.approve(
                    TeamApprovalRequest(task_id)
                )
                self.renderer.render(approval)
                if not approval.ok:
                    return approval
                approval_id = str(approval.data.get("approval_id", ""))
                implementation = self.application.implement(
                    TeamImplementationRequest(task_id, approval_id)
                )
                self.renderer.render(implementation)
                return implementation
            self.renderer.output(
                "Please enter Y, N, or D. No approval has been recorded."
            )

    def _agent_run(self, payload: str) -> ApplicationResult:
        goal = ""
        selected: list[str] = []
        provider = "auto"
        model = "auto"
        clear_draft = False
        if payload.strip():
            try:
                tokens = shlex.split(payload, posix=True)
            except ValueError as exc:
                return self._render(ApplicationResult.failure(
                    f"Could not read agent team job: {exc}",
                    errors=(str(exc),),
                ))
            positional: list[str] = []
            index = 0
            while index < len(tokens):
                token = tokens[index]
                if token in {"--agents", "--provider", "--model"}:
                    if index + 1 >= len(tokens):
                        return self._usage(f"Option {token} requires a value.")
                    item = tokens[index + 1]
                    if token == "--agents":
                        selected = [
                            value.strip()
                            for value in item.split(",")
                            if value.strip()
                        ]
                    elif token == "--provider":
                        provider = item
                    else:
                        model = item
                    index += 2
                    continue
                if token.startswith("--"):
                    return self._usage(f"Unknown team run option: {token}")
                positional.append(token)
                index += 1
            goal = " ".join(positional).strip()
        else:
            draft_result = self.application.agent_draft()
            if not draft_result.ok:
                return self._render(draft_result)
            draft = self._plain(draft_result.data.get("draft", {}))
            goal = str(draft.get("goal", ""))
            selected = [str(item) for item in draft.get("selected_agents", ())]
            clear_draft = True
        if not goal:
            return self._usage(
                'Usage: team run "<goal>" [--agents agent-1,agent-2]'
            )
        if not selected:
            available_result = self.application.enabled_agents()
            if not available_result.ok:
                return self._render(available_result)
            available = list(
                self._plain(available_result.data.get("agents", ()))
            )
            if not available:
                return self._render(ApplicationResult.failure(
                    (
                        "No enabled agents are available. Create one with "
                        "agent create or agent create --from-template <template>."
                    ),
                    errors=("No enabled agents are available.",),
                ))
            self.renderer.output("Choose agents in execution order:")
            for number, agent in enumerate(available, 1):
                role = agent.get("role", {})
                job = role.get("job", "") if isinstance(role, dict) else ""
                self.renderer.output(
                    f"  [{number}] {agent.get('name', '')} "
                    f"({agent.get('agent_id', '')}) - {job}"
                )
            answer = self.input(
                "Selected numbers or IDs, comma-separated: "
            ).strip()
            for item in (part.strip() for part in answer.split(",")):
                if not item:
                    continue
                if item.isdigit() and 1 <= int(item) <= len(available):
                    selected.append(str(available[int(item) - 1].get("agent_id", "")))
                else:
                    selected.append(item)
        if not selected:
            return self._render(ApplicationResult.failure(
                "No agents were selected; the job was not started.",
                errors=("At least one enabled agent must be selected.",),
            ))
        self.renderer.output("AI Team agent order: " + " -> ".join(selected))
        return self._render(self.application.run_agent_team(
            TeamPlanRequest(
                goal,
                selected_agents=tuple(selected),
                provider=provider,
                model=model,
            ),
            clear_draft=clear_draft,
        ))

    def _usage(self, message: str) -> ApplicationResult:
        return self._render(ApplicationResult.failure(message, errors=(message,)))

    def _render(self, result: ApplicationResult) -> ApplicationResult:
        self.renderer.render(result)
        return result

    @staticmethod
    def _unquote(payload: str) -> str:
        goal = str(payload).strip()
        if goal[:1] in {'"', "'"}:
            if len(goal) < 2 or goal[-1] != goal[0]:
                raise ValueError("closing quote is missing.")
            goal = goal[1:-1].strip()
        return goal

    @classmethod
    def _plain(cls, value):
        if isinstance(value, Mapping):
            return {str(key): cls._plain(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [cls._plain(item) for item in value]
        return value


def dispatch_ai_team(
    runtime,
    raw_command: str,
    *,
    interactive_approval: bool = False,
    approval_input: Callable[[str], str] | None = None,
) -> bool:
    """Recognize one Team command family and delegate it to the CLI adapter."""
    normalized = str(raw_command).strip().lower()
    if normalized != "team" and not normalized.startswith("team "):
        return False
    AiTeamCliAdapter(
        runtime,
        interactive_approval=interactive_approval,
        approval_input=approval_input,
    ).handle(str(raw_command).strip()[len("team"):].strip())
    return True
