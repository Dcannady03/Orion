"""Application-core boundary for Orion AI Team lifecycle operations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from orion.application.results import ApplicationResult
from orion.application.team_reconciliation import synchronize_command_center_team
from orion.services.codex_bridge import (
    CodexBridgeError,
    PlanSnapshot,
)
from orion.services.execution_engines import ExecutionEngineUnavailable
from orion.services.team import TeamPlanningError
from orion.services.workspace_snapshot import (
    WorkspaceRollbackError,
    WorkspaceSnapshotError,
)


@dataclass(frozen=True)
class TeamPlanRequest:
    """Structured request for a bounded planning operation."""

    goal: str
    selected_agents: tuple[str, ...] = ()
    provider: str = "auto"
    model: str = "auto"
    task_id: str | None = None


@dataclass(frozen=True)
class TeamTaskRequest:
    """Structured request for one persisted Team task."""

    team_task_id: str


@dataclass(frozen=True)
class TeamApprovalRequest:
    """Structured request to approve one immutable persisted plan."""

    team_task_id: str
    actor: str = "user"
    plan_sha256: str | None = None


@dataclass(frozen=True)
class TeamImplementationRequest:
    """Structured request for one approval-bound implementation."""

    team_task_id: str
    approval_id: str


@dataclass(frozen=True)
class TeamRunRequest:
    """Structured request for one persisted implementation run."""

    run_id: str


@dataclass(frozen=True)
class TeamRollbackRequest:
    """Structured request for an explicitly confirmed rollback."""

    run_id: str
    confirmed: bool = False


@dataclass(frozen=True)
class TeamRoleAssignmentRequest:
    """Structured request to set one workflow role assignment."""

    role: str
    assignment: str


@dataclass(frozen=True)
class TeamSyncRequest:
    """Structured request to reconcile linked Command Center state."""

    team_task_id: str = ""
    run_id: str = ""


def team_task_next_actions(task: Any) -> tuple[str, ...]:
    """Return capability-oriented next actions for an authoritative Team task."""
    task_id = str(getattr(task, "task_id", "")).strip()
    status = str(getattr(task, "status", "")).strip().lower()
    show = f"team.show {task_id}" if task_id else "team.show"
    if status == "planning":
        return (show,)
    if status == "awaiting_approval":
        return (f"team.approve {task_id}", show)
    return (show,) if task_id else ()


def team_run_stage(run: Any) -> str:
    """Project persisted run state into the existing review sub-stages."""
    status = str(getattr(run, "status", "")).strip().lower()
    if status != "awaiting_review":
        return status
    if getattr(run, "validation", None) is None:
        return "validation"
    if getattr(run, "documentation", None) is None:
        return "documentation_review"
    return "final_review"


def team_run_next_actions(run: Any) -> tuple[str, ...]:
    """Return only actions that are valid for the current persisted run."""
    run_id = str(getattr(run, "run_id", "")).strip()
    if not run_id:
        return ()
    status = str(getattr(run, "status", "")).strip().lower()
    show = f"team.show {run_id}"
    if status == "executing":
        return (show,)
    if status == "rolled_back":
        return (show,)
    if status == "failed":
        if getattr(run, "changes", None) is not None:
            return (show, f"team.rollback {run_id}")
        return (show,)
    if status != "awaiting_review":
        return (show,)

    actions: list[str] = [show]
    validation = getattr(run, "validation", None)
    documentation = getattr(run, "documentation", None)
    if validation is None:
        actions.append(f"team.validate {run_id}")
    if documentation is None:
        actions.append(f"team.documentation_review {run_id}")
    actions.append(f"team.rollback {run_id}")
    return tuple(actions)


def team_task_lifecycle_data(task: Any) -> dict[str, object]:
    """Serialize one Team task without exposing live service objects."""
    raw_selected = getattr(task, "selected_agents", ())
    selected_agents = (
        list(raw_selected) if isinstance(raw_selected, (list, tuple)) else []
    )
    raw_snapshots = getattr(task, "agent_snapshots", ())
    snapshots = (
        list(raw_snapshots) if isinstance(raw_snapshots, (list, tuple)) else []
    )
    routes = []
    for snapshot in snapshots:
        routes.append({
            "agent_id": str(getattr(snapshot, "agent_id", "")),
            "provider": str(getattr(snapshot, "actual_provider", "")),
            "model": str(getattr(snapshot, "actual_model", "")),
        })
    risks: list[str] = []
    raw_artifacts = getattr(task, "artifacts", ())
    artifacts = raw_artifacts if isinstance(raw_artifacts, (list, tuple)) else ()
    for artifact in artifacts:
        for risk in getattr(getattr(artifact, "output", None), "risks", ()) or ():
            if risk not in risks:
                risks.append(str(risk))
    status = str(getattr(task, "status", "")).strip()
    actions = team_task_next_actions(task)
    data: dict[str, object] = {
        "team_task_id": str(getattr(task, "task_id", "")),
        "status": status,
        "stage": status,
        "goal": str(getattr(task, "goal", "")),
        "approval_required": status == "awaiting_approval",
        "approval_status": (
            "pending" if status == "awaiting_approval" else "not_required"
        ),
        "resolved_agents": selected_agents,
        "provider_routes": routes,
        "risks": risks,
        "next_actions": list(actions),
        "created_at": str(getattr(task, "created_at", "")),
        "updated_at": str(getattr(task, "updated_at", "")),
    }
    if hasattr(task, "to_dict") and callable(task.to_dict):
        serialized = task.to_dict()
        if isinstance(serialized, Mapping):
            data["task"] = serialized
    error = str(getattr(task, "error", "")).strip()
    if error:
        data["error"] = error
    return data


def team_run_lifecycle_data(run: Any) -> dict[str, object]:
    """Serialize one implementation lifecycle without fabricating absent state."""
    status = str(getattr(run, "status", "")).strip()
    validation = getattr(run, "validation", None)
    documentation = getattr(run, "documentation", None)
    result = getattr(run, "result", None)
    changes = getattr(run, "changes", None)
    workspace = getattr(run, "workspace", None)
    actions = team_run_next_actions(run)
    data: dict[str, object] = {
        "run_id": str(getattr(run, "run_id", "")),
        "team_task_id": str(getattr(run, "team_task_id", "")),
        "status": status,
        "stage": team_run_stage(run),
        "workspace": str(getattr(run, "workspace_root", "")),
        "approval_required": True,
        "approval_status": "consumed",
        "approval_id": str(getattr(run, "approval_id", "")),
        "plan_sha256": str(getattr(run, "plan_hash", "")),
        "implementation_status": (
            "complete" if result is not None else status
        ),
        "review_status": (
            "awaiting_review" if status == "awaiting_review" else status
        ),
        "next_actions": list(actions),
        "created_at": str(getattr(run, "started_at", "")),
        "updated_at": (
            str(getattr(run, "completed_at", ""))
            or str(getattr(run, "started_at", ""))
        ),
    }
    if workspace is not None:
        data["workspace_mode"] = str(getattr(workspace, "mode", ""))
        branch = str(getattr(workspace, "branch", "")).strip()
        commit = str(getattr(workspace, "commit", "")).strip()
        if branch:
            data["branch"] = branch
        if commit:
            data["commit"] = commit
    if validation is not None:
        data["validation_status"] = str(getattr(validation, "status", ""))
        data["validation"] = validation.to_dict()
    if documentation is not None:
        data["documentation_review_status"] = str(
            getattr(documentation, "status", "")
        )
        data["documentation_review"] = documentation.to_dict()
    if result is not None:
        data["tests"] = [
            item.to_dict() for item in getattr(result, "tests", ()) or ()
        ]
        data["risks"] = list(getattr(result, "risks", ()) or ())
    if changes is not None:
        data["files_changed"] = [
            item.to_dict() for item in getattr(changes, "changes", ()) or ()
        ]
    error = str(getattr(run, "error", "")).strip()
    if error:
        data["error"] = error
    if hasattr(run, "to_dict") and callable(run.to_dict):
        data["run"] = run.to_dict()
    return data


class AiTeamApplicationHandler:
    """Coordinate existing Team services and return structured application results."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self._last_task = None

    @property
    def team(self):
        service = getattr(self.runtime, "team", None)
        if service is None:
            raise RuntimeError("AI Team service is not available.")
        return service

    @property
    def bridge(self):
        service = getattr(self.runtime, "codex_bridge", None)
        if service is None:
            raise RuntimeError("Codex Bridge service is not available.")
        return service

    def list(self, *, limit: int = 5) -> ApplicationResult:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            return ApplicationResult.failure(
                "AI Team tasks could not be listed: limit must be between 1 and 100.",
                errors=("AI Team list limit must be between 1 and 100.",),
            )
        try:
            tasks = self.team.recent(limit)
        except (OSError, PermissionError, RuntimeError, ValueError) as exc:
            return self._failure("AI Team tasks could not be listed", exc)
        payload = [team_task_lifecycle_data(task) for task in tasks]
        lines = [
            "AI Team",
            "-" * 62,
            "Planning: Architect -> Engineering Reviewer -> explicit Y/N/D approval.",
            (
                "Implementation Engine -> read-only Automatic Tester -> "
                "read-only Documentation Reviewer -> Awaiting Review."
            ),
            "Commits, pushes, merges, tags, and pull requests remain disabled.",
        ]
        if tasks:
            lines.append("Recent tasks")
            lines.extend(
                f"  {task.task_id} | "
                f"{task.status.replace('_', ' ').title()} | {task.goal[:60]}"
                for task in tasks
            )
        else:
            lines.append("No team planning tasks have been created yet.")
        lines.extend([
            "-" * 62,
            (
                'Commands: team plan "<goal>" | team plan --manual "<goal>" | '
                "team roles | team role show/set/reset | "
                "team approve <task-id> | "
                "team implement <task-id> <approval-id> | team run <run-id> | "
                "team test <run-id|last> | team rollback <run-id>"
            ),
        ])
        return ApplicationResult.success(
            "\n".join(lines),
            data={"tasks": payload, "count": len(payload)},
            next_actions=("team.plan",),
        )

    def show_task(self, request: TeamTaskRequest) -> ApplicationResult:
        try:
            task_id = self._required(request.team_task_id, "AI Team task ID")
        except ValueError as exc:
            return self._failure("AI Team task could not be shown", exc)
        try:
            task = self.team.task(task_id)
        except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
            return self._failure("AI Team task could not be shown", exc, team_task_id=task_id)
        return self._task_result(task)

    def plan(self, request: TeamPlanRequest) -> ApplicationResult:
        goal = " ".join(str(request.goal).split()).strip()
        if not goal:
            return ApplicationResult.failure(
                "AI Team planning failed: AI Team goal cannot be empty.",
                errors=("AI Team goal cannot be empty.",),
            )
        selected = tuple(str(item).strip() for item in request.selected_agents if str(item).strip())
        try:
            if (
                not selected
                and str(request.provider or "auto") == "auto"
                and str(request.model or "auto") == "auto"
                and request.task_id is None
            ):
                task = self.team.plan(goal)
            else:
                task = self.team.plan(
                    goal,
                    selected_agents=list(selected) if selected else None,
                    provider=str(request.provider or "auto"),
                    model=str(request.model or "auto"),
                    task_id=request.task_id,
                )
        except (OSError, PermissionError, TeamPlanningError, ValueError) as exc:
            task_id = str(getattr(exc, "task_id", "")).strip()
            return self._failure(
                "AI Team planning failed",
                exc,
                team_task_id=task_id,
                next_actions=((f"team.show {task_id}",) if task_id else ()),
            )
        return self._task_result(task)

    def approval_details(self, request: TeamTaskRequest) -> ApplicationResult:
        try:
            task_id = self._required(request.team_task_id, "AI Team task ID")
        except ValueError as exc:
            return self._failure("AI Team approval details are unavailable", exc)
        try:
            task = self.team.task(task_id)
            # A few legacy embedders supplied a planning-only Team facade. Keep
            # interactive details compatible without making the persisted lookup
            # optional for production services.
            if str(getattr(task, "task_id", "")) != task_id:
                cached = self._last_task
                if str(getattr(cached, "task_id", "")) == task_id:
                    task = cached
            capabilities = self._workspace_capabilities()
            engines = self._execution_engines()
        except (FileNotFoundError, OSError, PermissionError, RuntimeError, ValueError) as exc:
            return self._failure("AI Team approval details are unavailable", exc)
        engine_label = "Codex CLI (codex)"
        if engines is not None:
            try:
                detected = {engine.engine_id: engine for engine in engines.status()}
                engine = detected.get("codex")
                if engine is None or not engine.ready_for_implementation:
                    engine_label = "Codex CLI (not currently available)"
                elif engine.version:
                    engine_label = f"Codex CLI {engine.version} (codex)"
            except (OSError, TypeError, ValueError):
                pass
        risks = []
        for role in ("architect", "engineer_reviewer"):
            artifact = task.artifact(role)
            if artifact is not None:
                for risk in artifact.output.risks:
                    if risk not in risks:
                        risks.append(risk)
        plan_hash = PlanSnapshot.from_team_task(task).hash
        lines = [
            "AI Team Approval Details",
            "-" * 72,
            f"Task: {task.task_id}",
            f"Plan SHA-256: {plan_hash}",
            f"Workspace: {capabilities.root}",
            f"Workspace Mode: {capabilities.mode.title()}",
            f"Execution Engine: {engine_label}",
            "Sandbox Mode: workspace-write",
            "Expected Permissions:",
            "  - Read and write only inside the exact approved workspace",
            "  - Network and web search disabled",
            "  - Temporary, parent, profile, and unrelated writable roots excluded",
            "  - .git, .codex, and .agents protected; no commit, push, merge, or PR",
            "Final Plan:",
        ]
        lines.extend(f"  {index}. {item}" for index, item in enumerate(task.final_plan, 1))
        lines.append("Risks:")
        lines.extend((f"  - {item}" for item in risks) if risks else ("  none reported",))
        return ApplicationResult.success(
            "\n".join(lines),
            data={
                **team_task_lifecycle_data(task),
                "workspace": str(capabilities.root),
                "workspace_mode": str(capabilities.mode),
                "plan_sha256": plan_hash,
                "execution_engine": "codex",
                "risks": list(risks),
            },
            next_actions=(f"team.approve {task.task_id}",),
        )

    def approve(self, request: TeamApprovalRequest) -> ApplicationResult:
        try:
            task_id = self._required(request.team_task_id, "AI Team task ID")
        except ValueError as exc:
            return self._failure("Codex Bridge approval failed", exc)
        try:
            if request.plan_sha256:
                task = self.team.task(task_id)
                actual_hash = PlanSnapshot.from_team_task(task).hash
                if actual_hash != str(request.plan_sha256).strip().lower():
                    raise ValueError("Plan SHA-256 does not match the persisted Team plan.")
            self._bind_workspace()
            approval = self.bridge.approve(task_id, actor=str(request.actor or "user"))
        except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
            return self._failure(
                "Codex Bridge approval failed",
                exc,
                team_task_id=task_id,
                next_actions=(f"team.show {task_id}",),
            )
        sync_warnings = self._sync(team_task_id=approval.team_task_id)
        data: dict[str, object] = {
            "team_task_id": approval.team_task_id,
            "status": "approved",
            "stage": "implementation",
            "workspace": approval.workspace_root,
            "workspace_mode": approval.workspace.mode,
            "approval_required": True,
            "approval_status": "approved",
            "approval_id": approval.approval_id,
            "plan_sha256": approval.plan_hash,
            "execution_engine": approval.execution_engine,
            "created_at": approval.approved_at,
            "updated_at": approval.approved_at,
            "next_actions": [
                f"team.implement {approval.team_task_id} {approval.approval_id}"
            ],
            "approval": approval.to_dict(),
        }
        if approval.workspace.branch:
            data["branch"] = approval.workspace.branch
        if approval.workspace.commit:
            data["commit"] = approval.workspace.commit
        return ApplicationResult.success(
            self._format_approval(approval, show_manual_command=True),
            data=data,
            warnings=sync_warnings,
            next_actions=tuple(data["next_actions"]),
        )

    def implement(self, request: TeamImplementationRequest) -> ApplicationResult:
        try:
            task_id = self._required(request.team_task_id, "AI Team task ID")
            approval_id = self._required(request.approval_id, "Approval ID")
        except ValueError as exc:
            return self._failure("Codex Bridge execution failed", exc)
        engines = self._execution_engines()
        if engines is None:
            return ApplicationResult.failure(
                "No execution engine service is available.",
                data={"team_task_id": task_id, "approval_id": approval_id},
                errors=("No execution engine service is available.",),
            )
        try:
            registry = self._role_registry()
            execution_engine = (
                registry.engine("implementation")
                if registry is not None
                else engines.require_codex()
            )
            if execution_engine is None:
                execution_engine = engines.require_codex()
        except (
            ConnectionError,
            OSError,
            RuntimeError,
            ValueError,
            ExecutionEngineUnavailable,
        ) as exc:
            return ApplicationResult.failure(
                f"AI Team execution role validation failed: {exc}\n"
                + self._no_execution_engine_message(engines),
                data={"team_task_id": task_id, "approval_id": approval_id},
                errors=(str(exc),),
            )
        try:
            capabilities = self._bind_workspace()
            context = self.bridge.execution_context(
                task_id,
                approval_id,
                execution_engine,
                capabilities,
            )
            run = self.bridge.execute(context)
        except ExecutionEngineUnavailable as exc:
            return ApplicationResult.failure(
                self._no_execution_engine_message(engines),
                data={"team_task_id": task_id, "approval_id": approval_id},
                errors=(str(exc),),
            )
        except (
            FileNotFoundError,
            OSError,
            PermissionError,
            TypeError,
            ValueError,
            WorkspaceSnapshotError,
            CodexBridgeError,
        ) as exc:
            run_id = str(getattr(exc, "run_id", "")).strip()
            if run_id:
                sync_warnings = self._sync(run_id=run_id)
            else:
                sync_warnings = ()
            message = f"Codex Bridge execution failed: {exc}"
            if isinstance(exc, CodexBridgeError) and exc.category == "codex_cli_unavailable":
                message = self._no_execution_engine_message(engines)
            if run_id:
                message += f"\nSaved run: {run_id}"
            return ApplicationResult.failure(
                message,
                data={
                    "team_task_id": task_id,
                    "approval_id": approval_id,
                    **({"run_id": run_id} if run_id else {}),
                },
                errors=(str(exc),),
                warnings=sync_warnings,
                next_actions=((f"team.show {run_id}",) if run_id else ()),
            )
        sync_warnings = self._sync(run_id=run.run_id)
        result = self._run_result(run, warnings=sync_warnings)
        return ApplicationResult.success(
            "Starting one approval-bound local Codex execution...\n" + result.message,
            data=result.data,
            warnings=result.warnings,
            next_actions=result.next_actions,
        )

    def show_run(self, request: TeamRunRequest) -> ApplicationResult:
        try:
            run_id = self._required(request.run_id, "AI Team run ID")
        except ValueError as exc:
            return self._failure("Codex Bridge Error", exc)
        try:
            run = self.bridge.run(run_id)
        except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
            return self._failure("Codex Bridge Error", exc, run_id=run_id)
        return self._run_result(run)

    def validate(self, request: TeamRunRequest) -> ApplicationResult:
        try:
            run_id = self._required(request.run_id, "AI Team run ID")
        except ValueError as exc:
            return self._failure("Automatic validation refused", exc)
        try:
            self._bind_workspace()
            selected = (
                self.bridge.latest_validatable_run()
                if run_id.lower() == "last"
                else self.bridge.run(run_id)
            )
            run = self.bridge.validate(selected.run_id)
        except (
            FileNotFoundError,
            OSError,
            PermissionError,
            RuntimeError,
            ValueError,
        ) as exc:
            return self._failure("Automatic validation refused", exc, run_id=run_id)
        sync_warnings = self._sync(run_id=run.run_id)
        return self._run_result(run, warnings=sync_warnings)

    def documentation_review(self, request: TeamRunRequest) -> ApplicationResult:
        try:
            run_id = self._required(request.run_id, "AI Team run ID")
        except ValueError as exc:
            return self._failure("Documentation Review refused", exc)
        selected_latest = run_id.lower() == "last"
        try:
            self._bind_workspace()
            selected = (
                self.bridge.latest_documentable_run()
                if selected_latest
                else self.bridge.run(run_id)
            )
            run = self.bridge.document(selected.run_id)
        except (
            FileNotFoundError,
            OSError,
            PermissionError,
            RuntimeError,
            ValueError,
        ) as exc:
            return self._failure("Documentation Review refused", exc, run_id=run_id)
        sync_warnings = self._sync(run_id=run.run_id)
        prefix = (
            f"Selected documentation run: {selected.run_id}\n"
            if selected_latest else ""
        )
        result = self._run_result(run, warnings=sync_warnings)
        return ApplicationResult.success(
            prefix + result.message,
            data=result.data,
            warnings=result.warnings,
            next_actions=result.next_actions,
        )

    def documentation_status(self, request: TeamRunRequest) -> ApplicationResult:
        try:
            run_id = self._required(request.run_id, "AI Team run ID")
        except ValueError as exc:
            return self._failure("Documentation Review Error", exc)
        try:
            self._bind_workspace()
            run = self.bridge.documentation_status(run_id)
        except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
            return self._failure("Documentation Review Error", exc, run_id=run_id)
        documentation = getattr(run, "documentation", None)
        if documentation is None:
            return ApplicationResult.success(
                f"Documentation Review: Not Run ({run.run_id})",
                data=team_run_lifecycle_data(run),
                next_actions=team_run_next_actions(run),
            )
        lines = self._documentation_lines(
            documentation,
            attempts=len(getattr(run, "documentation_history", ())),
        )
        if run.status == "rolled_back":
            lines.append(
                "Run status: Rolled Back; documentation artifacts are retained for audit."
            )
        return ApplicationResult.success(
            "\n".join(lines),
            data=team_run_lifecycle_data(run),
            next_actions=team_run_next_actions(run),
        )

    def rollback_preview(self, request: TeamRunRequest) -> ApplicationResult:
        try:
            run_id = self._required(request.run_id, "AI Team run ID")
        except ValueError as exc:
            return self._failure("Codex Bridge Error", exc)
        try:
            run = self.bridge.run(run_id)
        except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
            return self._failure("Codex Bridge Error", exc, run_id=run_id)
        return ApplicationResult.success(
            (
                f"Rollback workspace changes from {run.run_id}?\n"
                "This removes files created by the run and restores its saved preimages."
            ),
            data=team_run_lifecycle_data(run),
            next_actions=(f"team.rollback {run.run_id}",),
        )

    def rollback(self, request: TeamRollbackRequest) -> ApplicationResult:
        try:
            run_id = self._required(request.run_id, "AI Team run ID")
        except ValueError as exc:
            return self._failure("Team rollback refused", exc)
        if not request.confirmed:
            return ApplicationResult.failure(
                "Team rollback requires explicit confirmation.",
                data={"run_id": run_id, "approval_required": True},
                errors=("Rollback was not explicitly confirmed.",),
                next_actions=(f"team.show {run_id}",),
            )
        try:
            rolled_back = self.bridge.rollback(run_id)
        except (
            FileNotFoundError,
            OSError,
            PermissionError,
            ValueError,
            WorkspaceRollbackError,
        ) as exc:
            return self._failure("Team rollback refused", exc, run_id=run_id)
        sync_warnings = self._sync(run_id=rolled_back.run_id)
        return ApplicationResult.success(
            (
                f"[OK] Run {rolled_back.run_id} was rolled back without "
                "Git reset or checkout."
            ),
            data=team_run_lifecycle_data(rolled_back),
            warnings=sync_warnings,
            next_actions=team_run_next_actions(rolled_back),
        )

    def roles(self) -> ApplicationResult:
        try:
            roles = self.team.roles()
        except (ConnectionError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            return self._failure("AI Team role configuration is invalid", exc)
        lines = [
            "AI Team Roles",
            "-" * 96,
            (
                f"{'Role':<23} {'Assignment':<30} {'Availability':<13} "
                f"{'Source':<15} Capability"
            ),
        ]
        for role in roles:
            availability = "Ready" if role.available else "Unavailable"
            lines.append(
                f"{role.display_name:<23} {role.actual_assignment:<30} "
                f"{availability:<13} {role.source:<15} {role.capability}"
            )
            lines.append(
                f"  Type: {role.category} | Requested: {role.requested_assignment}"
            )
            lines.append(f"  Fallback: {role.fallback}")
            if role.fallback_reason:
                lines.append(f"  Fallback reason: {role.fallback_reason}")
            if role.availability_reason:
                lines.append(f"  Availability detail: {role.availability_reason}")
            if role.agent_id:
                lines.append(f"  Agent: {role.agent_id} ({role.agent_name})")
        lines.append(
            "Orion owns every prompt, handoff, artifact, approval, and user-facing result."
        )
        return ApplicationResult.success(
            "\n".join(lines),
            data={"roles": [self._serialize_role(role) for role in roles]},
        )

    def show_role(self, role_name: str) -> ApplicationResult:
        registry = self._role_registry()
        if registry is None:
            return ApplicationResult.failure(
                "AI Team role registry is not available.",
                errors=("AI Team role registry is not available.",),
            )
        try:
            role = registry.show(self._required(role_name, "AI Team role"))
        except (ConnectionError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            return self._failure("AI Team role configuration is invalid", exc)
        lines = [
            f"AI Team Role: {role.display_name}",
            "-" * 72,
            f"Role ID: {role.role}",
            f"Type: {role.category}",
            f"Requested Assignment: {role.requested_assignment}",
            f"Actual Assignment: {role.actual_assignment}",
            f"Availability: {'Ready' if role.available else 'Unavailable'}",
        ]
        if role.availability_reason:
            lines.append(f"Availability Detail: {role.availability_reason}")
        lines.extend([
            f"Capability: {role.capability}",
            f"Fallback: {role.fallback}",
        ])
        if role.fallback_reason:
            lines.append(f"Fallback Reason: {role.fallback_reason}")
        lines.append(f"Source: {role.source}")
        if role.agent_id:
            lines.append(f"Agent: {role.agent_id} ({role.agent_name})")
        return ApplicationResult.success(
            "\n".join(lines),
            data={"role": self._serialize_role(role)},
        )

    def set_role(self, request: TeamRoleAssignmentRequest) -> ApplicationResult:
        registry = self._role_registry()
        if registry is None:
            return ApplicationResult.failure(
                "AI Team role registry is not available.",
                errors=("AI Team role registry is not available.",),
            )
        try:
            role = registry.set(
                self._required(request.role, "AI Team role"),
                self._required(request.assignment, "AI Team role assignment"),
            )
        except (ConnectionError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            return self._failure("AI Team role assignment was not saved", exc)
        return ApplicationResult.success(
            f"[OK] {role.display_name} -> {role.actual_assignment} (user-configured)",
            data={"role": self._serialize_role(role)},
        )

    def reset_role(self, role_name: str) -> ApplicationResult:
        registry = self._role_registry()
        if registry is None:
            return ApplicationResult.failure(
                "AI Team role registry is not available.",
                errors=("AI Team role registry is not available.",),
            )
        try:
            role = registry.reset(self._required(role_name, "AI Team role"))
        except (ConnectionError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            return self._failure("AI Team role assignment was not reset", exc)
        return ApplicationResult.success(
            (
                f"[OK] {role.display_name} reset to {role.requested_assignment} "
                f"({role.actual_assignment})"
            ),
            data={"role": self._serialize_role(role)},
        )

    def create_agent_draft(self, goal: str) -> ApplicationResult:
        drafts = getattr(self.runtime, "agent_team_drafts", None)
        if drafts is None:
            return ApplicationResult.failure(
                "Workspace agent team drafts are not available.",
                errors=("Workspace agent team drafts are not available.",),
            )
        try:
            draft = drafts.create(self._required(goal, "Agent team goal"))
        except (OSError, PermissionError, ValueError) as exc:
            return self._failure("Agent team draft was not created", exc)
        return ApplicationResult.success(
            (
                f"[OK] Agent team draft created: {draft.goal}\n"
                "Add agents in order with: team agents add <agent>\n"
                "Run the selected team with: team run"
            ),
            data={"draft": draft.to_dict()},
            next_actions=("team agents add <agent>", "team run"),
        )

    def add_agent_to_draft(self, reference: str) -> ApplicationResult:
        drafts = getattr(self.runtime, "agent_team_drafts", None)
        agents = getattr(self.runtime, "agents", None)
        if drafts is None or agents is None:
            return ApplicationResult.failure(
                "Workspace agent team drafts are not available.",
                errors=("Workspace agent team drafts are not available.",),
            )
        try:
            agent = agents.load(self._required(reference, "Agent reference"))
            if not agent.enabled:
                raise ValueError(f"Agent is disabled: {agent.agent_id}")
            draft = drafts.add(agent.agent_id)
        except (
            FileNotFoundError,
            OSError,
            PermissionError,
            RuntimeError,
            ValueError,
        ) as exc:
            return self._failure("Agent was not added", exc)
        return ApplicationResult.success(
            f"[OK] Added {agent.name}. Order: " + " -> ".join(draft.selected_agents),
            data={"draft": draft.to_dict(), "agent_id": agent.agent_id},
            next_actions=("team run",),
        )

    def agent_draft(self) -> ApplicationResult:
        """Load the active workspace draft for CLI-assisted agent selection."""
        drafts = getattr(self.runtime, "agent_team_drafts", None)
        if drafts is None:
            return ApplicationResult.failure(
                "Workspace agent team drafts are not available.",
                errors=("Workspace agent team drafts are not available.",),
            )
        try:
            draft = drafts.load()
        except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
            return self._failure("Agent team draft is unavailable", exc)
        return ApplicationResult.success("", data={"draft": draft.to_dict()})

    def enabled_agents(self) -> ApplicationResult:
        """List enabled agents as JSON-safe selection metadata."""
        agents = getattr(self.runtime, "agents", None)
        if agents is None:
            return ApplicationResult.failure(
                "Agent service is not available.",
                errors=("Agent service is not available.",),
            )
        try:
            enabled = [agent for agent in agents.all() if agent.enabled]
        except (OSError, PermissionError, ValueError) as exc:
            return self._failure("Could not list agents", exc)
        payload = [
            agent.to_dict() if hasattr(agent, "to_dict") else {
                "agent_id": str(getattr(agent, "agent_id", "")),
                "name": str(getattr(agent, "name", "")),
            }
            for agent in enabled
        ]
        return ApplicationResult.success("", data={"agents": payload})

    def run_agent_team(
        self,
        request: TeamPlanRequest,
        *,
        clear_draft: bool = False,
    ) -> ApplicationResult:
        if not request.selected_agents:
            return ApplicationResult.failure(
                "No agents were selected; the job was not started.",
                errors=("At least one enabled agent must be selected.",),
            )
        result = self.plan(request)
        if result.ok and clear_draft:
            drafts = getattr(self.runtime, "agent_team_drafts", None)
            if drafts is not None:
                try:
                    drafts.clear()
                except OSError:
                    pass
        if not result.ok:
            return ApplicationResult.failure(
                result.message.replace("AI Team planning failed", "AI Team agent job failed", 1),
                data=result.data,
                errors=result.errors,
                warnings=result.warnings,
                next_actions=result.next_actions,
            )
        return result

    def sync(self, request: TeamSyncRequest) -> ApplicationResult:
        if not request.team_task_id and not request.run_id:
            return ApplicationResult.failure(
                "AI Team synchronization requires a task ID or run ID.",
                errors=("Synchronization reference is missing.",),
            )
        integration = getattr(self.runtime, "command_center_team", None)
        return synchronize_command_center_team(
            integration,
            team_task_id=str(request.team_task_id).strip(),
            run_id=str(request.run_id).strip(),
        )

    def _task_result(self, task: Any) -> ApplicationResult:
        self._last_task = task
        data = team_task_lifecycle_data(task)
        return ApplicationResult.success(
            self._format_task(task),
            data=data,
            next_actions=team_task_next_actions(task),
        )

    def _run_result(
        self,
        run: Any,
        *,
        warnings: tuple[str, ...] = (),
    ) -> ApplicationResult:
        return ApplicationResult.success(
            self._format_run(run, self._artifact_directory(run)),
            data=team_run_lifecycle_data(run),
            warnings=warnings,
            next_actions=team_run_next_actions(run),
        )

    def _artifact_directory(self, run: Any) -> Path | None:
        store = getattr(self.bridge, "store", None)
        resolver = getattr(store, "run_directory", None)
        if not callable(resolver):
            return None
        try:
            return Path(resolver(str(getattr(run, "run_id", ""))))
        except (OSError, TypeError, ValueError):
            return None

    def _sync(self, *, team_task_id: str = "", run_id: str = "") -> tuple[str, ...]:
        result = self.sync(TeamSyncRequest(team_task_id=team_task_id, run_id=run_id))
        return tuple((*result.warnings, *result.errors))

    def _bind_workspace(self):
        workspace_manager = getattr(self.runtime, "workspace_manager", None)
        if workspace_manager is not None:
            capabilities = workspace_manager.refresh_capabilities()
            self.bridge.bind(workspace_manager.root, capabilities)
            return capabilities
        return self.bridge.workspace_capabilities

    def _workspace_capabilities(self):
        try:
            return self._bind_workspace()
        except (OSError, ValueError):
            return self.bridge.workspace_capabilities

    def _execution_engines(self):
        return getattr(self.runtime, "execution_engines", None) or getattr(
            self.bridge, "execution_engines", None
        )

    def _role_registry(self):
        registry = getattr(self.runtime, "team_roles", None)
        if registry is not None:
            return registry
        team = getattr(self.runtime, "team", None)
        attributes = getattr(team, "__dict__", {})
        return attributes.get("role_registry") if isinstance(attributes, dict) else None

    @staticmethod
    def _required(value: object, label: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{label} cannot be empty.")
        return text

    @staticmethod
    def _failure(
        prefix: str,
        exc: BaseException,
        *,
        team_task_id: str = "",
        run_id: str = "",
        next_actions: tuple[str, ...] = (),
    ) -> ApplicationResult:
        message = f"{prefix}: {exc}"
        data: dict[str, object] = {"error_type": type(exc).__name__}
        if team_task_id:
            data["team_task_id"] = team_task_id
        if run_id:
            data["run_id"] = run_id
        return ApplicationResult.failure(
            message,
            data=data,
            errors=(str(exc),),
            next_actions=next_actions,
        )

    @staticmethod
    def _serialize_role(role: Any) -> dict[str, object]:
        if hasattr(role, "snapshot") and callable(role.snapshot):
            value = role.snapshot().to_dict()
        elif hasattr(role, "to_dict") and callable(role.to_dict):
            value = role.to_dict()
        else:
            value = {}
        for name in ("availability_reason", "agent_id", "agent_name"):
            item = getattr(role, name, "")
            if item:
                value[name] = item
        return value

    @staticmethod
    def _format_approval(approval: Any, *, show_manual_command: bool) -> str:
        lines = [
            "Codex Plan Approval",
            "-" * 72,
            f"AI Team Task: {approval.team_task_id}",
            f"Approval ID: {approval.approval_id}",
            f"Plan SHA-256: {approval.plan_hash}",
            f"Workspace: {approval.workspace_root}",
            f"Workspace Mode: {approval.workspace.mode.title()}",
            f"Execution Engine: Codex CLI ({approval.execution_engine})",
        ]
        if approval.workspace.is_git_repository:
            lines.append(f"Repository Root: {approval.workspace.git_root}")
            if approval.workspace.branch:
                lines.append(f"Branch: {approval.workspace.branch}")
        lines.append(
            "Approval is immutable and bound to this plan, workspace capability, "
            "Codex engine, active-workspace scope, and one implementation."
        )
        if show_manual_command:
            lines.append(
                f"Run with: team implement {approval.team_task_id} {approval.approval_id}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_task(task: Any) -> str:
        lines = [
            "AI Team Plan",
            "-" * 62,
            f"Task: {task.task_id}",
            f"Goal: {task.goal}",
        ]
        raw_assignments = getattr(task, "role_assignments", ())
        assignments = (
            raw_assignments
            if isinstance(raw_assignments, (list, tuple))
            else ()
        )
        if assignments:
            lines.append("\nWorkflow Role Assignments")
            for assignment in assignments:
                availability = "Ready" if assignment.available else "Unavailable"
                lines.append(
                    f"  {assignment.display_name:<23} {assignment.actual_assignment} "
                    f"[{availability}; {assignment.source}]"
                )
                if assignment.fallback_reason:
                    lines.append(f"    Fallback: {assignment.fallback_reason}")
        raw_snapshots = getattr(task, "agent_snapshots", ())
        snapshots = (
            raw_snapshots if isinstance(raw_snapshots, (list, tuple)) else ()
        )
        if snapshots:
            lines.append("\nSelected Agents (ordered snapshots)")
            for index, snapshot in enumerate(snapshots, 1):
                lines.append(
                    f"  {index}. {snapshot.name} ({snapshot.agent_id}) - "
                    f"{snapshot.job} | "
                    f"{snapshot.actual_provider}:{snapshot.actual_model}"
                )
                lines.append(f"     Responsibility: {snapshot.responsibility}")
        labels = {
            "architect": "Architect",
            "engineer_reviewer": "Engineering Reviewer",
        }
        for role in ("architect", "engineer_reviewer"):
            artifact = task.artifact(role)
            if artifact is None:
                continue
            lines.extend([f"\n{labels[role]}", f"  {artifact.output.summary}"])
            lines.extend(f"  - {item}" for item in artifact.output.recommendations)
            if artifact.output.risks:
                lines.append("  Risks:")
                lines.extend(f"    - {risk}" for risk in artifact.output.risks)
            metadata = getattr(artifact, "role_metadata", None)
            if metadata is not None:
                lines.append(
                    f"  Assignment: {metadata.requested_assignment} -> "
                    f"{metadata.actual_assignment}"
                )
                if metadata.fallback_reason:
                    lines.append(f"  Fallback: {metadata.fallback_reason}")
                lines.append(f"  Duration: {metadata.duration_seconds:.3f}s")
        for snapshot in snapshots:
            artifact = task.artifact(snapshot.agent_id)
            if artifact is None:
                continue
            lines.extend([
                f"\n{snapshot.name} Contribution",
                f"  {artifact.output.summary}",
            ])
            lines.extend(f"  - {item}" for item in artifact.output.recommendations)
            if artifact.output.risks:
                lines.append("  Risks:")
                lines.extend(f"    - {risk}" for risk in artifact.output.risks)
            metadata = getattr(artifact, "role_metadata", None)
            if metadata is not None:
                lines.append(
                    f"  Assignment: {metadata.requested_assignment} -> "
                    f"{metadata.actual_assignment}"
                )
                if metadata.fallback_reason:
                    lines.append(f"  Fallback: {metadata.fallback_reason}")
        if task.final_plan:
            lines.append("\nFinal Plan")
            lines.extend(
                f"  {index}. {item}"
                for index, item in enumerate(task.final_plan, start=1)
            )
        if task.usage:
            lines.append("\nUsage (estimated tokens)")
            for usage in task.usage:
                cost = (
                    "not configured"
                    if usage.estimated_cost_usd is None
                    else f"${usage.estimated_cost_usd:.6f}"
                )
                lines.append(
                    f"  {usage.role.replace('_', ' ').title():<23} "
                    f"{usage.provider}:{usage.model} | "
                    f"{usage.input_tokens} in + {usage.output_tokens} out | Cost: {cost}"
                )
            total = (
                "not configured"
                if task.estimated_cost_usd is None
                else f"${task.estimated_cost_usd:.6f}"
            )
            lines.append(f"  Total: {task.total_tokens} tokens | Cost: {total}")
        lines.append(f"\nStatus: {task.status.replace('_', ' ').title()}")
        if task.error:
            lines.append(f"Error: {task.error}")
        if task.status == "awaiting_approval":
            lines.extend([
                "No implementation has been performed. This task is awaiting your approval.",
                f"Approve this exact plan with: team approve {task.task_id}",
            ])
        return "\n".join(lines)

    @classmethod
    def _format_run(
        cls,
        run: Any,
        artifact_directory: Path | None = None,
    ) -> str:
        lines = [
            "AI Team Run",
            "-" * 72,
            f"Run: {run.run_id}",
            f"AI Team Task: {run.team_task_id}",
            f"Approval: {run.approval_id}",
            f"Plan SHA-256: {run.plan_hash}",
            f"Workspace: {run.workspace_root}",
            f"Workspace Mode: {run.workspace.mode.title()}",
        ]
        if run.workspace.is_git_repository:
            lines.append(f"Repository Root: {run.workspace.git_root}")
            if run.workspace.branch:
                lines.append(f"Branch: {run.workspace.branch}")
            if run.workspace.commit:
                lines.append(f"Commit: {run.workspace.commit[:12]}")
        lines.extend([
            f"Status: {run.status.replace('_', ' ').title()}",
            "\nImplementation",
            "-" * 72,
        ])
        if run.result is not None:
            lines.extend([
                "Status: Complete",
                "\nSummary",
                f"  {run.result.summary}",
                "\nFiles Changed",
            ])
            if run.changes is not None:
                lines.extend([
                    f"  Created:  {len(run.changes.by_kind('created'))}",
                    f"  Modified: {len(run.changes.by_kind('modified'))}",
                    f"  Deleted:  {len(run.changes.by_kind('deleted'))}",
                ])
            lines.append("\nImplementation-Reported Tests")
            lines.extend(
                f"  - [{test.status.upper()}] {test.command}: {test.summary}"
                for test in run.result.tests
            )
            if run.result.risks:
                lines.append("\nRisks")
                lines.extend(f"  - {item}" for item in run.result.risks)
            if run.result.remaining_work:
                lines.append("\nRemaining Work")
                lines.extend(f"  - {item}" for item in run.result.remaining_work)
            if run.result.review_notes:
                lines.append("\nReview Notes")
                lines.extend(f"  - {item}" for item in run.result.review_notes)
        if run.changes is not None:
            lines.append("\nWorkspace Review")
            for kind, label in (
                ("created", "Created"),
                ("modified", "Modified"),
                ("deleted", "Deleted"),
            ):
                items = run.changes.by_kind(kind)
                lines.append(f"  {label}:")
                if not items:
                    lines.append("    none")
                for item in items:
                    suffix = " (binary metadata only)" if item.binary else ""
                    lines.append(f"    - {item.path}{suffix}")
            if run.changes.diff_truncated:
                lines.append(
                    "  Text diff was truncated at the configured safety limit."
                )
        validation = getattr(run, "validation", None)
        lines.extend(["\nAutomatic Validation", "-" * 72])
        if validation is None:
            lines.append("NOT RUN  No automatic validation attempt is recorded.")
        else:
            lines.append(
                f"Tester: {validation.tester_requested} -> "
                f"{validation.tester_resolved or 'unavailable'}"
            )
            if validation.fallback_reason:
                lines.append(f"Fallback: {validation.fallback_reason}")
            marker = {
                "passed": "PASS",
                "warning": "WARN",
                "failed": "FAIL",
                "skipped": "SKIP",
                "error": "ERROR",
            }
            lines.extend(
                f"{marker.get(check.status, check.status.upper()):5} "
                f"{check.name}: {check.summary}"
                for check in validation.checks
            )
            lines.extend(f"INFO  {item}" for item in validation.safe_diagnostics)
            lines.extend([
                "\nValidation Summary",
                f"  Checks:   {len(validation.checks)}",
                f"  Passed:   {len(validation.checks_passed)}",
                f"  Warnings: {len(validation.warnings)}",
                f"  Failed:   {len(validation.checks_failed)}",
                f"  Skipped:  {len(validation.skipped_checks)}",
                f"  Attempts: {len(getattr(run, 'validation_history', ())) or 1}",
            ])
        documentation = getattr(run, "documentation", None)
        if documentation is None:
            lines.extend([
                "\nDocumentation Review",
                "-" * 72,
                "NOT RUN  No documentation-review attempt is recorded.",
            ])
        else:
            lines.extend(
                cls._documentation_lines(
                    documentation,
                    attempts=len(getattr(run, "documentation_history", ())) or 1,
                )
            )
        if run.error:
            lines.append(f"Error category: {run.error}")
        if artifact_directory is not None:
            lines.append(f"\nArtifacts: {artifact_directory}")
        if run.status == "awaiting_review":
            validation_label = (
                validation.review_status.replace("Awaiting Review — ", "")
                if validation is not None else "Validation Not Run"
            )
            documentation_label = (
                documentation.review_status
                if documentation is not None else "Documentation Not Run"
            )
            lines.extend([
                "\nOverall Review Status",
                "-" * 72,
                (
                    f"Awaiting Review — {validation_label} — "
                    f"{documentation_label}"
                ),
                (
                    "Validation and Documentation Review never accept, edit, "
                    "or roll back changes."
                ),
                "No Git or pull-request action was performed.",
            ])
            if artifact_directory is not None:
                lines.append(
                    "Review the bounded diff at: "
                    f"{artifact_directory / 'workspace.diff'}"
                )
            lines.extend([
                f"Rerun validation with: team test {run.run_id}",
                f"Rerun documentation review with: team docs {run.run_id}",
                f"Rollback with: team rollback {run.run_id}",
            ])
        elif run.status == "rolled_back":
            lines.append("Workspace changes from this run have been safely rolled back.")
        return "\n".join(lines)

    @staticmethod
    def _documentation_lines(documentation: Any, *, attempts: int = 1) -> list[str]:
        lines = [
            "\nDocumentation Review",
            "-" * 72,
            (
                f"Reviewer: {documentation.reviewer_requested} -> "
                f"{documentation.reviewer_resolved or 'unavailable'}"
            ),
        ]
        if documentation.fallback_reason:
            lines.append(f"Fallback: {documentation.fallback_reason}")
        lines.extend([
            f"Status: {documentation.review_status}",
            f"Documents inspected: {len(documentation.documents_inspected)}",
            f"Warnings: {documentation.counts_by_severity.get('warning', 0)}",
            f"Errors: {documentation.counts_by_severity.get('error', 0)}",
            f"Attempts: {attempts}",
        ])
        marker = {"info": "INFO", "warning": "WARN", "error": "ERROR"}
        for finding in documentation.findings[:10]:
            lines.append(
                f"{marker.get(finding.severity, finding.severity.upper()):5} "
                f"{finding.document}"
            )
            lines.append(f"      {finding.finding}")
        remaining = len(documentation.findings) - 10
        if remaining > 0:
            lines.append(
                f"INFO  {remaining} additional finding(s) are stored in "
                "the bounded artifact."
            )
        lines.extend(f"INFO  {item}" for item in documentation.safe_diagnostics[:5])
        return lines

    @staticmethod
    def _no_execution_engine_message(engines: Any) -> str:
        lines = ["No execution engine is currently available.", "", "Detected:", ""]
        try:
            detected = {engine.engine_id: engine for engine in engines.status()}
        except (OSError, TypeError, ValueError):
            detected = {}
        for engine_id in (
            "codex_desktop",
            "chatgpt_desktop",
            "codex",
            "claude_code",
            "gemini_cli",
        ):
            engine = detected.get(engine_id)
            if engine is None:
                continue
            marker = (
                "✓"
                if engine.ready_for_implementation
                or (not engine.cli_support and engine.installed)
                else "!" if engine.installed else "✗"
            )
            lines.append(f"{marker} {engine.name}")
        lines.extend(["", "Use:", "", "  execution status", "", "to configure an execution engine."])
        return "\n".join(lines)
