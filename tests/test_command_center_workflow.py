import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orion.command_center import (
    ApprovalState,
    CommandCenterService,
    CommandCenterTeamIntegrationService,
    FileCommandCenterRepository,
    JobStatus,
    JobTeamIntegration,
    LaunchValidationError,
    TeamIntegrationLink,
    WorkflowAgentAssignment,
    WorkflowStage,
)
from orion.command_center.cli import CommandCenterCommandHandler
from orion.core.router import CommandRouter
from orion.services.registry import ServiceRegistry


NOW = "2026-07-27T22:00:00+00:00"
OLD = "2026-07-25T20:00:00+00:00"
JOB_ID = "job-command-center-001"
TEAM_TASK_ID = "team-command-center-001"
APPROVAL_ID = "approval-command-center-001"
RUN_ID = "run-command-center-001"


def agent(agent_id, job, *, enabled=True):
    return SimpleNamespace(
        agent_id=agent_id,
        name=agent_id.replace("-", " ").title(),
        enabled=enabled,
        scope="permanent",
        role=SimpleNamespace(job=job, specialty=job),
    )


ENGINEERING_AGENTS = (
    agent("planner", "Planner"),
    agent("architect", "Architect"),
    agent("software-engineer", "Software Engineer"),
    agent("engineer", "Engineering Reviewer"),
    agent("reviewer", "Code Reviewer"),
)


class FakeAgentManager:
    is_production_agent_manager = True

    def __init__(self, agents=ENGINEERING_AGENTS):
        self.agents = {item.agent_id: item for item in agents}
        self.route_calls = 0

    def all(self):
        return tuple(self.agents.values())

    def load(self, reference):
        normalized = str(reference).strip().casefold()
        matches = [
            item for item in self.agents.values()
            if item.agent_id.casefold() == normalized
            or item.name.casefold() == normalized
        ]
        if not matches:
            raise FileNotFoundError(f"Agent not found: {reference}")
        return matches[0]

    def resolution_candidates(
        self,
        selected,
        *,
        goal,
        provider="auto",
        model="auto",
    ):
        self.route_calls += 1
        if selected.agent_id == "route-disabled":
            raise ValueError("No provider/model is available for agent route-disabled.")
        return (
            SimpleNamespace(
                provider="openai",
                model="test-model",
                source="routing",
                fallback_reason="",
            ),
        )


class FakeTeam:
    def __init__(self):
        self.tasks = {}
        self.plan_calls = []
        self.next_status = "awaiting_approval"
        self.role_registry = None

    def reserve_task_id(self):
        return TEAM_TASK_ID

    def plan(
        self,
        goal,
        *,
        selected_agents,
        provider,
        model,
        task_id,
    ):
        self.plan_calls.append({
            "goal": goal,
            "selected_agents": tuple(selected_agents),
            "provider": provider,
            "model": model,
            "task_id": task_id,
        })
        task = SimpleNamespace(
            task_id=task_id,
            goal=goal,
            status=self.next_status,
            selected_agents=list(selected_agents),
            artifacts=[],
            error=(
                "Planner role failed (RuntimeError)."
                if self.next_status == "failed"
                else ""
            ),
        )
        self.tasks[task_id] = task
        return task

    def task(self, task_id):
        if task_id not in self.tasks:
            raise FileNotFoundError(f"AI Team task not found: {task_id}")
        return self.tasks[task_id]


class FakeExternalState:
    def __init__(self):
        self.approvals = {}
        self.runs = {}
        self.unresolved_runs = {}
        self.run_inspection_error = None

    def approvals_for_task(self, team_task_id):
        return tuple(self.approvals.get(team_task_id, ()))

    def runs_for_task(self, team_task_id):
        return tuple(
            item for item in self.runs.values()
            if item.team_task_id == team_task_id
        )

    def inspect_runs_for_task(self, team_task_id):
        if self.run_inspection_error is not None:
            raise self.run_inspection_error
        return SimpleNamespace(
            runs=self.runs_for_task(team_task_id),
            unresolved=tuple(self.unresolved_runs.get(team_task_id, ())),
        )

    def run(self, run_id):
        if run_id not in self.runs:
            raise FileNotFoundError(f"AI Team run not found: {run_id}")
        return self.runs[run_id]


