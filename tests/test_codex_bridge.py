import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orion.core.router import CommandRouter
from orion.services.codex_bridge import (
    RUN_STATUS_AWAITING_REVIEW,
    RUN_STATUS_FAILED,
    CodexBridge,
    CodexBridgeError,
    CodexBridgeStore,
    CodexCLICapabilities,
    CodexCLICapabilityDetector,
    CodexCLICompatibilityError,
    CodexProcessResult,
    ExecutionContext,
    LocalCodexRunner,
    PlanSnapshot,
)
from orion.services.execution_engines import (
    ENGINE_STATUS_INSTALLED,
    ExecutionEngine,
    ExecutionEngineService,
    ExecutionEngineUnavailable,
)
from orion.services.team import (
    TEAM_STATUS_AWAITING_APPROVAL,
    TEAM_STATUS_FAILED,
    RoleOutput,
    TeamArtifact,
    TeamTask,
    TeamTaskStore,
)
from orion.services.workspace import WorkspaceCapabilities
from orion.services.workspace_snapshot import WorkspaceRollbackError, WorkspaceSnapshotError


class FlatConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeRunner:
    def __init__(self, result=None, error=None, mutator=None):
        self.result = result or CodexProcessResult(0, valid_jsonl(), "")
        self.error = error
        self.mutator = mutator
        self.calls = []

    def run(self, command, *, cwd, prompt, timeout):
        self.calls.append({
            "command": tuple(command),
            "cwd": Path(cwd),
            "prompt": prompt,
            "timeout": timeout,
        })
        if self.error is not None:
            raise self.error
        if self.mutator is not None:
            self.mutator(Path(cwd), len(self.calls))
        elif self.result.returncode == 0:
            self._apply_reported_changes(Path(cwd), len(self.calls))
        return self.result

    def _apply_reported_changes(self, workspace, sequence):
        try:
            events = [json.loads(line) for line in self.result.stdout.splitlines() if line.strip()]
            messages = [
                event["item"]["text"]
                for event in events
                if event.get("type") == "item.completed"
                and isinstance(event.get("item"), dict)
                and event["item"].get("type") == "agent_message"
            ]
            result = json.loads(messages[-1])
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return
        for item in result.get("files_changed", []):
            relative = Path(str(item.get("path", "")))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                continue
            if any(part.lower() in {".git", ".codex", ".agents"} for part in relative.parts):
                continue
            target = (workspace / relative).resolve()
            try:
                target.relative_to(workspace.resolve())
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"bounded change {sequence}\n", encoding="utf-8")


class WorkspaceWriteSimulatingRunner(FakeRunner):
    """Model the writable boundary encoded by Orion's Codex command."""

    def __init__(self, outside_target):
        result = implementation_result(files_changed=[
            {"path": "approved.txt", "summary": "Created inside the approved workspace."},
        ])
        super().__init__(CodexProcessResult(0, valid_jsonl(result), ""))
        self.outside_target = Path(outside_target).resolve()
        self.outside_blocked = False

    def run(self, command, *, cwd, prompt, timeout):
        command = tuple(command)
        workspace = Path(command[command.index("--cd") + 1]).resolve()
        if command[command.index("--sandbox") + 1] != "workspace-write":
            raise PermissionError("Workspace-write sandbox was not selected.")
        if workspace != Path(cwd).resolve():
            raise PermissionError("Codex --cd and subprocess workspace differ.")
        if "--add-dir" in command or any(
            item.startswith("sandbox_workspace_write.writable_roots=")
            for item in command
        ):
            raise PermissionError("Unexpected additional writable roots were granted.")
        if 'approval_policy="never"' not in command:
            raise PermissionError("Noninteractive Codex approval policy was not fixed.")
        if 'sandbox_mode="workspace-write"' not in command:
            raise PermissionError("Workspace-write compatibility config was not fixed.")
        if 'windows.sandbox="elevated"' not in command:
            raise PermissionError("Elevated native Windows sandbox was not selected.")

        self.calls.append({
            "command": command,
            "cwd": Path(cwd),
            "prompt": prompt,
            "timeout": timeout,
        })
        (workspace / "approved.txt").write_text("approved workspace write\n", encoding="utf-8")
        try:
            self.outside_target.relative_to(workspace)
        except ValueError:
            self.outside_blocked = True
        else:
            self.outside_target.write_text("unexpected outside write\n", encoding="utf-8")
        return self.result


class StaticCapabilityDetector:
    DEFAULT_OPTIONS = frozenset({
        "--ask-for-approval",
        "--cd",
        "--config",
        "--ephemeral",
        "--ignore-user-config",
        "--json",
        "--output-schema",
        "--sandbox",
        "--skip-git-repo-check",
        "--strict-config",
    })

    def __init__(self, options=None):
        self.options = self.DEFAULT_OPTIONS if options is None else frozenset(options)
        self.calls = []

    def detect(self, executable):
        self.calls.append(str(executable))
        return CodexCLICapabilities(str(executable), self.options)


def implementation_result(**updates):
    value = {
        "summary": "Implemented the approved plan and stopped for review.",
        "files_changed": [
            {"path": "orion/services/example.py", "summary": "Added the bounded service."},
        ],
        "tests": [
            {"command": "python -m unittest tests.test_example", "status": "passed", "summary": "3 tests passed."},
        ],
        "risks": ["Reviewer should verify the new boundary."],
        "remaining_work": [],
        "review_notes": ["No Git actions were performed."],
    }
    value.update(updates)
    return value


def valid_jsonl(result=None):
    final = json.dumps(result or implementation_result())
    events = [
        {"type": "thread.started", "thread_id": "thread-test-001"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "item-1", "type": "agent_message", "text": final}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 20}},
    ]
    return "".join(json.dumps(item) + "\n" for item in events)


