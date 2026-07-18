import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orion.core.router import CommandRouter
from orion.services.codex_bridge import (
    RUN_STATUS_AWAITING_REVIEW,
    RUN_STATUS_FAILED,
    CodexBridge,
    CodexBridgeError,
    CodexBridgeStore,
    CodexProcessResult,
    LocalCodexRunner,
    PlanSnapshot,
)
from orion.services.team import (
    TEAM_STATUS_AWAITING_APPROVAL,
    TEAM_STATUS_FAILED,
    RoleOutput,
    TeamArtifact,
    TeamTask,
    TeamTaskStore,
)


class FlatConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeRunner:
    def __init__(self, result=None, error=None):
        self.result = result or CodexProcessResult(0, valid_jsonl(), "")
        self.error = error
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
        return self.result


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
    def build(self, root, *, runner=None, config=None, task_status=TEAM_STATUS_AWAITING_APPROVAL):
        base = Path(root)
        workspace = base / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".git").mkdir()
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
        bridge = CodexBridge(
            FlatConfig(config),
            team_store,
            CodexBridgeStore(base / "user" / "codex"),
            workspace,
            runner=runner or FakeRunner(),
            now=lambda: datetime(2026, 7, 18, 13, 0, tzinfo=timezone.utc),
            approval_id_factory=lambda: next(approvals),
            run_id_factory=lambda: next(runs),
        )
        return bridge, team_store, workspace

    def test_approval_persists_immutable_plan_hash_outside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, team_store, workspace = self.build(tmp)
            task = team_store.load("team-test-001")
            expected_hash = PlanSnapshot.from_team_task(task).hash

            approval = bridge.approve(task.task_id, actor="owner")

            self.assertEqual(approval.plan_hash, expected_hash)
            self.assertEqual(approval.workspace_root, str(workspace.resolve()))
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

    def test_approval_requires_awaiting_approval_plan_and_git_workspace_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, workspace = self.build(tmp, task_status=TEAM_STATUS_FAILED)
            with self.assertRaises(ValueError):
                bridge.approve("team-test-001")

            bridge, _, workspace = self.build(Path(tmp) / "second")
            (workspace / ".git").rmdir()
            with self.assertRaises(ValueError):
                bridge.approve("team-test-001")

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
            self.assertEqual(len(runner.calls), 1)
            call = runner.calls[0]
            command = call["command"]
            self.assertEqual(call["cwd"], workspace.resolve())
            self.assertEqual(call["timeout"], 1800)
            self.assertEqual(command[:4], ("codex", "exec", "--json", "--ephemeral"))
            self.assertIn("workspace-write", command)
            self.assertIn('web_search="disabled"', command)
            self.assertIn("mcp_servers={}", command)
            self.assertIn("features.apps=false", command)
            self.assertIn("features.hooks=false", command)
            self.assertIn("features.multi_agent=false", command)
            self.assertIn("features.remote_plugin=false", command)
            self.assertIn("sandbox_workspace_write.network_access=false", command)
            self.assertIn("sandbox_workspace_write.writable_roots=[]", command)
            self.assertIn("--output-schema", command)
            self.assertIn(str(workspace.resolve()), command)
            self.assertEqual(command[-1], "-")
            self.assertNotIn("--skip-git-repo-check", command)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
            self.assertNotIn("--add-dir", command)
            self.assertIn(approval.plan_hash, call["prompt"])
            self.assertIn("Do not create or switch branches", call["prompt"])
            self.assertIn("Do not invoke web search", call["prompt"])

            directory = bridge.store.run_directory(run.run_id)
            self.assertEqual(
                {path.name for path in directory.iterdir()},
                {"approved-plan.json", "result-schema.json", "events.jsonl", "implementation-result.json", "run.json"},
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

    def test_unapproved_tampered_or_workspace_mismatched_plan_never_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = FakeRunner()
            bridge, team_store, _ = self.build(tmp, runner=runner)
            with self.assertRaises(FileNotFoundError):
                bridge.execute("team-test-001", "approval-missing-001")

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


if __name__ == "__main__":
    unittest.main()