def approval(*, state="approved"):
    return SimpleNamespace(
        approval_id=APPROVAL_ID,
        team_task_id=TEAM_TASK_ID,
        approved_at=NOW,
        execution_engine="codex",
        state=state,
    )


def run(
    status,
    *,
    validation=None,
    documentation=None,
    error="",
    result_summary="Implementation finished safely.",
):
    return SimpleNamespace(
        run_id=RUN_ID,
        approval_id=APPROVAL_ID,
        team_task_id=TEAM_TASK_ID,
        started_at=NOW,
        status=status,
        validation=validation,
        documentation=documentation,
        error=error,
        result=SimpleNamespace(summary=result_summary),
    )


class WorkflowFixture:
    def build(self, root, *, agents=ENGINEERING_AGENTS, now=NOW):
        manager = FakeAgentManager(agents)
        workspace = SimpleNamespace(
            root=Path(root).resolve(),
            capabilities=SimpleNamespace(
                mode="standard",
                is_git_repository=False,
                branch="",
            ),
        )
        repository = FileCommandCenterRepository(
            Path(root) / "user" / "command-center"
        )
        service = CommandCenterService(
            repository,
            manager,
            workspace_manager=workspace,
            now=lambda: now,
        )
        service.ensure_default_organization()
        department = service.create_department(
            name="Engineering",
            workflow_policy_reference="engineering",
        )
        for item in agents:
            if item.agent_id in {
                "planner", "architect", "software-engineer", "engineer", "reviewer"
            }:
                department = service.add_agent(
                    department.department_id,
                    item.agent_id,
                )
        team = FakeTeam()
        external = FakeExternalState()
        registry = ServiceRegistry()
        registry.register("command_center", service)
        registry.register("team", team)
        registry.register("agents", manager)
        registry.register("workspace", workspace)
        integration = CommandCenterTeamIntegrationService(
            service,
            team,
            manager,
            workspace_manager=workspace,
            service_registry=registry,
            external_state_source=external,
            execution_engines=None,
            now=lambda: now,
        )
        service.set_team_integration(integration)
        return service, integration, team, external, manager

    @staticmethod
    def create_job(service, root, **overrides):
        values = {
            "job_id": JOB_ID,
            "title": "Build workflow integration",
            "goal": "Create a safe implementation plan and stop for approval.",
            "department": "Engineering",
            "workspace_reference": root,
        }
        values.update(overrides)
        return service.create_job(**values)


