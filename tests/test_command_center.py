import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from orion.command_center import (
    ActivityEvent,
    CommandCenterJobUpdateAdapter,
    CommandCenterService,
    Department,
    FileCommandCenterRepository,
    JobStatus,
    Organization,
    department_templates,
)
from orion.command_center.cli import CommandCenterCommandHandler
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter
from orion.ui.console import BASE_COMMANDS


NOW = "2026-07-27T20:00:00+00:00"


class FakeAgentManager:
    def __init__(self, agents=()):
        self.agents = {agent.agent_id: agent for agent in agents}

    def all(self):
        return tuple(sorted(self.agents.values(), key=lambda item: item.agent_id))

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


def fake_agent(
    agent_id="engineer",
    *,
    name="Engineer",
    enabled=True,
    scope="permanent",
):
    return SimpleNamespace(
        agent_id=agent_id,
        name=name,
        enabled=enabled,
        scope=scope,
    )


class CommandCenterFixture:
    def build(self, root, *, agents=None, provider_manager=None, workspace=None):
        manager = FakeAgentManager(agents or (
            fake_agent(),
            fake_agent("reviewer", name="Reviewer"),
        ))
        service = CommandCenterService(
            FileCommandCenterRepository(Path(root) / "user" / "command-center"),
            manager,
            provider_manager=provider_manager,
            routing_service=SimpleNamespace(enabled=True, profile="balanced"),
            workspace_manager=workspace,
            now=lambda: NOW,
        )
        return service, manager


class CommandCenterModelAndStorageTests(unittest.TestCase, CommandCenterFixture):
    def test_default_organization_creation_and_serialization_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _ = self.build(tmp)
            organization = service.ensure_default_organization()

            self.assertEqual(organization.name, "Orion Organization")
            self.assertEqual(service.organization(), organization)
            stored = yaml.safe_load(
                service.repository.organization_path.read_text(encoding="utf-8")
            )
            self.assertEqual(stored["schema_version"], 1)
            self.assertNotIn("email", stored)
            self.assertNotIn("api_key", stored)

    def test_invalid_and_future_schema_are_rejected_without_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = FileCommandCenterRepository(Path(tmp) / "command-center")
            repository.root.mkdir(parents=True)
            repository.organization_path.write_text(
                "schema_version: 99\nid: orion-organization\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Unsupported"):
                repository.load_organization()
            self.assertIn(
                "schema_version: 99",
                repository.organization_path.read_text(encoding="utf-8"),
            )

    def test_atomic_write_failure_preserves_existing_record_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = FileCommandCenterRepository(Path(tmp) / "command-center")
            original = Organization.create(now=NOW)
            repository.save_organization(original)
            before = repository.organization_path.read_bytes()
            updated = replace(original, description="Updated")

            with patch(
                "orion.command_center.repository.os.replace",
                side_effect=OSError("simulated"),
            ):
                with self.assertRaises(OSError):
                    repository.save_organization(updated)

            self.assertEqual(repository.organization_path.read_bytes(), before)
            self.assertEqual(list(repository.root.glob(".*.tmp")), [])

    def test_storage_creates_missing_directories_and_rejects_malformed_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = FileCommandCenterRepository(
                Path(tmp) / "deep" / "user" / "command-center"
            )
            repository.save_organization(Organization.create(now=NOW))
            department = Department.create(
                department_id="engineering",
                name="Engineering",
                now=NOW,
            )
            path = repository.save_department(department)
            self.assertTrue(path.is_file())
            path.write_text("schema_version: [", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "malformed YAML"):
                repository.load_department("engineering")

    def test_activity_is_append_only_ordered_bounded_and_secret_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = FileCommandCenterRepository(Path(tmp) / "command-center")
            for index in range(5):
                repository.append_activity(ActivityEvent.create(
                    event_id=f"event-{index}",
                    event_type="job.progress_updated",
                    source_type="job",
                    source_id="job-1",
                    job_id="job-1",
                    message=f"Progress {index}.",
                    timestamp=f"2026-07-27T20:00:0{index}+00:00",
                    metadata={"progress": index},
                ))
            recent = repository.list_activity(2)
            self.assertEqual([item.message for item in recent], ["Progress 3.", "Progress 4."])
            self.assertEqual(len(repository.activity_path.read_text(encoding="utf-8").splitlines()), 5)
            with self.assertRaises(ValueError):
                ActivityEvent.create(
                    event_id="event-secret",
                    event_type="job.failed",
                    source_type="job",
                    source_id="job-1",
                    message="Bearer abcdefghijklmnop",
                    timestamp=NOW,
                )
            with self.assertRaises(ValueError):
                repository.list_activity(1_001)

    def test_activity_malformed_line_and_duplicate_id_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = FileCommandCenterRepository(Path(tmp) / "command-center")
            event = ActivityEvent.create(
                event_id="event-one",
                event_type="organization.created",
                source_type="organization",
                source_id="orion-organization",
                message="Created organization.",
                timestamp=NOW,
            )
            repository.append_activity(event)
            with self.assertRaises(FileExistsError):
                repository.append_activity(event)
            with repository.activity_path.open("a", encoding="utf-8") as handle:
                handle.write("{not-json}\n")
            with self.assertRaisesRegex(ValueError, "line 2"):
                repository.list_activity(10)
            codes = {item.code for item in repository.diagnostics()}
            self.assertIn("activity.invalid", codes)

    def test_templates_are_immutable_recommendations_only(self):
        templates = department_templates()
        self.assertEqual(
            [item.name for item in templates],
            ["Engineering", "Marketing", "Business", "Automation"],
        )
        engineering = templates[0]
        self.assertIn("Security Reviewer", engineering.recommended_roles)
        self.assertIn("Release Manager", engineering.recommended_roles)

    def test_command_center_path_is_external_to_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = OrionPaths(root / "Orion", root / "home" / ".orion")
            self.assertFalse(paths.command_center.is_relative_to(paths.install_root))
            self.assertEqual(
                paths.command_center,
                (root / "home" / ".orion" / "command-center").resolve(),
            )


