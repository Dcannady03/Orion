import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from orion.core.router import CommandRouter
from orion.services.codex_bridge import (
    CodexBridge, CodexBridgeStore, CodexCLICapabilities, CodexProcessResult, CodexRun,
)
from orion.services.execution_engines import ENGINE_STATUS_INSTALLED, ExecutionEngine
from orion.services.team import TEAM_STATUS_AWAITING_APPROVAL, RoleOutput, TeamArtifact, TeamTask, TeamTaskStore
from orion.services.team_roles import ResolvedTeamRole, ROLE_SPEC_BY_NAME, TeamRoleSnapshot
from orion.services.team_validation import (
    AutomaticValidationService,
    BoundedValidationRunner,
    ValidationProcessResult,
    ValidationRequest,
)
from orion.services.workspace import WorkspaceCapabilities
from orion.services.workspace_snapshot import SnapshotLimits, WorkspaceSnapshotService


class FlatConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeValidationRunner:
    def __init__(self, results=None, mutator=None, error=None):
        self.results = list(results or [])
        self.mutator = mutator
        self.error = error
        self.calls = []

    def run(self, command, *, cwd, temp_root, timeout, max_output_bytes):
        self.calls.append((tuple(command), Path(cwd), Path(temp_root), timeout, max_output_bytes))
        if self.error:
            raise self.error
        if self.mutator:
            self.mutator(Path(cwd), len(self.calls))
        if self.results:
            return self.results.pop(0)
        return ValidationProcessResult(0, False, 0.01, 40, 4 if "unittest" in command else None)


class StaticCapabilities:
    OPTIONS = frozenset({
        "--ask-for-approval", "--cd", "--config", "--ephemeral", "--ignore-user-config",
        "--json", "--output-schema", "--sandbox", "--skip-git-repo-check", "--strict-config",
    })

    def detect(self, executable):
        return CodexCLICapabilities(str(executable), self.OPTIONS)


class ImplementationRunner:
    def __init__(self, result, mutator):
        self.result = result
        self.mutator = mutator
        self.calls = []

    def run(self, command, *, cwd, prompt, timeout):
        self.calls.append(tuple(command))
        self.mutator(Path(cwd))
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(self.result)},
        }
        return CodexProcessResult(0, json.dumps(event) + "\n", "")


class TesterRoles:
    def __init__(self, engine, *, available=True):
        self._engine = engine
        self._available = available

    def status(self, role, *, prompt=""):
        return ResolvedTeamRole(
            spec=ROLE_SPEC_BY_NAME["tester"],
            requested_assignment="codex",
            actual_assignment="codex",
            source="default",
            available=self._available,
            fallback="none (fail closed)",
            fallback_reason="" if self._available else "Codex is unavailable.",
            engine_id="codex",
        )

    def engine(self, role):
        if not self._available:
            raise ValueError("Codex is unavailable")
        return self._engine


class AutomaticValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = FlatConfig({
            "codex_bridge.snapshot_max_files": 1_000,
            "codex_bridge.snapshot_max_file_bytes": 1_000_000,
            "codex_bridge.snapshot_max_total_bytes": 10_000_000,
            "codex_bridge.diff_max_bytes": 100_000,
            "team.validation.command_timeout_seconds": 30,
            "team.validation.max_output_bytes": 10_000,
        })
        self.engine = ExecutionEngine(
            engine_id="codex",
            name="Codex CLI",
            status=ENGINE_STATUS_INSTALLED,
            installed=True,
            cli_support=True,
            implementation_supported=True,
            executable="codex.cmd",
        )

    @staticmethod
    def _write(root, relative, content):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    def request(
        self,
        root,
        *,
        before=None,
        after=None,
        runner=None,
        tester_available=True,
        goal="Implement the approved change",
        plan_steps=("Implement the approved change",),
    ):
        workspace = Path(root) / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".git").mkdir()
        before = before or {}
        after = after or {}
        for relative, content in before.items():
            self._write(workspace, relative, content)
        snapshots = WorkspaceSnapshotService()
        service = AutomaticValidationService(
            self.config,
            snapshot_service=snapshots,
            runner=runner or FakeValidationRunner(),
            now=lambda: datetime(2026, 7, 19, 18, 0, tzinfo=timezone.utc),
        )
        protected = service.protected_state(workspace)
        blob_root = Path(root) / "artifacts" / "snapshot" / "blobs"
        capabilities = WorkspaceCapabilities.detect(workspace, which=lambda _name: None)
        baseline = snapshots.capture(
            capabilities,
            blob_root,
            SnapshotLimits.from_config(self.config),
            created_at="2026-07-19T17:59:00+00:00",
        )
        for relative, content in after.items():
            path = workspace / relative
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                self._write(workspace, relative, content)
        changes, _ = snapshots.compare(baseline, blob_root, SnapshotLimits.from_config(self.config))
        result = {
            "summary": "Implemented.",
            "files_changed": [
                {"path": item.path, "summary": f"{item.kind.title()} file."}
                for item in changes.changes
            ],
            "tests": [{"command": "reported", "status": "passed", "summary": "reported"}],
            "risks": [],
            "remaining_work": [],
            "review_notes": [],
        }
        tester = TeamRoleSnapshot(
            role="tester",
            display_name="Tester",
            category="Validation role (execution engine)",
            requested_assignment="codex",
            actual_assignment="codex",
            available=tester_available,
            capability="Bounded local test execution",
            fallback="none (fail closed)",
            fallback_reason="" if tester_available else "Codex is unavailable.",
            source="default",
        )
        request = ValidationRequest(
            attempt_id="validation-0001",
            run_id="run-validation-001",
            team_task_id="team-validation-001",
            approval_id="approval-validation-001",
            workspace=capabilities,
            active_workspace=str(workspace.resolve()),
            changes=changes,
            implementation_result=result,
            plan_goal=goal,
            plan_steps=plan_steps,
            tester=tester,
            execution_engine=self.engine if tester_available else None,
            baseline=baseline,
            blob_root=blob_root,
            protected_baseline=protected,
            artifact_paths=("validation/validation-0001.json", "validation/validation-0001.log"),
        )
        return service, request, workspace

    def test_json_validation_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(tmp, after={"config/settings.json": '{"ready": true}\n'})
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "passed")
            self.assertEqual(next(item for item in attempt.checks if item.check_id == "json_syntax").status, "passed")
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(tmp, after={"config/settings.json": '{"ready": }\n'})
            attempt = service.validate(request)
            check = next(item for item in attempt.checks if item.check_id == "json_syntax")
            self.assertEqual(attempt.status, "failed")
            self.assertIn("line 1", check.summary)

    def test_yaml_validation_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(tmp, after={"config/settings.yaml": "ready: true\n"})
            self.assertEqual(service.validate(request).status, "passed")
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(tmp, after={"config/settings.yaml": "ready: [\n"})
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "failed")
            self.assertEqual(next(item for item in attempt.checks if item.check_id == "yaml_syntax").status, "failed")

    def test_toml_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(tmp, after={"pyproject.toml": '[tool.orion]\nready = true\n'})
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "passed")
            self.assertEqual(next(item for item in attempt.checks if item.check_id == "toml_syntax").status, "passed")
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(tmp, after={"pyproject.toml": "[tool.orion\nready = true\n"})
            self.assertEqual(service.validate(request).status, "failed")

    def test_plan_documentation_requirement_without_markdown_is_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(
                tmp,
                after={"settings.json": "{}\n"},
                goal="Update documentation for the setting",
            )
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "warnings")
            self.assertEqual(
                next(item for item in attempt.checks if item.check_id == "documentation_expected").status,
                "warning",
            )

    def test_markdown_structure_and_missing_local_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(
                tmp,
                after={"docs/guide.md": "# Guide\n\n[Missing](missing.md)\n"},
            )
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "warnings")
            self.assertEqual(next(item for item in attempt.checks if item.check_id == "markdown_structure").status, "passed")
            self.assertEqual(next(item for item in attempt.checks if item.check_id == "markdown_links").status, "warning")

    def test_markdown_unclosed_fence_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(tmp, after={"README.md": "# Guide\n\n```python\nprint('x')\n"})
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "failed")

    def test_python_compile_and_targeted_test_discovery(self):
        runner = FakeValidationRunner()
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(
                tmp,
                before={"tests/test_widget.py": "import unittest\n"},
                after={"orion/services/widget.py": "VALUE = 1\n"},
                runner=runner,
            )
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "passed")
            self.assertEqual(len(runner.calls), 2)
            self.assertTrue(all(item.exit_code == 0 for item in attempt.commands))
            self.assertIn("py_compile", runner.calls[0][0])
            self.assertIn("tests.test_widget", runner.calls[1][0])
            self.assertEqual(next(item for item in attempt.checks if item.check_id == "python_full_suite").status, "skipped")

    def test_python_full_suite_fallback_when_target_is_unknown(self):
        runner = FakeValidationRunner()
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(
                tmp,
                before={"tests/test_known.py": "import unittest\n"},
                after={"orion/services/unknown_feature.py": "VALUE = 1\n"},
                runner=runner,
            )
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "passed")
            self.assertIn("discover", runner.calls[1][0])
            self.assertIsNotNone(next(item for item in attempt.checks if item.check_id == "python_full_tests"))

    def test_broad_python_change_selects_full_suite(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeValidationRunner()
            service, request, _ = self.request(
                tmp,
                before={"tests/test_router.py": "import unittest\n"},
                after={"orion/core/router.py": "VALUE = 1\n"},
                runner=runner,
            )
            service.validate(request)
            self.assertIn("discover", runner.calls[1][0])

    def test_expected_created_and_deleted_files_are_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(
                tmp,
                before={"obsolete.json": "{}\n"},
                after={"obsolete.json": None, "new.json": "{}\n"},
            )
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "passed")
            self.assertIn("new.json", attempt.files_inspected)
            self.assertIn("obsolete.json", attempt.files_inspected)

    def test_changed_or_missing_implementation_file_fails_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, workspace = self.request(tmp, after={"new.json": "{}\n"})
            (workspace / "new.json").write_text('{"changed": true}\n', encoding="utf-8")
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "failed")
            self.assertEqual(attempt.checks[0].status, "failed")

    def test_validation_failure_does_not_remove_or_roll_back_implementation(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, workspace = self.request(tmp, after={"broken.json": '{"bad": }\n'})
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "failed")
            self.assertTrue((workspace / "broken.json").is_file())

    def test_unexpected_tester_workspace_write_is_detected(self):
        def mutate(workspace, _count):
            (workspace / "orion/services/widget.py").write_text("VALUE = 2\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeValidationRunner(mutator=mutate)
            service, request, _ = self.request(
                tmp,
                before={"tests/test_widget.py": "import unittest\n"},
                after={"orion/services/widget.py": "VALUE = 1\n"},
                runner=runner,
            )
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "failed")
            self.assertEqual(next(item for item in attempt.checks if item.check_id == "tester_read_only").status, "failed")

    def test_protected_workspace_write_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, workspace = self.request(tmp, after={"new.json": "{}\n"})
            (workspace / ".git" / "index").write_text("changed", encoding="utf-8")
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "failed")
            self.assertEqual(next(item for item in attempt.checks if item.check_id == "protected_workspace").status, "failed")

    def test_unavailable_tester_fails_closed_without_commands(self):
        runner = FakeValidationRunner()
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(
                tmp,
                after={"new.json": "{}\n"},
                runner=runner,
                tester_available=False,
            )
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "unavailable")
            self.assertEqual(runner.calls, [])
            self.assertEqual(attempt.review_status, "Validation Unavailable")

    def test_workspace_mismatch_aborts_before_tester(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(tmp, after={"new.json": "{}\n"})
            request = ValidationRequest(**{**request.__dict__, "active_workspace": str(Path(tmp) / "other")})
            with self.assertRaises(PermissionError):
                service.validate(request)

    def test_validation_timeout_is_an_error_with_no_raw_output(self):
        runner = FakeValidationRunner(results=[ValidationProcessResult(None, True, 30.0, 0)])
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(tmp, after={"orion/services/widget.py": "VALUE = 1\n"}, runner=runner)
            attempt = service.validate(request)
            self.assertEqual(attempt.status, "error")
            self.assertTrue(attempt.commands[0].timed_out)
            self.assertNotIn("stdout", json.dumps(attempt.to_dict()).lower())

    def test_output_limit_and_secrets_are_redacted(self):
        secret = "sk-thismustneverpersist123456"
        runner = FakeValidationRunner(results=[ValidationProcessResult(1, False, 0.1, 50_000)])
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _ = self.request(tmp, after={"orion/services/widget.py": "VALUE = 1\n"}, runner=runner)
            attempt = service.validate(request)
            errored = service.error(request, f"Provider token {secret} was rejected")
            payload = json.dumps({"attempt": attempt.to_dict(), "error": errored.to_dict()})
            self.assertEqual(attempt.status, "error")
            self.assertNotIn(secret, payload)
            self.assertIn("[REDACTED]", payload)
            self.assertNotIn("environment", payload.lower())

    def test_real_compile_leaves_no_workspace_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, workspace = self.request(
                tmp,
                after={"orion/services/widget.py": "VALUE = 1\n"},
                runner=None,
            )
            service.runner = BoundedValidationRunner()
            attempt = service.validate(request)
            self.assertNotEqual(attempt.status, "failed")
            self.assertEqual(list(workspace.rglob("__pycache__")), [])

    def test_real_tester_blocks_workspace_vault_outside_network_git_and_nested_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            (workspace / "tests").mkdir(parents=True)
            (workspace / "vault").mkdir()
            (workspace / "source.txt").write_text("original\n", encoding="utf-8")
            secret = "sk-never-readable-validation-secret"
            (workspace / "vault/vault.yaml").write_text(secret, encoding="utf-8")
            (workspace / "tests/test_guard.py").write_text(
                """import os
import socket
import subprocess
import unittest
from pathlib import Path

class GuardTests(unittest.TestCase):
    def test_boundaries(self):
        workspace = Path(os.environ['ORION_VALIDATION_WORKSPACE'])
        temporary = Path(os.environ['ORION_VALIDATION_TEMP'])
        with self.assertRaises(PermissionError):
            (workspace / 'source.txt').write_text('changed', encoding='utf-8')
        with self.assertRaises(PermissionError):
            (workspace / 'vault/vault.yaml').read_text(encoding='utf-8')
        with self.assertRaises(PermissionError):
            (temporary.parent / 'outside.txt').write_text('outside', encoding='utf-8')
        with self.assertRaises(PermissionError):
            subprocess.run(['git', 'status'])
        with self.assertRaises(PermissionError):
            socket.create_connection(('127.0.0.1', 9))
""",
                encoding="utf-8",
            )
            temp_root = root / "validation-temp"
            temp_root.mkdir()
            result = BoundedValidationRunner().run(
                (str(Path(__import__('sys').executable)), "-m", "unittest", "tests.test_guard"),
                cwd=workspace,
                temp_root=temp_root,
                timeout=30,
                max_output_bytes=10_000,
            )
            self.assertEqual(result.exit_code, 0)
            self.assertEqual((workspace / "source.txt").read_text(encoding="utf-8"), "original\n")
            self.assertFalse((root / "outside.txt").exists())
            self.assertEqual((workspace / "vault/vault.yaml").read_text(encoding="utf-8"), secret)


class ValidationWorkflowTests(unittest.TestCase):
    def bridge(self, root, *, validation_runner=None, tester_available=True):
        root = Path(root)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / ".git").mkdir()
        capabilities = WorkspaceCapabilities.detect(workspace, which=lambda _name: None)
        store = TeamTaskStore(root / "user/team/tasks")
        task = TeamTask(
            task_id="team-validation-001",
            goal="Add valid JSON configuration",
            status=TEAM_STATUS_AWAITING_APPROVAL,
            artifacts=[TeamArtifact(
                role="engineer",
                kind="engineering_review",
                output=RoleOutput("Ready", ("Add settings.json",), (), "Approve"),
                created_at="2026-07-19T17:00:00+00:00",
            )],
            final_plan=["Add settings.json"],
            created_at="2026-07-19T17:00:00+00:00",
            updated_at="2026-07-19T17:00:00+00:00",
        )
        store.save(task)
        engine = ExecutionEngine(
            "codex", "Codex CLI", ENGINE_STATUS_INSTALLED, True, True, True,
            executable=str(root / "codex.cmd"),
        )
        result = {
            "summary": "Added JSON configuration.",
            "files_changed": [{"path": "settings.json", "summary": "Added settings."}],
            "tests": [{"command": "json parse", "status": "passed", "summary": "Valid."}],
            "risks": [], "remaining_work": [], "review_notes": [],
        }
        implementation = ImplementationRunner(
            result,
            lambda cwd: (cwd / "settings.json").write_text('{"ready": true}\n', encoding="utf-8"),
        )
        validation = AutomaticValidationService(
            FlatConfig(),
            runner=validation_runner or FakeValidationRunner(),
            now=lambda: datetime(2026, 7, 19, 18, 0, tzinfo=timezone.utc),
        )
        bridge = CodexBridge(
            FlatConfig(),
            store,
            CodexBridgeStore(root / "user/codex"),
            workspace,
            workspace_capabilities=capabilities,
            runner=implementation,
            capability_detector=StaticCapabilities(),
            team_roles=TesterRoles(engine, available=tester_available),
            validation_service=validation,
            default_execution_engine=engine,
            now=lambda: datetime(2026, 7, 19, 18, 0, tzinfo=timezone.utc),
            approval_id_factory=lambda: "approval-validation-001",
            run_id_factory=lambda: "run-validation-001",
            platform_name="nt",
        )
        return bridge, workspace

    def test_automatic_validation_runs_after_successful_implementation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _ = self.bridge(tmp)
            approval = bridge.approve("team-validation-001")
            run = bridge.execute("team-validation-001", approval.approval_id)
            self.assertEqual(run.status, "awaiting_review")
            self.assertEqual(run.validation.status, "passed")
            self.assertEqual(run.validation.tester_requested, "codex")
            self.assertEqual(run.validation.tester_resolved, "codex")
            self.assertEqual(run.validation.execution_engine, "codex")
            self.assertGreaterEqual(run.validation.duration_seconds, 0)
            self.assertEqual(len(run.validation_history), 1)
            self.assertTrue((bridge.store.run_directory(run.run_id) / run.validation_history[0]).is_file())

    def test_repeated_team_test_preserves_history_and_does_not_reuse_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _ = self.bridge(tmp)
            approval = bridge.approve("team-validation-001")
            first = bridge.execute("team-validation-001", approval.approval_id)
            claim = bridge.store.approval_claim_path(first.team_task_id, first.approval_id).read_text(encoding="utf-8")
            second = bridge.validate(first.run_id)
            self.assertEqual(len(second.validation_history), 2)
            self.assertNotEqual(second.validation_history[0], second.validation_history[1])
            self.assertEqual(
                bridge.store.approval_claim_path(first.team_task_id, first.approval_id).read_text(encoding="utf-8"),
                claim,
            )

    def test_team_test_last_and_explicit_run_use_same_validation_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _ = self.bridge(tmp)
            approval = bridge.approve("team-validation-001")
            run = bridge.execute("team-validation-001", approval.approval_id)
            router = CommandRouter(SimpleNamespace(codex_bridge=bridge))
            with patch("builtins.print") as output:
                router.handle("team test last")
                router.handle(f"team test {run.run_id}")
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Automatic Validation", rendered)
            self.assertIn("Awaiting Review", rendered)
            self.assertEqual(len(bridge.run(run.run_id).validation_history), 3)

    def test_missing_implementation_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _ = self.bridge(tmp)
            approval = bridge.approve("team-validation-001")
            run = bridge.execute("team-validation-001", approval.approval_id)
            (bridge.store.run_directory(run.run_id) / "implementation-result.json").unlink()
            with self.assertRaisesRegex(ValueError, "missing implementation artifacts"):
                bridge.validate(run.run_id)

    def test_rolled_back_run_is_rejected_and_validation_history_remains(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _ = self.bridge(tmp)
            approval = bridge.approve("team-validation-001")
            run = bridge.execute("team-validation-001", approval.approval_id)
            rolled_back = bridge.rollback(run.run_id)
            self.assertEqual(len(rolled_back.validation_history), 1)
            with self.assertRaisesRegex(ValueError, "Rolled-back"):
                bridge.validate(run.run_id)

    def test_unavailable_tester_records_unavailable_and_starts_no_validation_command(self):
        runner = FakeValidationRunner()
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _ = self.bridge(tmp, validation_runner=runner, tester_available=False)
            approval = bridge.approve("team-validation-001")
            run = bridge.execute("team-validation-001", approval.approval_id)
            self.assertEqual(run.validation.status, "unavailable")
            self.assertEqual(runner.calls, [])

    def test_existing_v2_run_without_validation_fields_remains_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _ = self.bridge(tmp)
            approval = bridge.approve("team-validation-001")
            run = bridge.execute("team-validation-001", approval.approval_id)
            legacy = run.to_dict()
            legacy.pop("validation")
            legacy.pop("validation_history")
            restored = CodexRun.from_value(legacy)
            self.assertIsNone(restored.validation)
            self.assertEqual(restored.validation_history, ())

    def test_workspace_mismatch_rejects_validation_before_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, workspace = self.bridge(tmp)
            approval = bridge.approve("team-validation-001")
            run = bridge.execute("team-validation-001", approval.approval_id)
            other = Path(tmp) / "other"
            other.mkdir()
            bridge.bind(other, WorkspaceCapabilities.detect(other, which=lambda _name: None))
            with self.assertRaises(PermissionError):
                bridge.validate(run.run_id)

    def test_help_exposes_validation_commands(self):
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("team test <run-id>", rendered)
        self.assertIn("team test last", rendered)

    def test_required_documentation_references_validation_commands_and_safety(self):
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "README.md", "CHANGELOG.md", "docs/USER_GUIDE.md", "docs/AI_TEAM.md",
            "docs/CODEX_BRIDGE.md", "docs/EXECUTION_ENGINES.md",
        ):
            text = (root / relative).read_text(encoding="utf-8")
            self.assertIn("team test <run-id>", text, relative)
            self.assertIn("Validation", text, relative)
        guide = (root / "docs/USER_GUIDE.md").read_text(encoding="utf-8")
        self.assertIn("never accepts or rolls back", guide)
        self.assertIn("read-only toward implementation files", guide)


if __name__ == "__main__":
    unittest.main()