class CommandCenterLaunchTests(unittest.TestCase, WorkflowFixture):
    def test_dry_run_resolves_workflow_routes_and_performs_no_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, team, _, manager = self.build(tmp)
            job = self.create_job(service, tmp)
            before = {
                path.relative_to(service.repository.root): path.read_bytes()
                for path in service.repository.root.rglob("*")
                if path.is_file()
            }

            preview = integration.preview_launch(job.job_id)

            after = {
                path.relative_to(service.repository.root): path.read_bytes()
                for path in service.repository.root.rglob("*")
                if path.is_file()
            }
            self.assertTrue(preview.allowed)
            self.assertEqual(before, after)
            self.assertEqual(team.plan_calls, [])
            self.assertEqual(manager.route_calls, 5)
            self.assertEqual(
                preview.workflow.agent_ids,
                (
                    "planner", "architect", "software-engineer",
                    "engineer", "reviewer",
                ),
            )
            self.assertEqual(Path(preview.workspace_root), Path(tmp).resolve())
            self.assertNotIn(str(Path(tmp).parent), json.dumps(preview.to_dict()))

    def test_explicit_launch_creates_one_team_task_and_durable_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, team, _, _ = self.build(tmp)
            job = self.create_job(service, tmp)

            result = integration.launch(job.job_id)

            self.assertEqual(len(team.plan_calls), 1)
            self.assertEqual(result.team_task_id, TEAM_TASK_ID)
            linked = service.job(job.job_id)
            self.assertEqual(linked.status, JobStatus.AWAITING_APPROVAL)
            self.assertEqual(linked.current_stage, "awaiting_approval")
            self.assertEqual(linked.progress, 25)
            self.assertEqual(linked.approval_state, ApprovalState.PENDING)
            stored = JobTeamIntegration.from_job(linked)
            self.assertEqual(stored.active_link.team_task_id, TEAM_TASK_ID)
            self.assertEqual(
                JobTeamIntegration.from_value(stored.to_dict()),
                stored,
            )
            with self.assertRaises(LaunchValidationError):
                integration.launch(job.job_id)

    def test_creation_is_inert_and_failed_validation_preserves_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, team, _, _ = self.build(tmp)
            job = self.create_job(service, tmp)
            self.assertEqual(team.plan_calls, [])
            self.assertEqual(job.status, JobStatus.DRAFT)

            with self.assertRaises(LaunchValidationError):
                integration.launch(job.job_id, workflow="missing-workflow")

            current = service.job(job.job_id)
            self.assertEqual(current.to_dict(), job.to_dict())
            self.assertEqual(team.plan_calls, [])
            types = [item.event_type for item in service.activity(20)]
            self.assertIn("job.launch_requested", types)
            self.assertIn("job.launch_failed", types)

    def test_launch_rejects_terminal_active_department_agent_and_workspace_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, _, manager = self.build(tmp)
            job = self.create_job(service, tmp)
            service.cancel_job(job.job_id)
            self.assertFalse(integration.preview_launch(job.job_id).allowed)

            other = self.create_job(
                service,
                tmp,
                job_id="job-command-center-002",
            )
            department = service.department("engineering")
            service.repository.save_department(
                replace(department, enabled=False),
                overwrite=True,
            )
            self.assertFalse(integration.preview_launch(other.job_id).allowed)
            service.repository.save_department(department, overwrite=True)

            manager.agents["architect"] = replace_namespace(
                manager.agents["architect"],
                enabled=False,
            )
            preview = integration.preview_launch(other.job_id)
            self.assertFalse(preview.allowed)
            self.assertTrue(any("disabled" in item for item in preview.errors))
            manager.agents["architect"] = agent("architect", "Architect")

            outside = Path(tmp).parent
            preview = integration.preview_launch(
                other.job_id,
                workspace=str(outside),
            )
            self.assertFalse(preview.allowed)
            self.assertTrue(any("active workspace" in item for item in preview.errors))

    def test_explicit_assignments_take_priority_without_mutating_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, _, manager = self.build(tmp)
            job = self.create_job(
                service,
                tmp,
                assigned_agents=(
                    "planner", "architect", "software-engineer",
                    "engineer", "reviewer",
                ),
            )
            before = {
                key: dict(vars(value))
                for key, value in manager.agents.items()
            }

            preview = integration.preview_launch(job.job_id)

            self.assertTrue(preview.allowed)
            self.assertEqual(preview.workflow.source, "department-policy")
            self.assertEqual(
                before,
                {key: dict(vars(value)) for key, value in manager.agents.items()},
            )

    def test_ambiguous_or_missing_required_role_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents = tuple(
                item for item in ENGINEERING_AGENTS
                if item.agent_id != "architect"
            )
            service, integration, _, _, _ = self.build(tmp, agents=agents)
            job = self.create_job(service, tmp)
            preview = integration.preview_launch(job.job_id)
            self.assertFalse(preview.allowed)
            self.assertTrue(
                any("architecture" in item for item in preview.errors)
            )