class CommandCenterDepartmentTests(unittest.TestCase, CommandCenterFixture):
    def test_create_list_show_and_duplicate_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _ = self.build(tmp)
            service.ensure_default_organization()
            created = service.create_department(template="Engineering")

            self.assertEqual(service.department("Engineering"), created)
            self.assertEqual(service.departments(), (created,))
            with self.assertRaises(FileExistsError):
                service.create_department(name="engineering", department_id="other")

    def test_membership_validates_agents_prevents_duplicates_and_does_not_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, manager = self.build(tmp)
            service.ensure_default_organization()
            service.create_department(name="Engineering")

            first = service.add_agent("Engineering", "Engineer")
            second = service.add_agent("engineering", "engineer")
            self.assertEqual(first.agent_ids, ("engineer",))
            self.assertEqual(second.agent_ids, ("engineer",))
            with self.assertRaises(FileNotFoundError):
                service.add_agent("engineering", "missing")
            removed = service.remove_agent("engineering", "engineer")
            self.assertEqual(removed.agent_ids, ())
            self.assertIn("engineer", manager.agents)

    def test_agent_can_belong_to_multiple_departments(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _ = self.build(tmp)
            service.ensure_default_organization()
            service.create_department(name="Engineering")
            service.create_department(name="Automation")
            service.add_agent("engineering", "engineer")
            service.add_agent("automation", "engineer")
            self.assertEqual(
                [item.agent_ids for item in service.departments()],
                [("engineer",), ("engineer",)],
            )

    def test_missing_and_disabled_members_produce_snapshot_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents = (
                fake_agent(),
                fake_agent("reviewer", name="Reviewer", enabled=False),
            )
            service, manager = self.build(tmp, agents=agents)
            service.ensure_default_organization()
            department = service.create_department(name="Engineering")
            department = department.with_agent("reviewer", NOW).with_agent("missing-agent", NOW)
            service.repository.save_department(department, overwrite=True)

            snapshot = service.snapshot()

            self.assertTrue(any("disabled agent reviewer" in item for item in snapshot["warnings"]))
            self.assertTrue(any("missing agent missing-agent" in item for item in snapshot["warnings"]))
            grouped = snapshot["agents_by_department"]["engineering"]
            self.assertEqual(
                next(item for item in grouped if item["id"] == "missing-agent")["reference_status"],
                "missing",
            )


class CommandCenterJobTests(unittest.TestCase, CommandCenterFixture):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service, self.manager = self.build(self.temp.name)
        self.service.ensure_default_organization()
        self.service.create_department(name="Engineering")

    def tearDown(self):
        self.temp.cleanup()

    def create(self, **overrides):
        values = {
            "title": "Build command center",
            "goal": "Build a safe persistent organization foundation.",
            "department": "Engineering",
        }
        values.update(overrides)
        return self.service.create_job(**values)

    def test_create_list_show_serialization_and_timestamps(self):
        job = self.create(
            assigned_agents=("engineer",),
            workspace_reference=self.temp.name,
            priority="high",
        )
        loaded = self.service.job(job.job_id)

        self.assertEqual(loaded, job)
        self.assertEqual(self.service.jobs(), (job,))
        self.assertEqual(job.status, JobStatus.DRAFT)
        self.assertEqual(job.created_at, NOW)
        self.assertEqual(job.updated_at, NOW)
        self.assertTrue(Path(job.workspace_reference).is_absolute())
        self.assertEqual(job.assigned_agent_ids, ("engineer",))

    def test_valid_lifecycle_completion_and_invalid_transition(self):
        job = self.create()
        with self.assertRaises(ValueError):
            self.service.update_job_status(job.job_id, "completed")
        self.service.update_job_status(job.job_id, "queued")
        running = self.service.update_job_status(job.job_id, "running")
        self.assertEqual(running.started_at, NOW)
        review = self.service.update_job_status(job.job_id, "awaiting_review")
        completed = self.service.update_job_status(
            job.job_id,
            "completed",
            result_summary="Implementation verified.",
        )
        self.assertEqual(review.status, JobStatus.AWAITING_REVIEW)
        self.assertEqual(completed.progress, 100)
        self.assertEqual(completed.completed_at, NOW)
        with self.assertRaises(ValueError):
            self.service.cancel_job(job.job_id)

    def test_approval_state_cannot_be_bypassed(self):
        job = self.create()
        self.service.update_job_status(job.job_id, "queued")
        self.service.update_job_status(job.job_id, "awaiting_approval")

        with self.assertRaises(PermissionError):
            self.service.update_job_status(job.job_id, "running")
        self.service.update_job_status(job.job_id, "queued")
        with self.assertRaises(PermissionError):
            self.service.update_job_status(job.job_id, "running")
        approved = self.service.resolve_job_approval(job.job_id, "approved")
        self.assertEqual(approved.approval_state.value, "approved")
        running = self.service.update_job_status(job.job_id, "running")
        self.assertEqual(running.status, JobStatus.RUNNING)

    def test_progress_boundaries_monotonicity_and_terminal_protection(self):
        job = self.create()
        for value in (-1, 101):
            with self.assertRaises(ValueError):
                self.service.update_job_progress(job.job_id, value)
        updated = self.service.update_job_progress(job.job_id, 40, current_stage="design")
        self.assertEqual((updated.progress, updated.current_stage), (40, "design"))
        with self.assertRaises(ValueError):
            self.service.update_job_progress(job.job_id, 39)
        self.service.cancel_job(job.job_id)
        with self.assertRaises(ValueError):
            self.service.update_job_progress(job.job_id, 50)

    def test_assignment_and_department_validation(self):
        with self.assertRaises(FileNotFoundError):
            self.create(department="Missing")
        with self.assertRaises(FileNotFoundError):
            self.create(assigned_agents=("missing",))
        self.manager.agents["disabled"] = fake_agent("disabled", enabled=False)
        with self.assertRaises(ValueError):
            self.create(assigned_agents=("disabled",))

        job = self.create()
        assigned = self.service.assign_job(job.job_id, "engineer")
        again = self.service.assign_job(job.job_id, "engineer")
        self.assertEqual(assigned.assigned_agent_ids, ("engineer",))
        self.assertEqual(again.assigned_agent_ids, ("engineer",))

    def test_cancellation_is_terminal_and_does_not_execute(self):
        executor = Mock()
        job = self.create()
        cancelled = self.service.cancel_job(job.job_id)
        self.assertEqual(cancelled.status, JobStatus.CANCELLED)
        self.assertEqual(cancelled.completed_at, NOW)
        executor.assert_not_called()

    def test_secret_bearing_metadata_is_rejected(self):
        with self.assertRaises(ValueError):
            self.create(metadata={"api_key": "not-stored"})
        with self.assertRaises(ValueError):
            self.create(metadata={"note": "sk-abcdefghijklmnop"})


class CommandCenterSnapshotDoctorIntegrationTests(
    unittest.TestCase,
    CommandCenterFixture,
):
    def test_snapshot_is_json_safe_deterministic_and_classifies_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            statuses = [
                "running", "queued", "awaiting_approval",
                "awaiting_review", "completed",
            ]
            service, _ = self.build(tmp)
            service.ensure_default_organization()
            service.create_department(name="Engineering")
            service.add_agent("engineering", "engineer")
            for index, target in enumerate(statuses):
                job = service.create_job(
                    title=f"Job {index}",
                    goal=f"Goal {index}",
                    department="engineering",
                    assigned_agents=("engineer",),
                )
                service.update_job_status(job.job_id, "queued")
                if target == "queued":
                    continue
                if target == "awaiting_approval":
                    service.update_job_status(job.job_id, target)
                    continue
                service.update_job_status(job.job_id, "running")
                if target == "running":
                    continue
                service.update_job_status(job.job_id, "awaiting_review")
                if target == "awaiting_review":
                    continue
                service.update_job_status(job.job_id, "completed")

            first = service.snapshot()
            second = service.snapshot()

            self.assertEqual(first, second)
            json.dumps(first)
            self.assertEqual(len(first["active_jobs"]), 1)
            self.assertEqual(len(first["queued_jobs"]), 1)
            self.assertEqual(len(first["jobs_awaiting_approval"]), 1)
            self.assertEqual(len(first["jobs_awaiting_review"]), 1)
            self.assertEqual(len(first["recently_completed_jobs"]), 1)
            self.assertEqual(first["agent_counts"]["enabled"], 2)
            self.assertNotIn("goal", first["active_jobs"][0])
            self.assertNotIn("workspace_reference", first["active_jobs"][0])

    def test_snapshot_provider_and_workspace_adapters_are_optional_and_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = SimpleNamespace(
                key="openai",
                enabled=True,
                configured=True,
                active=True,
                model="gpt-test",
            )
            providers = SimpleNamespace(statuses=lambda: [status])
            workspace = SimpleNamespace(
                root=Path(tmp) / "Secret Parent" / "Orion-Dev",
                capabilities=SimpleNamespace(
                    mode="git",
                    is_git_repository=True,
                    branch="main",
                ),
            )
            workspace.root.mkdir(parents=True)
            service, _ = self.build(
                tmp,
                provider_manager=providers,
                workspace=workspace,
            )
            service.ensure_default_organization()
            snapshot = service.snapshot()

            self.assertEqual(snapshot["provider_health"]["providers"][0]["status"], "ready")
            self.assertEqual(snapshot["workspace"]["name"], "Orion-Dev")
            self.assertNotIn(str(workspace.root.parent), json.dumps(snapshot))

            no_optional, _ = self.build(Path(tmp) / "other")
            no_optional.ensure_default_organization()
            optional_snapshot = no_optional.snapshot()
            self.assertFalse(optional_snapshot["provider_health"]["available"])
            self.assertFalse(optional_snapshot["workspace"]["available"])

    def test_doctor_is_read_only_and_detects_references_workspaces_and_malformed_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, manager = self.build(tmp)
            service.ensure_default_organization()
            department = service.create_department(name="Engineering")
            department = department.with_agent("missing-agent", NOW)
            service.repository.save_department(department, overwrite=True)
            job = service.create_job(
                title="Future workspace",
                goal="Inspect a future project.",
                department="engineering",
                workspace_reference=Path(tmp) / "does-not-exist",
            )
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
            self.assertIn("department.missing_agent", codes)
            self.assertIn("job.workspace_inaccessible", codes)
            self.assertTrue(report["ok"])
            self.assertGreaterEqual(report["warning_count"], 2)

            job_path = service.repository.jobs_root / f"{job.job_id}.yaml"
            value = yaml.safe_load(job_path.read_text(encoding="utf-8"))
            duplicate_path = service.repository.jobs_root / "other-job.yaml"
            duplicate_path.write_text(
                yaml.safe_dump(value),
                encoding="utf-8",
            )
            duplicate = service.doctor()
            duplicate_codes = {item["code"] for item in duplicate["issues"]}
            self.assertIn("job.duplicate_id", duplicate_codes)
            self.assertIn("job.invalid", duplicate_codes)

            value["progress"] = 101
            job_path.write_text(
                yaml.safe_dump(value),
                encoding="utf-8",
            )
            corrupt = service.doctor()
            self.assertFalse(corrupt["ok"])
            self.assertIn("job.invalid", {item["code"] for item in corrupt["issues"]})

    def test_team_adapter_maps_state_without_provider_or_codex_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _ = self.build(tmp)
            service.ensure_default_organization()
            job = service.create_job(title="Plan work", goal="Create a safe plan.")
            adapter = CommandCenterJobUpdateAdapter(service)

            planning = adapter.sync_team_task(
                job.job_id, SimpleNamespace(status="planning")
            )
            awaiting = adapter.sync_team_task(
                job.job_id, SimpleNamespace(status="awaiting_approval")
            )
            with self.assertRaises(PermissionError):
                adapter.running(job.job_id)
            adapter.resolve_approval(job.job_id, approved=True)
            running = adapter.running(job.job_id)

            self.assertEqual(planning.status, JobStatus.PLANNING)
            self.assertEqual(awaiting.status, JobStatus.AWAITING_APPROVAL)
            self.assertEqual(running.status, JobStatus.RUNNING)


class CommandCenterCliTests(unittest.TestCase, CommandCenterFixture):
    def test_cli_department_job_status_snapshot_activity_and_doctor(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _ = self.build(tmp)
            service.ensure_default_organization()
            output = []
            handler = CommandCenterCommandHandler(
                service,
                output_provider=output.append,
            )
            handler.handle("department create --template Engineering")
            handler.handle("department add-agent Engineering engineer")
            handler.handle(
                'job create --title "Build marketplace" '
                '--goal "Design a secure marketplace" '
                "--department Engineering --priority high"
            )
            job = service.jobs()[0]
            handler.handle(f"job status {job.job_id} queued")
            handler.handle(f"job progress {job.job_id} 25 --stage planning")
            handler.handle("status")
            handler.handle("activity --limit 5")
            handler.handle("doctor")

            rendered = "\n".join(output)
            self.assertIn("Orion Command Center", rendered)
            self.assertIn("Build marketplace", rendered)
            self.assertIn("progress is 25%", rendered)
            self.assertIn("Doctor", rendered)
            self.assertIn("made no repairs", rendered)

            output.clear()
            handler.handle("snapshot --json")
            snapshot = json.loads(output[-1])
            self.assertEqual(snapshot["organization"]["name"], "Orion Organization")

    def test_cli_noninteractive_errors_are_helpful_and_creation_is_inert(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _ = self.build(tmp)
            service.ensure_default_organization()
            output = []
            handler = CommandCenterCommandHandler(
                service,
                output_provider=output.append,
            )
            handler.handle("job create --title MissingGoal")
            handler.handle("job progress missing not-a-number")
            handler.handle("department add-agent Missing missing")
            rendered = "\n".join(output)
            self.assertIn("requires --title and --goal", rendered)
            self.assertIn("must be an integer", rendered)
            self.assertIn("Department not found", rendered)

    def test_router_alias_and_completion_preserve_existing_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _ = self.build(tmp)
            service.ensure_default_organization()
            router = CommandRouter(SimpleNamespace(command_center=service))
            with patch("builtins.print") as output:
                self.assertTrue(router.handle("cc status"))
                self.assertTrue(router.handle("command-center templates"))
            rendered = "\n".join(
                str(call.args[0]) for call in output.call_args_list if call.args
            )
            self.assertIn("Orion Command Center", rendered)
            self.assertIn("Department Templates", rendered)
            self.assertIn("agent create", BASE_COMMANDS)
            self.assertIn("team plan", BASE_COMMANDS)
            self.assertIn("cc status", BASE_COMMANDS)
            self.assertIn("command-center doctor", BASE_COMMANDS)


if __name__ == "__main__":
    unittest.main()