class CodexBridgeTests(unittest.TestCase):
    def build(
        self,
        root,
        *,
        runner=None,
        config=None,
        task_status=TEAM_STATUS_AWAITING_APPROVAL,
        execution_engines=None,
        capability_detector=None,
        include_default_engine=True,
        git_workspace=True,
        platform_name="nt",
    ):
        base = Path(root)
        workspace = base / "workspace"
        workspace.mkdir(parents=True)
        if git_workspace:
            (workspace / ".git").mkdir()
        capabilities = WorkspaceCapabilities.detect(workspace, which=lambda _name: None)
        team_store = TeamTaskStore(base / "user" / "team" / "tasks")
        task = TeamTask(
            task_id="team-test-001",
            goal="Implement a bounded Codex bridge",
            status=task_status,
            artifacts=[TeamArtifact(
                role="engineer",
                kind="engineering_review",
                output=RoleOutput(
                    summary="Approved implementation shape",
                    recommendations=("Add a bridge service", "Add strict tests"),
                    risks=("Keep execution workspace-confined",),
                    next_action="Await approval",
                ),
                created_at="2026-07-18T12:00:00+00:00",
            )],
            final_plan=["Add a bridge service", "Add strict tests"],
            created_at="2026-07-18T12:00:00+00:00",
            updated_at="2026-07-18T12:00:00+00:00",
            error="" if task_status != TEAM_STATUS_FAILED else "Engineer role failed (RuntimeError).",
        )
        team_store.save(task)
        approvals = iter([f"approval-test-{index:03d}" for index in range(1, 20)])
        runs = iter([f"run-test-{index:03d}" for index in range(1, 20)])
        default_engine = ExecutionEngine(
            engine_id="codex",
            name="Codex CLI",
            status=ENGINE_STATUS_INSTALLED,
            installed=True,
            cli_support=True,
            implementation_supported=True,
            executable=str((base / "tools" / "codex.cmd").resolve()),
        )
        if execution_engines is None:
            execution_engines = Mock()
            execution_engines.require_codex.return_value = default_engine
        bridge = CodexBridge(
            FlatConfig(config),
            team_store,
            CodexBridgeStore(base / "user" / "codex"),
            workspace,
            workspace_capabilities=capabilities,
            runner=runner or FakeRunner(),
            capability_detector=capability_detector or StaticCapabilityDetector(),
            execution_engines=execution_engines,
            default_execution_engine=default_engine if include_default_engine else None,
            now=lambda: datetime(2026, 7, 18, 13, 0, tzinfo=timezone.utc),
            approval_id_factory=lambda: next(approvals),
            run_id_factory=lambda: next(runs),
            platform_name=platform_name,
        )
        if isinstance(execution_engines, Mock):
            execution_engines.status.return_value = [default_engine]
        return bridge, team_store, workspace

    def interactive_router(self, root, answers, *, runner=None):
        bridge, team_store, workspace = self.build(root, runner=runner)
        team = Mock()
        team.plan.return_value = team_store.load("team-test-001")
        prompt = Mock(side_effect=answers)
        router = CommandRouter(
            SimpleNamespace(
                team=team,
                codex_bridge=bridge,
                execution_engines=bridge.execution_engines,
            ),
            interactive_team_approval=True,
            team_approval_input=prompt,
        )
        return router, bridge, team, prompt, workspace

    def test_approval_persists_immutable_plan_hash_outside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, team_store, workspace = self.build(tmp)
            task = team_store.load("team-test-001")
            expected_hash = PlanSnapshot.from_team_task(task).hash

            approval = bridge.approve(task.task_id, actor="owner")

            self.assertEqual(approval.plan_hash, expected_hash)
            self.assertEqual(approval.workspace_root, str(workspace.resolve()))
            self.assertEqual(approval.execution_engine, "codex")
            self.assertEqual(approval.approved_scope, "active_workspace")
            self.assertEqual(approval.expected_operation, "implement")
            self.assertEqual(approval.approved_by, "owner")
            approval_path = bridge.store.approval_path(task.task_id, approval.approval_id)
            self.assertTrue(approval_path.is_file())
            self.assertNotEqual(approval_path.parents[2], workspace)
            self.assertEqual(bridge.store.load_approval(task.task_id, approval.approval_id), approval)
            with self.assertRaises(FileExistsError):
                bridge.store.save_approval(approval)

    def test_plan_hash_is_deterministic_and_changes_with_implementation_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, team_store, _ = self.build(tmp)
            task = team_store.load("team-test-001")
            first = PlanSnapshot.from_team_task(task)
            second = PlanSnapshot.from_value(first.to_dict())
            self.assertEqual(first.hash, second.hash)

            task.final_plan.append("Update documentation")
            changed = PlanSnapshot.from_team_task(task)
            self.assertNotEqual(first.hash, changed.hash)
            self.assertEqual(bridge.runner.calls, [])

    def test_approval_requires_awaiting_plan_and_accepts_standard_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, workspace = self.build(tmp, task_status=TEAM_STATUS_FAILED)
            with self.assertRaises(ValueError):
                bridge.approve("team-test-001")

            bridge, _, workspace = self.build(Path(tmp) / "second")
            (workspace / ".git").rmdir()
            bridge.bind(workspace)
            approval = bridge.approve("team-test-001")
            self.assertEqual(approval.workspace.mode, "standard")

    def test_bridge_rejects_artifact_storage_inside_active_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".git").mkdir()
            team_store = TeamTaskStore(root / "user" / "team" / "tasks")
            bridge = CodexBridge(
                FlatConfig(),
                team_store,
                CodexBridgeStore(workspace / ".orion" / "codex"),
                workspace,
            )
            with self.assertRaises(ValueError):
                bridge.approve("team-test-001")

    def test_successful_execution_is_confined_structured_and_awaiting_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, workspace = self.build(tmp, runner=runner)
            approval = bridge.approve("team-test-001")

            run = bridge.execute("team-test-001", approval.approval_id)

            self.assertEqual(run.status, RUN_STATUS_AWAITING_REVIEW)
            self.assertEqual(run.result.tests[0].status, "passed")
            self.assertEqual(run.result.files_changed[0].path, "orion/services/example.py")
            self.assertEqual(run.workspace.mode, "git")
            self.assertEqual(run.changes.by_kind("created")[0].path, "orion/services/example.py")
            self.assertEqual(len(runner.calls), 1)
            call = runner.calls[0]
            command = call["command"]
            self.assertEqual(call["cwd"], workspace.resolve())
            self.assertEqual(call["timeout"], 1800)
            self.assertEqual(Path(command[0]).name.lower(), "codex.cmd")
            self.assertEqual(command[1:4], ("exec", "--json", "--ephemeral"))
            self.assertIn("workspace-write", command)
            self.assertIn('web_search="disabled"', command)
            self.assertIn("mcp_servers={}", command)
            self.assertIn("features.apps=false", command)
            self.assertIn("features.hooks=false", command)
            self.assertIn("features.multi_agent=false", command)
            self.assertIn("features.remote_plugin=false", command)
            self.assertIn('approval_policy="never"', command)
            self.assertIn('sandbox_mode="workspace-write"', command)
            self.assertIn('windows.sandbox="elevated"', command)
            self.assertIn("sandbox_workspace_write.network_access=false", command)
            self.assertIn("sandbox_workspace_write.exclude_tmpdir_env_var=true", command)
            self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", command)
            self.assertFalse(any(
                item.startswith("sandbox_workspace_write.writable_roots=")
                for item in command
            ))
            self.assertIn("--output-schema", command)
            self.assertEqual(command[command.index("--sandbox") + 1], "workspace-write")
            self.assertEqual(command[command.index("--cd") + 1], str(workspace.resolve()))
            self.assertEqual(command[-1], "-")
            self.assertNotIn("--skip-git-repo-check", command)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
            self.assertNotIn("--add-dir", command)
            self.assertIn("--ask-for-approval", command)
            self.assertEqual(command[command.index("--ask-for-approval") + 1], "never")
            self.assertIn(approval.plan_hash, call["prompt"])
            self.assertIn("Do not create or switch branches", call["prompt"])
            self.assertIn("Do not invoke web search", call["prompt"])
            self.assertIn("intentionally writable", call["prompt"])
            self.assertIn("file-edit/patch tool", call["prompt"])
            self.assertIn("do not infer that the workspace is read-only", call["prompt"])

            directory = bridge.store.run_directory(run.run_id)
            self.assertEqual(
                {path.name for path in directory.iterdir()},
                {
                    "approved-plan.json", "result-schema.json", "events.jsonl",
                    "implementation-result.json", "workspace-baseline.json",
                    "workspace-changes.json", "workspace.diff", "run.json", "snapshot",
                },
            )
            claim_path = bridge.store.approval_claim_path(run.team_task_id, run.approval_id)
            claim = json.loads(claim_path.read_text(encoding="utf-8"))
            self.assertEqual(claim["run_id"], run.run_id)
            with self.assertRaises(FileExistsError):
                bridge.store.claim_approval(
                    run.team_task_id,
                    run.approval_id,
                    "run-test-999",
                    "2026-07-18T13:01:00+00:00",
                )
            self.assertEqual(bridge.run(run.run_id), run)

    def test_codex_01445_help_is_parsed_and_cached_without_approval_flag(self):
        help_text = """Run Codex non-interactively
Options:
  -c, --config <key=value>
      --strict-config
  -s, --sandbox <SANDBOX_MODE>
  -C, --cd <DIR>
      --skip-git-repo-check
      --ephemeral
      --ignore-user-config
      --output-schema <FILE>
      --json
  -h, --help
"""
        help_runner = Mock()
        help_runner.run_help.return_value = CodexProcessResult(0, help_text, "")
        detector = CodexCLICapabilityDetector(help_runner, timeout=2.0)
        CodexCLICapabilityDetector.clear_cache()
        executable = str((Path(tempfile.gettempdir()) / "codex-01445.cmd").resolve())
        try:
            first = detector.detect(executable)
            second = detector.detect(executable)
        finally:
            CodexCLICapabilityDetector.clear_cache()
        self.assertIs(first, second)
        self.assertTrue(first.supports("--sandbox"))
        self.assertTrue(first.supports("--ephemeral"))
        self.assertFalse(first.supports("--ask-for-approval"))
        help_runner.run_help.assert_called_once_with(executable, timeout=2.0)

    def test_01445_command_omits_unsupported_approval_flag_and_keeps_supported_options(self):
        detector = StaticCapabilityDetector(
            StaticCapabilityDetector.DEFAULT_OPTIONS - {"--ask-for-approval"}
        )
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, _ = self.build(
                tmp,
                runner=runner,
                capability_detector=detector,
            )
            approval = bridge.approve("team-test-001")
            run = bridge.execute("team-test-001", approval.approval_id)
            command = runner.calls[0]["command"]
            self.assertNotIn("--ask-for-approval", command)
            self.assertIn("--ephemeral", command)
            self.assertIn("--sandbox", command)
            self.assertIn("--ignore-user-config", command)
            self.assertIn("--strict-config", command)
            self.assertIn('approval_policy="never"', command)
            self.assertIn('sandbox_mode="workspace-write"', command)
            self.assertIn('windows.sandbox="elevated"', command)
            self.assertEqual(run.diagnostics.exit_code, 0)
            self.assertFalse(run.diagnostics.timed_out)
            capabilities = detector.detect(command[0])
            command_plan = bridge._command(
                Path(command[0]),
                bridge.store.run_directory(run.run_id) / "result-schema.json",
                run.workspace,
                capabilities,
                bridge.workspace_root,
            )
            self.assertIn("--sandbox", command_plan.required_security_arguments)
            self.assertIn("--ephemeral", command_plan.optional_compatibility_arguments)
            self.assertIn("--ask-for-approval", command_plan.unsupported_arguments)

    def test_non_windows_command_does_not_emit_native_windows_sandbox_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, _ = self.build(tmp, runner=runner, platform_name="posix")
            approval = bridge.approve("team-test-001")

            bridge.execute("team-test-001", approval.approval_id)

            self.assertNotIn('windows.sandbox="elevated"', runner.calls[0]["command"])

    def test_missing_required_security_option_aborts_before_claim_or_workspace_change(self):
        detector = StaticCapabilityDetector(
            StaticCapabilityDetector.DEFAULT_OPTIONS - {"--sandbox"}
        )
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, workspace = self.build(
                tmp,
                runner=runner,
                capability_detector=detector,
            )
            approval = bridge.approve("team-test-001")
            with self.assertRaises(CodexCLICompatibilityError) as raised:
                bridge.execute("team-test-001", approval.approval_id)
            self.assertEqual(raised.exception.missing_required, ("--sandbox",))
            self.assertEqual(runner.calls, [])
            self.assertFalse(bridge.store.approval_used(approval.team_task_id, approval.approval_id))
            self.assertFalse((workspace / "orion").exists())
            self.assertFalse((bridge.store.root / "runs").exists())

    def test_cli_argument_error_is_sanitized_and_records_no_workspace_changes(self):
        stderr = "error: unexpected argument '--ask-for-approval' found\nSECRET_TOKEN=hidden"
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner(CodexProcessResult(2, "RAW_STDOUT_SECRET", stderr))
            bridge, _, workspace = self.build(tmp, runner=runner)
            approval = bridge.approve("team-test-001")
            with self.assertRaises(CodexBridgeError) as raised:
                bridge.execute("team-test-001", approval.approval_id)
            self.assertEqual(raised.exception.category, "codex_cli_argument_error")
            failed = bridge.run(raised.exception.run_id)
            self.assertEqual(failed.error, "codex_cli_argument_error")
            self.assertEqual(failed.diagnostics.exit_code, 2)
            self.assertFalse(failed.diagnostics.timed_out)
            self.assertEqual(failed.diagnostics.unsupported_argument, "--ask-for-approval")
            self.assertEqual(
                failed.diagnostics.safe_stderr_summary,
                "Codex rejected an unsupported command-line argument.",
            )
            self.assertEqual(failed.changes.changes, ())
            self.assertFalse((workspace / "orion").exists())
            run_document = json.loads(
                bridge.store.run_path(failed.run_id).read_text(encoding="utf-8")
            )
            self.assertEqual(set(run_document["diagnostics"]), {
                "exit_code",
                "timed_out",
                "resolved_executable",
                "safe_stderr_summary",
                "unsupported_argument",
            })
            persisted = "".join(
                path.read_text(encoding="utf-8")
                for path in bridge.store.run_directory(failed.run_id).iterdir()
                if path.is_file()
            )
            self.assertNotIn("SECRET_TOKEN", persisted)
            self.assertNotIn("RAW_STDOUT_SECRET", persisted)
            self.assertNotIn("unexpected argument", persisted)

    def test_utf8_mojibake_is_repaired_before_result_persistence(self):
        result = implementation_result(summary="Orionâ€™s compatibility fix is ready.")
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner(CodexProcessResult(0, valid_jsonl(result), ""))
            bridge, team_store, _ = self.build(
                tmp,
                runner=runner,
            )
            task = team_store.load("team-test-001")
            task.goal = "Verify Orionâ€™s UTF-8 persistence."
            team_store.save(task)
            approval = bridge.approve("team-test-001")
            run = bridge.execute("team-test-001", approval.approval_id)
            self.assertEqual(run.result.summary, "Orion’s compatibility fix is ready.")
            persisted = "".join((
                bridge.store.run_path(run.run_id).read_text(encoding="utf-8"),
                bridge.store.read_run_artifact(run.run_id, "approved-plan.json"),
                runner.calls[0]["prompt"],
            ))
            self.assertIn("Verify Orion’s UTF-8 persistence.", persisted)
            self.assertIn("Orion’s compatibility fix is ready.", persisted)
            self.assertNotIn("Orionâ€™s", persisted)

    def test_standard_workspace_approves_executes_and_uses_narrow_codex_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, workspace = self.build(tmp, runner=runner, git_workspace=False)

            approval = bridge.approve("team-test-001")
            run = bridge.execute("team-test-001", approval.approval_id)

            self.assertEqual(approval.workspace.mode, "standard")
            self.assertEqual(run.workspace.mode, "standard")
            command = runner.calls[0]["command"]
            self.assertIn("--skip-git-repo-check", command)
            self.assertEqual(command[command.index("--sandbox") + 1], "workspace-write")
            self.assertEqual(command[command.index("--cd") + 1], str(workspace.resolve()))
            self.assertEqual(runner.calls[0]["cwd"], workspace.resolve())
            self.assertFalse((workspace / ".git").exists())
            self.assertEqual(
                [item.path for item in run.changes.by_kind("created")],
                ["orion/services/example.py"],
            )
            diff = bridge.store.read_run_artifact(run.run_id, "workspace.diff")
            self.assertIn("--- /dev/null", diff)
            self.assertIn("+++ b/orion/services/example.py", diff)

    def test_git_workspace_subdirectory_keeps_execution_bounded_to_active_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, workspace = self.build(tmp, runner=runner, git_workspace=False)
            repository = Path(tmp).resolve()
            (repository / "outside-active.txt").write_text("outside", encoding="utf-8")
            capabilities = WorkspaceCapabilities(
                root=str(workspace.resolve()),
                mode="git",
                is_git_repository=True,
                git_root=str(repository),
                branch="feature/test",
                commit="b" * 40,
                supports_git_diff=True,
                supports_git_commands=True,
            )
            bridge.bind(workspace, capabilities)

            approval = bridge.approve("team-test-001")
            run = bridge.execute("team-test-001", approval.approval_id)

            self.assertEqual(run.workspace.git_root, str(repository))
            self.assertEqual(run.workspace.branch, "feature/test")
            command = runner.calls[0]["command"]
            self.assertNotIn("--skip-git-repo-check", command)
            self.assertEqual(command[command.index("--sandbox") + 1], "workspace-write")
            self.assertEqual(command[command.index("--cd") + 1], str(workspace.resolve()))
            self.assertEqual(runner.calls[0]["cwd"], workspace.resolve())
            baseline = bridge.store.read_run_artifact(run.run_id, "workspace-baseline.json")
            self.assertNotIn("outside-active.txt", baseline)

    def test_simulated_workspace_write_allows_active_file_and_blocks_outside(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside-workspace.txt"
            runner = WorkspaceWriteSimulatingRunner(outside)
            bridge, _, workspace = self.build(tmp, runner=runner, git_workspace=False)

            approval = bridge.approve("team-test-001")
            run = bridge.execute("team-test-001", approval.approval_id)

            self.assertEqual(run.status, RUN_STATUS_AWAITING_REVIEW)
            self.assertEqual(
                (workspace / "approved.txt").read_text(encoding="utf-8"),
                "approved workspace write\n",
            )
            self.assertTrue(runner.outside_blocked)
            self.assertFalse(outside.exists())
            self.assertEqual(
                [item.path for item in run.changes.by_kind("created")],
                ["approved.txt"],
            )

    def test_interactive_yes_approves_exact_plan_and_starts_one_implementation(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            router, bridge, team, prompt, workspace = self.interactive_router(
                tmp,
                ["yes"],
                runner=runner,
            )

            with patch("builtins.print") as output:
                router.handle('team plan "Implement the approved bridge"')

            approval = bridge.store.load_approval("team-test-001", "approval-test-001")
            task = bridge.team_store.load("team-test-001")
            self.assertEqual(approval.plan_hash, PlanSnapshot.from_team_task(task).hash)
            self.assertEqual(approval.workspace_root, str(workspace.resolve()))
            self.assertEqual(approval.execution_engine, "codex")
            self.assertTrue(bridge.store.approval_used(task.task_id, approval.approval_id))
            self.assertEqual(len(runner.calls), 1)
            prompt.assert_called_once_with("> ")
            team.plan.assert_called_once_with("Implement the approved bridge")
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Approve this exact plan?", rendered)
            self.assertIn("Approval ID: approval-test-001", rendered)
            self.assertIn(f"Plan SHA-256: {approval.plan_hash}", rendered)
            self.assertIn(f"Workspace: {workspace.resolve()}", rendered)
            self.assertIn("Execution Engine: Codex CLI (codex)", rendered)
            self.assertIn("Status: Awaiting Review", rendered)
            with self.assertRaises(PermissionError):
                bridge.execute(
                    task.task_id,
                    approval.approval_id,
                    execution_engine=bridge._default_execution_engine,
                )

    def test_interactive_no_launches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, bridge, _, prompt, _ = self.interactive_router(tmp, ["n"])
            with patch("builtins.print") as output:
                router.handle('team plan "Do not approve"')
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Plan not approved. No implementation was performed.", rendered)
            self.assertEqual(bridge.runner.calls, [])
            self.assertFalse(bridge.store.approval_path(
                "team-test-001", "approval-test-001"
            ).exists())
            prompt.assert_called_once_with("> ")

    def test_interactive_details_show_boundaries_and_prompt_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, bridge, _, prompt, workspace = self.interactive_router(tmp, ["d", "n"])
            with patch("builtins.print") as output:
                router.handle('team plan "Inspect details"')
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertEqual(rendered.count("Approve this exact plan?"), 2)
            self.assertIn("AI Team Approval Details", rendered)
            self.assertIn("Final Plan:", rendered)
            self.assertIn("Risks:", rendered)
            self.assertIn("Keep execution workspace-confined", rendered)
            self.assertIn(f"Workspace: {workspace.resolve()}", rendered)
            self.assertIn("Execution Engine: Codex CLI (codex)", rendered)
            self.assertIn("Sandbox Mode: workspace-write", rendered)
            self.assertIn("Expected Permissions:", rendered)
            self.assertEqual(prompt.call_count, 2)
            self.assertEqual(bridge.runner.calls, [])

    def test_interactive_empty_input_cancels_without_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, bridge, _, prompt, _ = self.interactive_router(tmp, [""])
            with patch("builtins.print") as output:
                router.handle('team plan "Empty is not approval"')
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("No approval recorded", rendered)
            self.assertEqual(prompt.call_count, 1)
            self.assertEqual(bridge.runner.calls, [])

    def test_interactive_invalid_input_prompts_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, bridge, _, prompt, _ = self.interactive_router(tmp, ["approve it", "no"])
            with patch("builtins.print") as output:
                router.handle('team plan "Require explicit approval"')
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Please enter Y, N, or D", rendered)
            self.assertEqual(prompt.call_count, 2)
            self.assertEqual(bridge.runner.calls, [])

    def test_interactive_ctrl_c_cancels_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            router, bridge, _, prompt, _ = self.interactive_router(
                tmp,
                KeyboardInterrupt(),
            )
            with patch("builtins.print") as output:
                router.handle('team plan "Cancel safely"')
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Approval cancelled", rendered)
            self.assertEqual(prompt.call_count, 1)
            self.assertEqual(bridge.runner.calls, [])

    def test_interactive_implementation_failure_keeps_review_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner(CodexProcessResult(9, "", "private failure detail"))
            router, bridge, _, _, _ = self.interactive_router(tmp, ["y"], runner=runner)
            with patch("builtins.print") as output:
                router.handle('team plan "Preserve failure artifacts"')
            failed = bridge.run("run-test-001")
            directory = bridge.store.run_directory(failed.run_id)
            self.assertEqual(failed.status, RUN_STATUS_FAILED)
            self.assertTrue((directory / "run.json").is_file())
            self.assertTrue((directory / "workspace-baseline.json").is_file())
            self.assertTrue((directory / "workspace-changes.json").is_file())
            self.assertTrue((directory / "workspace.diff").is_file())
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Saved run: run-test-001", rendered)
            self.assertEqual(len(runner.calls), 1)

    def test_standard_workspace_tracks_created_modified_deleted_and_rolls_back(self):
        result = implementation_result(files_changed=[
            {"path": "created.txt", "summary": "Created the approved file."},
            {"path": "modified.txt", "summary": "Updated the approved file."},
            {"path": "deleted.txt", "summary": "Deleted the explicitly approved file."},
        ])

        def mutate(workspace, _sequence):
            (workspace / "created.txt").write_text("created\n", encoding="utf-8")
            (workspace / "modified.txt").write_text("after\n", encoding="utf-8")
            (workspace / "deleted.txt").unlink()

        with tempfile.TemporaryDirectory() as tmp:
            bridge, team_store, workspace = self.build(
                tmp,
                runner=FakeRunner(CodexProcessResult(0, valid_jsonl(result), ""), mutator=mutate),
                git_workspace=False,
            )
            (workspace / "modified.txt").write_text("before\n", encoding="utf-8")
            (workspace / "deleted.txt").write_text("restore me\n", encoding="utf-8")
            task = team_store.load("team-test-001")
            task.goal = "Create created.txt, modify modified.txt, and delete deleted.txt"
            task.final_plan = ["Create created.txt", "Modify modified.txt", "Delete deleted.txt"]
            team_store.save(task)

            approval = bridge.approve(task.task_id)
            run = bridge.execute(task.task_id, approval.approval_id)

            self.assertEqual([item.path for item in run.changes.by_kind("created")], ["created.txt"])
            self.assertEqual([item.path for item in run.changes.by_kind("modified")], ["modified.txt"])
            self.assertEqual([item.path for item in run.changes.by_kind("deleted")], ["deleted.txt"])
            diff = bridge.store.read_run_artifact(run.run_id, "workspace.diff")
            self.assertIn("--- /dev/null", diff)
            self.assertIn("--- a/modified.txt", diff)
            self.assertIn("+++ /dev/null", diff)

            rolled_back = bridge.rollback(run.run_id)
            self.assertEqual(rolled_back.status, "rolled_back")
            self.assertFalse((workspace / "created.txt").exists())
            self.assertEqual((workspace / "modified.txt").read_text(encoding="utf-8"), "before\n")
            self.assertEqual((workspace / "deleted.txt").read_text(encoding="utf-8"), "restore me\n")

    def test_rollback_refuses_to_overwrite_newer_changes(self):
        result = implementation_result(files_changed=[
            {"path": "modified.txt", "summary": "Updated the file."},
        ])

        def mutate(workspace, _sequence):
            (workspace / "modified.txt").write_text("run change\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, workspace = self.build(
                tmp,
                runner=FakeRunner(CodexProcessResult(0, valid_jsonl(result), ""), mutator=mutate),
                git_workspace=False,
            )
            (workspace / "modified.txt").write_text("before\n", encoding="utf-8")
            approval = bridge.approve("team-test-001")
            run = bridge.execute("team-test-001", approval.approval_id)
            (workspace / "modified.txt").write_text("newer user work\n", encoding="utf-8")

            with self.assertRaises(WorkspaceRollbackError):
                bridge.rollback(run.run_id)

            self.assertEqual((workspace / "modified.txt").read_text(encoding="utf-8"), "newer user work\n")

    def test_binary_change_reports_metadata_without_text_dump(self):
        result = implementation_result(files_changed=[
            {"path": "image.bin", "summary": "Updated binary data."},
        ])

        def mutate(workspace, _sequence):
            (workspace / "image.bin").write_bytes(b"\x00after-binary-secret")

        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, workspace = self.build(
                tmp,
                runner=FakeRunner(CodexProcessResult(0, valid_jsonl(result), ""), mutator=mutate),
                git_workspace=False,
            )
            (workspace / "image.bin").write_bytes(b"\x00before-binary-secret")
            approval = bridge.approve("team-test-001")
            run = bridge.execute("team-test-001", approval.approval_id)

            change = run.changes.by_kind("modified")[0]
            self.assertTrue(change.binary)
            diff = bridge.store.read_run_artifact(run.run_id, "workspace.diff")
            self.assertNotIn("binary-secret", diff)
            self.assertNotIn("image.bin", diff)

    def test_diff_redacts_secrets_and_ignored_runtime_paths_are_excluded(self):
        result = implementation_result(files_changed=[
            {"path": "settings.txt", "summary": "Updated a setting."},
        ])

        def mutate(workspace, _sequence):
            (workspace / "settings.txt").write_text("API_KEY=new-super-secret\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, workspace = self.build(
                tmp,
                runner=FakeRunner(CodexProcessResult(0, valid_jsonl(result), ""), mutator=mutate),
                git_workspace=False,
            )
            (workspace / "settings.txt").write_text("API_KEY=old-super-secret\n", encoding="utf-8")
            (workspace / "node_modules").mkdir()
            (workspace / "node_modules" / "secret.txt").write_text("IGNORED_SECRET_VALUE", encoding="utf-8")
            (workspace / ".orion").mkdir()
            (workspace / ".orion" / "runtime.txt").write_text("RUNTIME_SECRET_VALUE", encoding="utf-8")

            approval = bridge.approve("team-test-001")
            run = bridge.execute("team-test-001", approval.approval_id)
            diff = bridge.store.read_run_artifact(run.run_id, "workspace.diff")
            baseline = bridge.store.read_run_artifact(run.run_id, "workspace-baseline.json")

            self.assertIn("<redacted>", diff)
            self.assertNotIn("old-super-secret", diff)
            self.assertNotIn("new-super-secret", diff)
            self.assertNotIn("IGNORED_SECRET_VALUE", baseline)
            self.assertNotIn("RUNTIME_SECRET_VALUE", baseline)
            self.assertNotIn("node_modules/secret.txt", baseline)
            self.assertNotIn(".orion/runtime.txt", baseline)

    def test_snapshot_limits_stop_execution_before_approval_is_consumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, workspace = self.build(
                tmp,
                runner=runner,
                git_workspace=False,
                config={"codex_bridge.snapshot_max_file_bytes": 4},
            )
            (workspace / "too-large.txt").write_text("12345", encoding="utf-8")
            approval = bridge.approve("team-test-001")

            with self.assertRaises(WorkspaceSnapshotError):
                bridge.execute("team-test-001", approval.approval_id)

            self.assertEqual(runner.calls, [])
            self.assertFalse(bridge.store.approval_used(approval.team_task_id, approval.approval_id))

    def test_snapshot_file_count_limit_stops_execution_before_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, workspace = self.build(
                tmp,
                runner=runner,
                git_workspace=False,
                config={"codex_bridge.snapshot_max_files": 1},
            )
            (workspace / "one.txt").write_text("one", encoding="utf-8")
            (workspace / "two.txt").write_text("two", encoding="utf-8")
            approval = bridge.approve("team-test-001")
            with self.assertRaises(WorkspaceSnapshotError):
                bridge.execute("team-test-001", approval.approval_id)
            self.assertEqual(runner.calls, [])

    def test_unified_diff_limit_is_enforced_and_reported(self):
        result = implementation_result(files_changed=[
            {"path": "long.txt", "summary": "Updated bounded text."},
        ])

        def mutate(workspace, _sequence):
            (workspace / "long.txt").write_text("after\n" * 100, encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, workspace = self.build(
                tmp,
                runner=FakeRunner(CodexProcessResult(0, valid_jsonl(result), ""), mutator=mutate),
                git_workspace=False,
                config={"codex_bridge.diff_max_bytes": 32},
            )
            (workspace / "long.txt").write_text("before\n" * 100, encoding="utf-8")
            approval = bridge.approve("team-test-001")
            run = bridge.execute("team-test-001", approval.approval_id)
            diff = bridge.store.read_run_artifact(run.run_id, "workspace.diff")
            self.assertTrue(run.changes.diff_truncated)
            self.assertIn("Orion diff truncated", diff)

    def test_workspace_capability_change_invalidates_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, workspace = self.build(tmp, runner=runner, git_workspace=False)
            approval = bridge.approve("team-test-001")
            (workspace / ".git").mkdir()
            changed = WorkspaceCapabilities.detect(workspace, which=lambda _name: None)
            bridge.bind(workspace, changed)

            with self.assertRaises(PermissionError):
                bridge.execute("team-test-001", approval.approval_id)

            self.assertEqual(runner.calls, [])
            self.assertFalse(bridge.store.approval_used(approval.team_task_id, approval.approval_id))

    def test_execution_workspace_mismatch_aborts_before_codex_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            detector = StaticCapabilityDetector()
            bridge, _, _ = self.build(
                tmp,
                runner=runner,
                capability_detector=detector,
            )
            approval = bridge.approve("team-test-001")
            other = Path(tmp) / "unapproved-workspace"
            other.mkdir()
            other_capabilities = WorkspaceCapabilities.detect(other, which=lambda _name: None)
            context = ExecutionContext(
                team_task_id=approval.team_task_id,
                approval_id=approval.approval_id,
                workspace=other_capabilities,
                execution_engine=bridge._default_execution_engine,
            )

            with self.assertRaisesRegex(PermissionError, "different active workspace"):
                bridge.execute(context)

            self.assertEqual(detector.calls, [])
            self.assertEqual(runner.calls, [])
            self.assertFalse(bridge.store.approval_used(approval.team_task_id, approval.approval_id))

    def test_observed_changes_must_match_structured_result(self):
        result = implementation_result(files_changed=[
            {"path": "reported.txt", "summary": "Reported a different file."},
        ])

        def mutate(workspace, _sequence):
            (workspace / "observed.txt").write_text("actual\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, _ = self.build(
                tmp,
                runner=FakeRunner(CodexProcessResult(0, valid_jsonl(result), ""), mutator=mutate),
                git_workspace=False,
            )
            approval = bridge.approve("team-test-001")
            with self.assertRaises(CodexBridgeError) as raised:
                bridge.execute("team-test-001", approval.approval_id)
            self.assertEqual(raised.exception.category, "workspace_change_mismatch")

    def test_status_and_bridge_share_and_launch_the_resolved_windows_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = root / "python.exe"
            python.write_text("runtime", encoding="utf-8")
            commands = {
                "codex.cmd": "C:/tools/codex.cmd",
                "codex": "C:/wrong/codex.exe",
            }
            engines = ExecutionEngineService(
                FlatConfig(),
                which=lambda candidate: commands.get(candidate),
                probe=lambda executable: executable == str(Path("C:/tools/codex.cmd")),
                environment={},
                platform_name="Windows",
                python_executable=str(python),
            )
            runner = FakeRunner()
            bridge, _, _ = self.build(
                root / "bridge",
                runner=runner,
                execution_engines=engines,
            )
            approval = bridge.approve("team-test-001")

            status_executable = next(
                engine.executable
                for engine in engines.status()
                if engine.engine_id == "codex"
            )
            run = bridge.execute(
                "team-test-001",
                approval.approval_id,
                execution_engine=engines.require_codex(),
            )

            self.assertEqual(status_executable, str(Path("C:/tools/codex.cmd")))
            self.assertEqual(runner.calls[0]["command"][0], status_executable)
            self.assertEqual(run.command[0], status_executable)
            self.assertNotEqual(runner.calls[0]["command"][0], "codex")

    def test_router_hands_one_resolved_engine_to_bridge_without_reprobing(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            engines = Mock()
            resolved = ExecutionEngine(
                engine_id="codex",
                name="Codex CLI",
                status=ENGINE_STATUS_INSTALLED,
                installed=True,
                cli_support=True,
                implementation_supported=True,
                executable=str((Path(tmp) / "tools" / "codex.cmd").resolve()),
            )
            engines.require_codex.side_effect = (
                resolved,
                ExecutionEngineUnavailable("second probe disagreed"),
            )
            bridge, _, _ = self.build(
                tmp,
                runner=runner,
                execution_engines=engines,
                include_default_engine=False,
            )
            router = CommandRouter(SimpleNamespace(
                execution_engines=engines,
                codex_bridge=bridge,
            ))
            approval = bridge.approve("team-test-001")

            with patch("builtins.print") as output:
                router.handle(
                    f"team implement team-test-001 {approval.approval_id}"
                )

            rendered = "\n".join(
                str(call.args[0]) for call in output.call_args_list if call.args
            )
            self.assertEqual(engines.require_codex.call_count, 1)
            self.assertEqual(len(runner.calls), 1)
            self.assertEqual(runner.calls[0]["command"][0], resolved.executable)
            self.assertIn("Starting one approval-bound local Codex execution", rendered)
            self.assertIn("Status: Awaiting Review", rendered)
            self.assertNotIn("No execution engine is currently available", rendered)

    def test_unapproved_tampered_or_workspace_mismatched_plan_never_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            detector = StaticCapabilityDetector()
            bridge, team_store, _ = self.build(
                tmp,
                runner=runner,
                capability_detector=detector,
            )
            with self.assertRaises(FileNotFoundError):
                bridge.execute("team-test-001", "approval-missing-001")
            self.assertEqual(detector.calls, [])
            self.assertEqual(runner.calls, [])

            approval = bridge.approve("team-test-001")
            task = team_store.load("team-test-001")
            task.final_plan.append("Unapproved new step")
            team_store.save(task)
            with self.assertRaises(PermissionError):
                bridge.execute(task.task_id, approval.approval_id)
            self.assertEqual(runner.calls, [])

        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, _ = self.build(tmp, runner=runner)
            approval = bridge.approve("team-test-001")
            other = Path(tmp) / "other-workspace"
            other.mkdir()
            (other / ".git").mkdir()
            bridge.bind(other)
            with self.assertRaises(PermissionError):
                bridge.execute("team-test-001", approval.approval_id)
            self.assertEqual(runner.calls, [])

    def test_each_approval_is_single_use_and_retry_requires_new_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, _ = self.build(tmp, runner=runner)
            first = bridge.approve("team-test-001")
            bridge.execute("team-test-001", first.approval_id)
            with self.assertRaises(PermissionError):
                bridge.execute("team-test-001", first.approval_id)

            second = bridge.approve("team-test-001")
            self.assertNotEqual(first.approval_id, second.approval_id)
            bridge.execute("team-test-001", second.approval_id)
            self.assertEqual(len(runner.calls), 2)

    def test_unavailable_engine_does_not_consume_plan_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            engines = Mock()
            engines.require_codex.side_effect = ExecutionEngineUnavailable(
                "No execution engine is currently available."
            )
            bridge, _, _ = self.build(
                tmp,
                runner=runner,
                execution_engines=engines,
                include_default_engine=False,
            )
            approval = bridge.approve("team-test-001")

            with self.assertRaises(ExecutionEngineUnavailable):
                bridge.execute("team-test-001", approval.approval_id)
            self.assertFalse(
                bridge.store.approval_claim_path(
                    approval.team_task_id, approval.approval_id
                ).exists()
            )
            self.assertEqual(runner.calls, [])

            resolved = ExecutionEngine(
                engine_id="codex",
                name="Codex CLI",
                status=ENGINE_STATUS_INSTALLED,
                installed=True,
                cli_support=True,
                implementation_supported=True,
                executable=str((Path(tmp) / "tools" / "codex.cmd").resolve()),
            )
            run = bridge.execute(
                "team-test-001",
                approval.approval_id,
                execution_engine=resolved,
            )
            self.assertEqual(run.status, RUN_STATUS_AWAITING_REVIEW)

    def test_invalid_structured_results_fail_closed_and_preserve_sanitized_run(self):
        invalid_results = {
            "unknown field": {**implementation_result(), "unexpected": True},
            "workspace escape": implementation_result(files_changed=[{"path": "../escape.py", "summary": "bad"}]),
            "protected metadata": implementation_result(files_changed=[{"path": ".git/config", "summary": "bad"}]),
            "duplicate path": implementation_result(files_changed=[
                {"path": "same.py", "summary": "one"},
                {"path": "same.py", "summary": "two"},
            ]),
            "missing test result": implementation_result(tests=[]),
            "unknown test status": implementation_result(tests=[
                {"command": "tests", "status": "maybe", "summary": "unknown"},
            ]),
        }
        for label, result in invalid_results.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                runner = FakeRunner(CodexProcessResult(0, valid_jsonl(result), ""))
                bridge, _, _ = self.build(tmp, runner=runner)
                approval = bridge.approve("team-test-001")
                with self.assertRaises(CodexBridgeError) as raised:
                    bridge.execute("team-test-001", approval.approval_id)
                self.assertEqual(raised.exception.category, "invalid_codex_output")
                failed = bridge.run(raised.exception.run_id)
                self.assertEqual(failed.status, RUN_STATUS_FAILED)
                self.assertEqual(failed.error, "invalid_codex_output")
                self.assertIsNone(failed.result)

    def test_invalid_jsonl_or_missing_final_message_fails_closed(self):
        outputs = {
            "invalid json": "{broken\n",
            "missing final message": json.dumps({"type": "turn.completed"}) + "\n",
            "missing event type": json.dumps({"item": {"type": "agent_message", "text": "{}"}}) + "\n",
        }
        for label, output in outputs.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                bridge, _, _ = self.build(
                    tmp,
                    runner=FakeRunner(CodexProcessResult(0, output, "")),
                )
                approval = bridge.approve("team-test-001")
                with self.assertRaises(CodexBridgeError) as raised:
                    bridge.execute("team-test-001", approval.approval_id)
                self.assertEqual(raised.exception.category, "invalid_codex_output")

    def test_process_failure_timeout_and_missing_cli_are_sanitized(self):
        failures = {
            "nonzero": (
                FakeRunner(CodexProcessResult(9, "", "SECRET_TOKEN=do-not-store")),
                "codex_process_failed",
            ),
            "timeout": (
                FakeRunner(error=subprocess.TimeoutExpired(["codex"], 1, output="secret")),
                "codex_timeout",
            ),
            "missing": (FakeRunner(error=FileNotFoundError("private executable path")), "codex_cli_unavailable"),
        }
        for label, (runner, category) in failures.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                bridge, _, _ = self.build(tmp, runner=runner)
                approval = bridge.approve("team-test-001")
                with self.assertRaises(CodexBridgeError) as raised:
                    bridge.execute("team-test-001", approval.approval_id)
                self.assertEqual(raised.exception.category, category)
                failed = bridge.run(raised.exception.run_id)
                self.assertEqual(failed.error, category)
                self.assertEqual(
                    failed.diagnostics.resolved_executable,
                    failed.command[0],
                )
                if category == "codex_timeout":
                    self.assertTrue(failed.diagnostics.timed_out)
                    self.assertIsNone(failed.diagnostics.exit_code)
                elif category == "codex_process_failed":
                    self.assertFalse(failed.diagnostics.timed_out)
                    self.assertEqual(failed.diagnostics.exit_code, 9)
                persisted = "".join(
                    path.read_text(encoding="utf-8")
                    for path in bridge.store.run_directory(failed.run_id).iterdir()
                    if path.is_file()
                )
                self.assertNotIn("SECRET_TOKEN", persisted)
                self.assertNotIn("private executable path", persisted)

    def test_output_capture_limit_is_validated_before_and_after_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, _ = self.build(tmp, runner=runner, config={"codex_bridge.max_output_bytes": 0})
            approval = bridge.approve("team-test-001")
            with self.assertRaises(ValueError):
                bridge.execute("team-test-001", approval.approval_id)
            self.assertEqual(runner.calls, [])

        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner(CodexProcessResult(0, "x" * 101, ""))
            bridge, _, _ = self.build(tmp, runner=runner, config={"codex_bridge.max_output_bytes": 100})
            approval = bridge.approve("team-test-001")
            with self.assertRaises(CodexBridgeError) as raised:
                bridge.execute("team-test-001", approval.approval_id)
            self.assertEqual(raised.exception.category, "codex_output_too_large")

    def test_corrupt_approval_and_run_records_are_rejected_without_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, _ = self.build(tmp)
            approval = bridge.approve("team-test-001")
            approval_path = bridge.store.approval_path("team-test-001", approval.approval_id)
            value = json.loads(approval_path.read_text(encoding="utf-8"))
            value["unexpected"] = True
            document = json.dumps(value)
            approval_path.write_text(document, encoding="utf-8")
            with self.assertRaises(ValueError):
                bridge.store.load_approval("team-test-001", approval.approval_id)
            self.assertEqual(approval_path.read_text(encoding="utf-8"), document)

        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, _ = self.build(tmp)
            approval = bridge.approve("team-test-001")
            run = bridge.execute("team-test-001", approval.approval_id)
            run_path = bridge.store.run_path(run.run_id)
            value = json.loads(run_path.read_text(encoding="utf-8"))
            value["status"] = "complete"
            document = json.dumps(value)
            run_path.write_text(document, encoding="utf-8")
            with self.assertRaises(ValueError):
                bridge.run(run.run_id)
            self.assertEqual(run_path.read_text(encoding="utf-8"), document)

        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, _ = self.build(tmp)
            approval = bridge.approve("team-test-001")
            run = bridge.execute("team-test-001", approval.approval_id)
            claim_path = bridge.store.approval_claim_path(run.team_task_id, run.approval_id)
            value = json.loads(claim_path.read_text(encoding="utf-8"))
            value["run_id"] = "invalid"
            document = json.dumps(value)
            claim_path.write_text(document, encoding="utf-8")
            with self.assertRaises(ValueError):
                bridge.execute("team-test-001", approval.approval_id)
            self.assertEqual(claim_path.read_text(encoding="utf-8"), document)

    def test_local_runner_uses_no_shell_and_does_not_forward_secret_environment(self):
        completed = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        environment = {
            "PATH": "safe-path",
            "USERPROFILE": "C:\\Users\\test",
            "CODEX_HOME": "C:\\Users\\test\\.codex",
            "OPENAI_API_KEY": "secret-openai",
            "CODEX_API_KEY": "secret-codex",
            "CODEX_ACCESS_TOKEN": "secret-token",
            "GITHUB_TOKEN": "secret-github",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "orion.services.codex_bridge.subprocess.run", return_value=completed
        ) as execute:
            result = LocalCodexRunner().run(
                ["codex", "exec", "-"],
                cwd=Path.cwd(),
                prompt="approved prompt",
                timeout=30,
            )
        self.assertEqual(result.returncode, 0)
        arguments = execute.call_args.kwargs
        self.assertFalse(arguments["shell"])
        self.assertEqual(arguments["input"], "approved prompt")
        self.assertEqual(arguments["env"]["PATH"], "safe-path")
        self.assertEqual(arguments["env"]["CODEX_HOME"], "C:\\Users\\test\\.codex")
        self.assertNotIn("OPENAI_API_KEY", arguments["env"])
        self.assertNotIn("CODEX_API_KEY", arguments["env"])
        self.assertNotIn("CODEX_ACCESS_TOKEN", arguments["env"])
        self.assertNotIn("GITHUB_TOKEN", arguments["env"])

    def test_windows_help_probe_validates_the_same_strict_sandbox_configuration(self):
        completed = SimpleNamespace(returncode=0, stdout="--sandbox --config", stderr="")
        with patch.dict(os.environ, {"PATH": "safe-path"}, clear=True), patch(
            "orion.services.codex_bridge.subprocess.run", return_value=completed
        ) as execute:
            LocalCodexRunner(platform_name="Windows").run_help("codex.exe", timeout=2.0)

        command = execute.call_args.args[0]
        self.assertEqual(command[:4], [
            "codex.exe", "exec", "--ignore-user-config", "--strict-config"
        ])
        self.assertIn('windows.sandbox="elevated"', command)
        self.assertEqual(command[-1], "--help")
        self.assertIsNone(execute.call_args.kwargs["input"])

    def test_local_runner_launches_windows_cmd_wrapper_with_fixed_arguments(self):
        completed = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appdata = root / "AppData" / "Roaming"
            wrapper = appdata / "npm" / "codex.cmd"
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text("@echo off\n", encoding="utf-8")
            program_files = root / "Program Files"
            node = program_files / "nodejs" / "node.exe"
            node.parent.mkdir(parents=True)
            node.write_text("runtime", encoding="utf-8")
            environment = {
                "APPDATA": str(appdata),
                "COMSPEC": "C:\\Windows\\System32\\cmd.exe",
                "PATH": "C:\\Windows\\System32",
                "PROGRAMFILES": str(program_files),
            }
            with patch.dict(os.environ, environment, clear=True), patch(
                "orion.services.codex_bridge.subprocess.run", return_value=completed
            ) as execute:
                result = LocalCodexRunner(platform_name="Windows").run(
                    [str(wrapper), "exec", "--json", "-"],
                    cwd=root,
                    prompt="approved prompt",
                    timeout=30,
                )
        self.assertEqual(result.returncode, 0)
        command = execute.call_args.args[0]
        self.assertEqual(command[:4], [
            "C:\\Windows\\System32\\cmd.exe", "/d", "/s", "/c"
        ])
        self.assertEqual(command[4:], [str(wrapper), "exec", "--json", "-"])
        self.assertFalse(execute.call_args.kwargs["shell"])
        self.assertIn(str(node.parent), execute.call_args.kwargs["env"]["PATH"])

    def test_bridge_can_be_disabled_and_timeout_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, _ = self.build(tmp, config={"codex_bridge.enabled": False})
            with self.assertRaises(ValueError):
                bridge.approve("team-test-001")

        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, _, _ = self.build(tmp, runner=runner, config={"codex_bridge.timeout_seconds": 7201})
            approval = bridge.approve("team-test-001")
            with self.assertRaises(ValueError):
                bridge.execute("team-test-001", approval.approval_id)
            self.assertEqual(runner.calls, [])

    def test_router_exposes_approval_execution_and_persisted_review_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, _ = self.build(tmp)
            router = CommandRouter(SimpleNamespace(codex_bridge=bridge))
            with patch("builtins.print") as output:
                router.handle("team approve team-test-001")
                router.handle("team implement team-test-001 approval-test-001")
                router.handle("team run run-test-001")

            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Approval ID: approval-test-001", rendered)
            self.assertIn("Status: Awaiting Review", rendered)
            self.assertIn("[PASSED]", rendered)
            self.assertIn("No Git or pull-request action was performed", rendered)

    def test_router_requires_explicit_confirmation_before_team_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, workspace = self.build(tmp, git_workspace=False)
            approval = bridge.approve("team-test-001")
            run = bridge.execute("team-test-001", approval.approval_id)
            created = workspace / "orion" / "services" / "example.py"
            router = CommandRouter(SimpleNamespace(codex_bridge=bridge))

            with patch("builtins.input", return_value="n"), patch("builtins.print"):
                router.team_rollback(run.run_id)
            self.assertTrue(created.exists())

            with patch("builtins.input", return_value="y"), patch("builtins.print"):
                router.team_rollback(run.run_id)
            self.assertFalse(created.exists())
            self.assertEqual(bridge.run(run.run_id).status, "rolled_back")


if __name__ == "__main__":
    unittest.main()