class CommandCenterSynchronizationTests(unittest.TestCase, WorkflowFixture):
    def launched(self, tmp):
        service, integration, team, external, manager = self.build(tmp)
        job = self.create_job(service, tmp)
        integration.launch(job.job_id)
        return service, integration, team, external, manager

    def test_planning_architecture_and_approval_mappings(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, team, external, _ = self.launched(tmp)
            job = service.job(JOB_ID)
            link = JobTeamIntegration.from_job(job).active_link
            team.tasks[TEAM_TASK_ID] = SimpleNamespace(
                task_id=TEAM_TASK_ID,
                status="planning",
                selected_agents=[
                    "planner", "architect", "software-engineer",
                    "engineer", "reviewer",
                ],
                artifacts=[SimpleNamespace(role="planner")],
                error="",
            )
            architecture = integration.sync_job(JOB_ID).job
            self.assertEqual(architecture.current_stage, "architecture")
            self.assertEqual(architecture.status, JobStatus.PLANNING)
            self.assertEqual(
                JobTeamIntegration.from_job(architecture).active_link.active_agent_id,
                "architect",
            )

            team.tasks[TEAM_TASK_ID].status = "awaiting_approval"
            awaiting = integration.sync_job(JOB_ID).job
            self.assertEqual(awaiting.status, JobStatus.AWAITING_APPROVAL)
            self.assertEqual(awaiting.approval_state, ApprovalState.PENDING)

            external.approvals[TEAM_TASK_ID] = [approval()]
            approved = integration.sync_job(JOB_ID).job
            self.assertEqual(approved.status, JobStatus.QUEUED)
            self.assertEqual(approved.progress, 30)
            self.assertEqual(approved.approval_state, ApprovalState.APPROVED)
            self.assertIn(
                "team implement",
                JobTeamIntegration.from_job(approved).active_link.next_action,
            )
            self.assertEqual(link.workflow_id, "engineering")

    def test_planning_and_awaiting_approval_require_no_execution_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, team, _, _ = self.build(tmp)
            team.next_status = "planning"
            job = self.create_job(service, tmp)

            launched = integration.launch(job.job_id)
            repeated = integration.sync_job(job.job_id)

            self.assertEqual(launched.job.status, JobStatus.PLANNING)
            self.assertEqual(launched.warnings, ())
            self.assertFalse(repeated.changed)
            self.assertEqual(
                JobTeamIntegration.from_job(repeated.job).active_link.team_run_id,
                "",
            )
            doctor_codes = {
                item["code"] for item in service.doctor()["issues"]
            }
            self.assertNotIn("integration.unresolved_team_run", doctor_codes)

        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, _, _ = self.launched(tmp)
            synchronized = integration.sync_job(JOB_ID)
            self.assertEqual(
                synchronized.job.status,
                JobStatus.AWAITING_APPROVAL,
            )
            self.assertEqual(synchronized.warnings, ())

    def test_launch_survives_optional_run_inspection_failure_and_retry_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, team, external, _ = self.build(tmp)
            external.run_inspection_error = ValueError(
                "Codex run is invalid: run-unrelated-history"
            )
            job = self.create_job(service, tmp)

            launched = integration.launch(job.job_id)

            self.assertEqual(launched.job.status, JobStatus.AWAITING_APPROVAL)
            self.assertEqual(len(team.plan_calls), 1)
            self.assertEqual(
                launched.warnings,
                ("Team workflow run inspection could not be completed.",),
            )
            self.assertNotIn("Codex", launched.warnings[0])
            with self.assertRaises(LaunchValidationError):
                integration.launch(job.job_id)
            self.assertEqual(len(team.plan_calls), 1)

            warning_events = [
                item for item in service.activity(1_000)
                if item.event_type == "job.sync_warning"
            ]
            repeated = integration.sync_job(job.job_id)
            self.assertFalse(repeated.changed)
            self.assertEqual(
                len([
                    item for item in service.activity(1_000)
                    if item.event_type == "job.sync_warning"
                ]),
                len(warning_events),
            )

    def test_unresolved_run_warns_without_blocking_and_valid_run_supersedes_it(self):
        unresolved_id = "run-unresolved-command-center-001"
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, external, _ = self.launched(tmp)
            external.unresolved_runs[TEAM_TASK_ID] = [
                SimpleNamespace(run_id=unresolved_id, started_at=NOW)
            ]

            first = integration.sync_job(JOB_ID)
            activity_count = len(service.activity(1_000))
            second = integration.sync_job(JOB_ID)

            self.assertTrue(first.changed)
            self.assertFalse(second.changed)
            self.assertEqual(first.job.status, JobStatus.AWAITING_APPROVAL)
            self.assertIn(unresolved_id, first.warnings[0])
            self.assertEqual(len(service.activity(1_000)), activity_count)
            unresolved_link = JobTeamIntegration.from_job(first.job).active_link
            self.assertEqual(unresolved_link.team_run_id, "")
            self.assertIn(unresolved_id, unresolved_link.synchronization_warnings[0])

            output = []
            handler = CommandCenterCommandHandler(
                service,
                integration=integration,
                output_provider=output.append,
            )
            handler.handle(f"job show {JOB_ID}")
            self.assertIn(unresolved_id, "\n".join(output))
            snapshot_job = service.snapshot()["jobs_awaiting_approval"][0]
            self.assertIn(unresolved_id, snapshot_job["warnings"][0])
            doctor_codes = {
                item["code"] for item in service.doctor()["issues"]
            }
            self.assertIn("integration.unresolved_team_run", doctor_codes)

            external.approvals[TEAM_TASK_ID] = [approval()]
            external.runs[RUN_ID] = run("executing")
            recovered = integration.sync_job(JOB_ID)
            recovered_link = JobTeamIntegration.from_job(
                recovered.job
            ).active_link
            self.assertEqual(recovered.job.status, JobStatus.RUNNING)
            self.assertEqual(recovered_link.team_run_id, RUN_ID)
            self.assertEqual(recovered_link.synchronization_warnings, ())
            self.assertIn(
                "job.sync_warning_cleared",
                recovered.activity_events,
            )

    def test_persisted_unresolved_run_reference_preserves_team_state(self):
        unresolved_id = "run-unresolved-persisted-001"
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, external, _ = self.launched(tmp)
            current = service.job(JOB_ID)
            stored = JobTeamIntegration.from_job(current)
            unresolved_link = replace(
                stored.active_link,
                team_run_id=unresolved_id,
            )
            metadata = dict(current.metadata)
            metadata["team_integration"] = stored.with_link(
                unresolved_link
            ).to_dict()
            service.synchronize_job(
                JOB_ID,
                status=current.status,
                current_stage=current.current_stage,
                progress=current.progress,
                approval_state=current.approval_state,
                metadata=metadata,
            )

            synchronized = integration.sync_job(JOB_ID)

            self.assertEqual(
                synchronized.job.status,
                JobStatus.AWAITING_APPROVAL,
            )
            self.assertEqual(
                JobTeamIntegration.from_job(
                    synchronized.job
                ).active_link.team_run_id,
                unresolved_id,
            )
            self.assertIn(unresolved_id, synchronized.warnings[0])
            self.assertNotIn("Codex", synchronized.warnings[0])

            external.approvals[TEAM_TASK_ID] = [approval()]
            external.runs[RUN_ID] = run("executing")
            recovered = integration.sync_job(JOB_ID).job
            self.assertEqual(
                JobTeamIntegration.from_job(recovered).active_link.team_run_id,
                RUN_ID,
            )

    def test_execution_testing_review_and_completion_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, external, _ = self.launched(tmp)
            external.approvals[TEAM_TASK_ID] = [approval()]
            integration.sync_job(JOB_ID)

            external.runs[RUN_ID] = run("executing")
            implementing = integration.sync_job(JOB_ID).job
            self.assertEqual(
                (implementing.status, implementing.current_stage, implementing.progress),
                (JobStatus.RUNNING, "implementation", 35),
            )

            external.runs[RUN_ID] = run("testing")
            testing = integration.sync_job(JOB_ID).job
            self.assertEqual(testing.current_stage, "testing")
            self.assertGreaterEqual(testing.progress, 70)

            external.runs[RUN_ID] = run(
                "awaiting_review",
                validation=SimpleNamespace(status="passed"),
                documentation=SimpleNamespace(status="passed"),
            )
            review = integration.sync_job(JOB_ID).job
            self.assertEqual(review.status, JobStatus.AWAITING_REVIEW)
            self.assertEqual(review.current_stage, "final_review")
            self.assertEqual(review.progress, 95)

            external.runs[RUN_ID] = run("completed")
            completed = integration.sync_job(JOB_ID).job
            self.assertEqual(completed.status, JobStatus.COMPLETED)
            self.assertEqual(completed.progress, 100)
            self.assertTrue(completed.completed_at)
            self.assertIsNone(JobTeamIntegration.from_job(completed).active_link)

    def test_failure_denial_and_rollback_are_safe_terminal_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, external, _ = self.launched(tmp)
            external.approvals[TEAM_TASK_ID] = [approval(state="denied")]
            denied = integration.sync_job(JOB_ID).job
            self.assertEqual(denied.status, JobStatus.FAILED)
            self.assertEqual(denied.approval_state, ApprovalState.DENIED)

        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, external, _ = self.launched(tmp)
            external.approvals[TEAM_TASK_ID] = [approval()]
            integration.sync_job(JOB_ID)
            external.runs[RUN_ID] = run(
                "failed",
                error="codex_process_failed",
            )
            failed = integration.sync_job(JOB_ID).job
            self.assertEqual(failed.status, JobStatus.FAILED)
            self.assertNotIn(str(Path(tmp)), failed.error_summary)

        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, external, _ = self.launched(tmp)
            external.approvals[TEAM_TASK_ID] = [approval()]
            integration.sync_job(JOB_ID)
            external.runs[RUN_ID] = run("rolled_back")
            rolled_back = integration.sync_job(JOB_ID).job
            self.assertEqual(rolled_back.status, JobStatus.CANCELLED)
            self.assertIn("rolled back", rolled_back.result_summary)

        with tempfile.TemporaryDirectory() as tmp:
            service, integration, team, _, _ = self.launched(tmp)
            team.tasks[TEAM_TASK_ID].status = "cancelled"
            cancelled = integration.sync_job(JOB_ID).job
            self.assertEqual(cancelled.status, JobStatus.CANCELLED)
            self.assertEqual(cancelled.approval_state, ApprovalState.CANCELLED)
            self.assertIn("Team task was cancelled", cancelled.result_summary)

    def test_repeated_sync_is_idempotent_and_does_not_duplicate_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, external, _ = self.launched(tmp)
            external.approvals[TEAM_TASK_ID] = [approval()]
            first = integration.sync_job(JOB_ID)
            count = len(service.activity(1_000))
            second = integration.sync_job(JOB_ID)
            self.assertTrue(first.changed)
            self.assertFalse(second.changed)
            self.assertEqual(len(service.activity(1_000)), count)

    def test_manual_status_progress_and_approval_cannot_bypass_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, external, _ = self.launched(tmp)
            with self.assertRaises(PermissionError):
                service.update_job_status(JOB_ID, "running")
            with self.assertRaises(PermissionError):
                service.update_job_progress(JOB_ID, 50)
            with self.assertRaises(PermissionError):
                service.resolve_job_approval(JOB_ID, "approved")

            external.approvals[TEAM_TASK_ID] = [approval()]
            integration.sync_job(JOB_ID)
            external.runs[RUN_ID] = run(
                "awaiting_review",
                validation=SimpleNamespace(status="passed"),
                documentation=SimpleNamespace(status="passed"),
            )
            integration.sync_job(JOB_ID)
            completed = service.update_job_status(JOB_ID, "completed")
            self.assertEqual(completed.status, JobStatus.COMPLETED)
            self.assertIsNone(JobTeamIntegration.from_job(completed).active_link)

    def test_cancellation_refuses_active_work_and_retains_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, team, _, _ = self.launched(tmp)
            team.tasks[TEAM_TASK_ID].status = "planning"
            with self.assertRaisesRegex(ValueError, "no safe cancellation"):
                integration.cancel(JOB_ID)
            self.assertIn(TEAM_TASK_ID, team.tasks)
            self.assertNotEqual(service.job(JOB_ID).status, JobStatus.CANCELLED)

        with tempfile.TemporaryDirectory() as tmp:
            service, integration, team, _, _ = self.launched(tmp)
            team.tasks[TEAM_TASK_ID].status = "awaiting_approval"
            cancelled = integration.cancel(JOB_ID)
            self.assertEqual(cancelled.status, JobStatus.CANCELLED)
            self.assertIn(TEAM_TASK_ID, team.tasks)

    def test_missing_linked_task_warns_without_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, team, _, _ = self.launched(tmp)
            del team.tasks[TEAM_TASK_ID]
            before = service.job(JOB_ID)
            result = integration.sync_job(JOB_ID)
            self.assertTrue(result.changed)
            self.assertEqual(result.job.status, before.status)
            self.assertIn("missing", result.warnings[0].lower())
            link = JobTeamIntegration.from_job(result.job).active_link
            self.assertEqual(link.external_status, "missing_team_task")


class CommandCenterObservabilityTests(unittest.TestCase, WorkflowFixture):
    def test_snapshot_v2_is_safe_deterministic_and_has_workflow_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, _, _ = self.build(tmp)
            job = self.create_job(service, tmp)
            integration.launch(job.job_id)

            first = service.snapshot()
            second = service.snapshot()

            self.assertEqual(first, second)
            self.assertEqual(first["schema_version"], 2)
            summary = first["jobs_awaiting_approval"][0]
            self.assertEqual(summary["active_agent_id"], "")
            self.assertEqual(summary["approval_state"], "pending")
            self.assertEqual(summary["team_link"]["team_task_id"], TEAM_TASK_ID)
            self.assertIn("next_action", summary)
            self.assertNotIn("goal", summary)
            self.assertNotIn("workspace_reference", summary)
            serialized = json.dumps(first)
            self.assertNotIn(str(Path(tmp).resolve()), serialized)
            department = first["departments"][0]
            self.assertEqual(department["awaiting_approval_count"], 1)
            self.assertEqual(first["workflow_summary"]["awaiting_approval"], 1)

    def test_doctor_detects_missing_task_stale_sync_and_required_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, team, _, manager = self.build(tmp)
            job = self.create_job(service, tmp)
            integration.launch(job.job_id)
            current = service.job(JOB_ID)
            stored = JobTeamIntegration.from_job(current)
            link = replace(stored.active_link, last_synced_at=OLD)
            metadata = dict(current.metadata)
            metadata["team_integration"] = stored.with_link(link).to_dict()
            service.synchronize_job(
                JOB_ID,
                status=current.status,
                current_stage=current.current_stage,
                progress=current.progress,
                approval_state=current.approval_state,
                metadata=metadata,
            )
            del team.tasks[TEAM_TASK_ID]
            del manager.agents["reviewer"]
            before = {
                path: path.read_bytes()
                for path in service.repository.root.rglob("*")
                if path.is_file()
            }

            report = service.doctor()

            after = {
                path: path.read_bytes()
                for path in service.repository.root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)
            codes = {item["code"] for item in report["issues"]}
            self.assertIn("integration.missing_team_task", codes)
            self.assertIn("integration.stale_sync", codes)
            self.assertIn("integration.missing_required_agent", codes)

    def test_unsupported_integration_schema_is_reported_not_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _, _, _, _ = self.build(tmp)
            job = self.create_job(service, tmp)
            metadata = dict(job.metadata)
            metadata["team_integration"] = {
                "integration_schema_version": 99,
                "active_team_task_id": "",
                "links": [],
            }
            corrupted = replace(job, metadata=metadata)
            service.repository.save_job(corrupted, overwrite=True)

            report = service.doctor()

            self.assertIn(
                "integration.unsupported_or_invalid_schema",
                {item["code"] for item in report["issues"]},
            )
            self.assertEqual(
                service.job(JOB_ID).metadata["team_integration"]
                ["integration_schema_version"],
                99,
            )

    def test_command_center_domain_has_no_provider_sdk_or_codex_import(self):
        root = Path(__file__).resolve().parents[1] / "orion" / "command_center"
        text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in root.glob("*.py")
        )
        self.assertNotIn("import openai", text.lower())
        self.assertNotIn("import google.generativeai", text.lower())
        self.assertNotIn("from orion.services.codex_bridge", text)


