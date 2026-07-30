import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from orion.application.capabilities import (
    CapabilityDefinition,
    CapabilityRegistry,
    default_capability_registry,
)
from orion.application.commands.command_center_commands import (
    CommandCenterApplicationHandler,
)
from orion.application.results import ApplicationResult
from orion.command_center.integrations import LaunchPreview, LaunchResult
from orion.command_center.models import Job
from orion.core.paths import OrionPaths
from orion.interfaces.cli.renderer import ApplicationResultRenderer


NOW = "2026-07-30T12:00:00+00:00"


class FakeCommandCenterService:
    def __init__(self):
        self.records = {}

    def create_job(self, **values):
        job = Job.create(
            job_id=values.get("job_id") or "job-application-001",
            title=values["title"],
            goal=values["goal"],
            priority=values.get("priority", "normal"),
            department_id=values.get("department", ""),
            assigned_agent_ids=values.get("assigned_agents", ()),
            workspace_reference=values.get("workspace_reference", ""),
            created_by=values.get("created_by", "user"),
            now=NOW,
        )
        self.records[job.job_id] = job
        return job

    def job(self, job_id):
        if job_id not in self.records:
            raise FileNotFoundError(f"Job not found: {job_id}")
        return self.records[job_id]

    def activity(self, _limit):
        return ()


class FakeIntegration:
    def __init__(self, preview):
        self.preview = preview
        self.launch_calls = []

    def preview_launch(self, job_id, *, workflow="", workspace=""):
        return self.preview

    def launch(self, job_id, *, workflow="", workspace=""):
        self.launch_calls.append((job_id, workflow, workspace))
        return LaunchResult(
            job=self.service.job(job_id),
            team_task_id="team-application-001",
            preview=self.preview,
        )

    def describe_next_action(self, _job_id):
        return "Approve the AI Team plan before implementation."


def preview(*, allowed=True, errors=()):
    return LaunchPreview(
        job_id="job-application-001",
        allowed=allowed,
        workflow=None,
        department_name="Engineering",
        workspace_root="" if errors else "C:/workspace/project",
        provider_routes=(),
        approval_required=True,
        intended_team_task_type="software_delivery",
        expected_stages=("planning", "awaiting_approval"),
        execution_engine="codex",
        warnings=(),
        errors=errors,
    )


class ApplicationResultTests(unittest.TestCase):
    def test_success_serialization_warnings_next_actions_and_immutability(self):
        source = {"items": [{"id": 1}]}
        result = ApplicationResult.success(
            "Ready",
            data=source,
            warnings=("Review recommended.",),
            next_actions=("Continue",),
        )
        source["items"].append({"id": 2})

        self.assertEqual(result.status, "warning")
        self.assertEqual(result.to_dict()["data"], {"items": [{"id": 1}]})
        self.assertEqual(json.loads(result.to_json())["next_actions"], ["Continue"])
        with self.assertRaises(TypeError):
            result.data["extra"] = True
        with self.assertRaises(FrozenInstanceError):
            result.status = "failure"

    def test_failure_errors_and_independent_defaults(self):
        first = ApplicationResult.failure("No", errors=("Denied",))
        second = ApplicationResult.success("Yes")
        self.assertEqual(first.errors, ("Denied",))
        self.assertEqual(second.errors, ())
        self.assertEqual(second.data, {})
        with self.assertRaises(ValueError):
            ApplicationResult("unknown", "")
        with self.assertRaises(TypeError):
            ApplicationResult.success("Bad", data={"service": object()})
        with self.assertRaises(TypeError):
            ApplicationResult.success("Bad", data={"number": float("nan")})
        with self.assertRaises(TypeError):
            ApplicationResult.success("Bad", data={1: "not a string key"})


class CapabilityRegistryTests(unittest.TestCase):
    def test_registration_duplicate_lookup_listing_and_serialization(self):
        definition = CapabilityDefinition(
            "example.inspect",
            "Inspect an example.",
            False,
            False,
            ("example.read",),
            {"type": "object"},
            {"type": "object"},
        )
        registry = CapabilityRegistry((definition,))
        self.assertIs(registry.lookup("example.inspect"), definition)
        self.assertEqual(registry.list(), (definition,))
        self.assertEqual(
            json.loads(json.dumps(registry.to_dict()))["capabilities"][0]
            ["capability_id"],
            "example.inspect",
        )
        with self.assertRaises(ValueError):
            registry.register(definition)
        with self.assertRaises(KeyError):
            registry.lookup("example.missing")

    def test_default_ids_are_stable_and_representative(self):
        ids = tuple(
            item.capability_id
            for item in default_capability_registry().list()
        )
        self.assertEqual(ids, tuple(sorted(ids, key=str.casefold)))
        self.assertEqual(len(ids), len(set(ids)))
        for capability_id in (
            "command_center.job.create",
            "command_center.job.preview",
            "command_center.job.launch",
            "command_center.job.show",
            "agent.create",
            "team.approve",
            "workspace.inspect",
            "application.open",
        ):
            self.assertIn(capability_id, ids)


class CommandCenterApplicationHandlerTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeCommandCenterService()

    def test_create_and_show_return_structured_results(self):
        handler = CommandCenterApplicationHandler(self.service)
        created = handler.handle(
            'job create --title "Build core" --goal "Stabilize Orion"'
        )
        shown = handler.handle("job show job-application-001")

        self.assertEqual(created.status, "success")
        self.assertEqual(
            created.to_dict()["data"]["result"]["id"],
            "job-application-001",
        )
        self.assertIn("did not start planning", created.message)
        self.assertEqual(shown.status, "success")
        self.assertEqual(
            shown.to_dict()["data"]["result"]["status"],
            "draft",
        )
        self.assertIn("Launch the job when ready.", shown.next_actions)

    def test_preview_approval_launch_and_unresolved_workspace(self):
        job = self.service.create_job(
            title="Build core",
            goal="Stabilize Orion",
        )
        integration = FakeIntegration(preview())
        integration.service = self.service
        handler = CommandCenterApplicationHandler(
            self.service,
            integration=integration,
        )

        dry_run = handler.handle(f"job launch {job.job_id} --dry-run")
        launched = handler.handle(f"job launch {job.job_id}")
        self.assertEqual(dry_run.status, "success")
        self.assertTrue(
            dry_run.to_dict()["data"]["result"]["approval_required"]
        )
        self.assertIn("Dry run made no changes", dry_run.message)
        self.assertEqual(launched.status, "success")
        self.assertIn("Approve the AI Team plan", launched.next_actions[0])

        blocked_integration = FakeIntegration(
            preview(
                allowed=False,
                errors=("Workspace could not be resolved safely.",),
            )
        )
        blocked = CommandCenterApplicationHandler(
            self.service,
            integration=blocked_integration,
        ).handle(f"job launch {job.job_id} --dry-run")
        self.assertEqual(blocked.status, "failure")
        self.assertIn("Workspace could not be resolved safely.", blocked.errors)

    def test_invalid_job_and_service_errors_map_to_failures(self):
        handler = CommandCenterApplicationHandler(self.service)
        missing = handler.handle("job show missing-job")
        self.assertEqual(missing.status, "failure")
        self.assertIn("Job not found", missing.errors[0])

        class BrokenService(FakeCommandCenterService):
            def create_job(self, **_values):
                raise OSError("storage unavailable")

        broken = CommandCenterApplicationHandler(BrokenService()).handle(
            'job create --title "Build" --goal "Test errors"'
        )
        self.assertEqual(broken.status, "failure")
        self.assertIn("storage unavailable", broken.message)


class CliRendererTests(unittest.TestCase):
    def test_renderer_displays_message_details_and_unique_diagnostics(self):
        output = []
        renderer = ApplicationResultRenderer(output.append)
        renderer.render(ApplicationResult.success("Completed", data={"count": 2}))
        renderer.render(
            ApplicationResult.failure(
                "",
                data={"reason": "denied"},
                errors=("Denied",),
                next_actions=("Review approval",),
            )
        )
        rendered = "\n".join(output)
        self.assertIn("Completed", rendered)
        self.assertIn('"reason": "denied"', rendered)
        self.assertIn("Error: Denied", rendered)
        self.assertIn("Next: Review approval", rendered)


class RuntimeBoundaryTests(unittest.TestCase):
    def test_default_user_data_is_home_not_repository_local_orion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = root / "repository"
            home = root / "home"
            (repository / ".orion").mkdir(parents=True)
            home.mkdir()
            with patch.object(Path, "home", return_value=home), patch.dict(
                os.environ,
                {},
                clear=True,
            ):
                paths = OrionPaths(install_root=repository)
            self.assertEqual(paths.user_root, (home / ".orion").resolve())
            self.assertNotEqual(paths.user_root, (repository / ".orion").resolve())

    def test_runtime_and_development_artifacts_are_ignored(self):
        repository = Path(__file__).resolve().parents[1]
        ignored = (repository / ".gitignore").read_text(encoding="utf-8")
        for entry in (
            ".orion/",
            ".vs/",
            ".venv/",
            "__pycache__/",
            ".pytest_cache/",
            "*.env",
        ):
            self.assertIn(entry, ignored)
        self.assertTrue((repository / "tests" / "fixtures" / "README.md").is_file())


if __name__ == "__main__":
    unittest.main()