class CommandCenterWorkflowCliTests(unittest.TestCase, WorkflowFixture):
    def test_cli_dry_run_launch_sync_show_list_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, _, _ = self.build(tmp)
            job = self.create_job(service, tmp)
            output = []
            handler = CommandCenterCommandHandler(
                service,
                integration=integration,
                output_provider=output.append,
            )

            handler.handle(f"job launch {job.job_id} --dry-run")
            self.assertEqual(service.job(job.job_id).status, JobStatus.DRAFT)
            handler.handle(f"job launch {job.job_id}")
            handler.handle(f"job sync {job.job_id}")
            handler.handle(f"job show {job.job_id}")
            handler.handle("jobs")

            rendered = "\n".join(output)
            self.assertIn("Launch Preview", rendered)
            self.assertIn("Dry run made no changes", rendered)
            self.assertIn("Team Task", rendered)
            self.assertIn("Next Action", rendered)
            self.assertIn("awaiting_approval", rendered)
            output.clear()
            handler.handle(f"job show {job.job_id} --json")
            view = json.loads(output[-1])
            self.assertEqual(view["team_task_id"], TEAM_TASK_ID)
            self.assertNotIn(str(Path(tmp).resolve()), output[-1])

    def test_long_and_short_router_aliases_launch_same_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, integration, _, _, _ = self.build(tmp)
            first = self.create_job(service, tmp)
            second = self.create_job(
                service,
                tmp,
                job_id="job-command-center-002",
            )
            orion = SimpleNamespace(
                command_center=service,
                command_center_team=integration,
                agents=SimpleNamespace(is_production_agent_manager=False),
            )
            router = CommandRouter(orion)
            with patch("builtins.print") as output:
                self.assertTrue(
                    router.handle(f"cc job launch {first.job_id} --dry-run")
                )
                self.assertTrue(
                    router.handle(
                        f"command-center job launch {second.job_id} --dry-run"
                    )
                )
            rendered = "\n".join(
                str(item.args[0])
                for item in output.call_args_list
                if item.args
            )
            self.assertEqual(rendered.count("Command Center Launch Preview"), 2)


def replace_namespace(value, **changes):
    data = dict(vars(value))
    data.update(changes)
    return SimpleNamespace(**data)


class CommandCenterLinkModelTests(unittest.TestCase):
    def test_history_is_preserved_and_schema_is_versioned(self):
        assignment = WorkflowAgentAssignment(
            WorkflowStage.PLANNING,
            "planner",
            "Planner",
        )
        first = TeamIntegrationLink.create(
            team_task_id=TEAM_TASK_ID,
            workflow_id="engineering",
            linked_at=NOW,
            role_assignments=(assignment,),
        )
        integration = JobTeamIntegration.empty().with_link(first)
        inactive = replace(
            first,
            active=False,
            external_status="completed",
            next_action="No further action is required.",
        )
        integration = integration.with_link(inactive)
        second = TeamIntegrationLink.create(
            team_task_id="team-command-center-002",
            workflow_id="engineering",
            linked_at=NOW,
            role_assignments=(assignment,),
        )
        integration = integration.with_link(second)

        self.assertEqual(integration.integration_schema_version, 1)
        self.assertEqual(len(integration.links), 2)
        self.assertEqual(
            integration.active_team_task_id,
            "team-command-center-002",
        )
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            JobTeamIntegration.from_value({
                **integration.to_dict(),
                "integration_schema_version": 99,
            })


if __name__ == "__main__":
    unittest.main()
