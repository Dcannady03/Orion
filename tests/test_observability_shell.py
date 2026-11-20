"""Tests for Orion approval-based shell execution."""
from __future__ import annotations

import os
import subprocess
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import yaml

from orion.application.commands.observability_cli import ObservabilityCliAdapter
from orion.application.observability import (
    ObservabilityApplicationHandler,
    ShellService,
    ShellUnavailableError,
)
from orion.application.observability.shell import (
    MAX_OUTPUT_CHARS,
    SHELL_CONFIG_KEY,
)
from orion.core.config import ConfigManager
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter
from orion.ui.console import BASE_COMMANDS


class _Clock:
    def __call__(self):
        from datetime import datetime, timezone

        return datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


class _FakeRunner:
    """Stand-in for subprocess.run: records calls, returns canned results."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        timeout: bool = False,
        missing: bool = False,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timeout = timeout
        self.missing = missing
        self.calls: list[tuple[list[str], float, str]] = []

    def __call__(self, argv, timeout, cwd):
        self.calls.append((list(argv), timeout, cwd))
        if self.missing:
            raise FileNotFoundError(2, "No such file", argv[0])
        if self.timeout:
            raise subprocess.TimeoutExpired(
                argv, timeout, output=self.stdout, stderr=self.stderr
            )
        return subprocess.CompletedProcess(
            argv, self.returncode, self.stdout, self.stderr
        )


class _FakeConfig:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


def _service(runner=None, *, cwd, platform_name="posix"):
    return ShellService(
        runner=runner or _FakeRunner(),
        default_cwd=cwd,
        platform_name=platform_name,
    )


def _handler(paths, config_values=None, runner=None, *, services=None, cwd=None):
    return ObservabilityApplicationHandler(
        paths=paths,
        config_manager=(
            None if config_values is None else _FakeConfig(config_values)
        ),
        services=services,
        shell_service=_service(runner, cwd=cwd, platform_name="posix"),
        clock=_Clock(),
    )


def _permitted(values=True):
    return {SHELL_CONFIG_KEY: values}


class ShellServiceTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_plan_resolves_windows_argv(self):
        service = _service(cwd=self.root, platform_name="nt")
        plan = service.plan("hostname")
        self.assertEqual(plan.shell, "powershell")
        self.assertEqual(plan.shell_binary, "powershell.exe")
        self.assertEqual(
            plan.argv,
            (
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "hostname",
            ),
        )
        self.assertEqual(plan.command, "hostname")
        self.assertEqual(plan.timeout, 30.0)
        self.assertEqual(plan.cwd, str(self.root.resolve()))

    def test_plan_resolves_posix_argv(self):
        service = _service(cwd=self.root, platform_name="posix")
        plan = service.plan("hostname")
        self.assertEqual(plan.shell, "sh")
        self.assertEqual(plan.shell_binary, "sh")
        self.assertEqual(plan.argv, ("sh", "-c", "hostname"))

    def test_plan_honors_orion_powershell_override(self):
        with patch.dict(os.environ, {"ORION_POWERSHELL": "pwsh.exe"}):
            service = _service(cwd=self.root, platform_name="nt")
            plan = service.plan("hostname")
        self.assertEqual(plan.shell_binary, "pwsh.exe")
        self.assertEqual(plan.argv[0], "pwsh.exe")

    def test_plan_rejects_empty_command(self):
        service = _service(cwd=self.root)
        for command in ("", "   "):
            with self.assertRaises(ValueError):
                service.plan(command)

    def test_plan_strips_surrounding_whitespace(self):
        service = _service(cwd=self.root)
        plan = service.plan("  hostname  ")
        self.assertEqual(plan.command, "hostname")

    def test_plan_clamps_timeout_to_max(self):
        service = _service(cwd=self.root)
        self.assertEqual(service.plan("hostname", timeout=900).timeout, 300.0)
        self.assertEqual(service.plan("hostname", timeout="45").timeout, 45.0)

    def test_plan_rejects_non_positive_timeout(self):
        service = _service(cwd=self.root)
        for timeout in (0, -5, "0"):
            with self.assertRaises(ValueError):
                service.plan("hostname", timeout=timeout)

    def test_plan_rejects_non_numeric_timeout(self):
        service = _service(cwd=self.root)
        with self.assertRaises(ValueError):
            service.plan("hostname", timeout="abc")

    def test_plan_rejects_missing_cwd(self):
        service = _service(cwd=self.root)
        with self.assertRaises(ValueError):
            service.plan("hostname", cwd=self.root / "nope")

    def test_plan_accepts_explicit_cwd(self):
        subdir = self.root / "work"
        subdir.mkdir()
        service = _service(cwd=self.root)
        plan = service.plan("hostname", cwd=subdir)
        self.assertEqual(plan.cwd, str(subdir.resolve()))

    def test_execute_returns_stdout_stderr_exit_code_duration(self):
        runner = _FakeRunner(returncode=0, stdout="hello\n", stderr="warn\n")
        service = _service(runner, cwd=self.root)
        execution = service.execute("echo hello")
        self.assertEqual(execution.exit_code, 0)
        self.assertFalse(execution.timed_out)
        self.assertEqual(execution.stdout, "hello\n")
        self.assertEqual(execution.stderr, "warn\n")
        self.assertGreaterEqual(execution.duration_seconds, 0.0)
        self.assertEqual(len(runner.calls), 1)
        argv, timeout, cwd = runner.calls[0]
        self.assertEqual(argv, ["sh", "-c", "echo hello"])
        self.assertEqual(timeout, 30.0)
        self.assertEqual(cwd, str(self.root.resolve()))

    def test_execute_nonzero_exit_is_a_result_not_a_crash(self):
        runner = _FakeRunner(returncode=3, stderr="boom\n")
        service = _service(runner, cwd=self.root)
        execution = service.execute("exit 3")
        self.assertEqual(execution.exit_code, 3)
        self.assertFalse(execution.timed_out)
        self.assertEqual(execution.stderr, "boom\n")

    def test_execute_timeout_kills_and_reports_partial_output(self):
        runner = _FakeRunner(timeout=True, stdout="partial\n")
        service = _service(runner, cwd=self.root)
        execution = service.execute("sleep 60", timeout=5)
        self.assertTrue(execution.timed_out)
        self.assertIsNone(execution.exit_code)
        self.assertEqual(execution.stdout, "partial\n")
        self.assertEqual(execution.timeout, 5.0)

    def test_execute_missing_shell_is_unavailable(self):
        runner = _FakeRunner(missing=True)
        service = _service(runner, cwd=self.root)
        with self.assertRaises(ShellUnavailableError) as ctx:
            service.execute("hostname")
        self.assertIn("not found", str(ctx.exception))

    def test_execute_truncates_long_output_keeping_the_tail(self):
        lines = [f"line-{index}" for index in range(2000)]
        runner = _FakeRunner(stdout="\n".join(lines))
        service = _service(runner, cwd=self.root)
        execution = service.execute("yes")
        self.assertTrue(execution.stdout_truncated)
        self.assertLessEqual(len(execution.stdout), MAX_OUTPUT_CHARS)
        self.assertIn("line-1999", execution.stdout)
        self.assertNotIn("line-0\n", execution.stdout)
        self.assertFalse(execution.stderr_truncated)

    def test_execute_truncates_single_huge_line_without_emptying_it(self):
        runner = _FakeRunner(stdout="x" * (MAX_OUTPUT_CHARS + 1000))
        service = _service(runner, cwd=self.root)
        execution = service.execute("yes")
        self.assertTrue(execution.stdout_truncated)
        self.assertEqual(len(execution.stdout), MAX_OUTPUT_CHARS)


class ShellHandlerTests(TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = OrionPaths(install_root=root, user_root=root / ".orion")
        self.paths.ensure()
        self.workdir = root / "work"
        self.workdir.mkdir()
        self.runner = _FakeRunner(stdout="DESKTOP-1\n")
        self.handler = _handler(
            self.paths, _permitted(), self.runner, cwd=self.workdir
        )

    def _history(self, handler=None):
        result = (handler or self.handler).recovery_list(kind="shell")
        self.assertTrue(result.ok)
        return result.data

    def test_plan_shows_exact_command_cwd_and_timeout(self):
        result = self.handler.shell_plan("Get-Date")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["command"], "Get-Date")
        self.assertEqual(result.data["cwd"], str(self.workdir.resolve()))
        self.assertEqual(result.data["timeout"], 30.0)
        self.assertTrue(result.data["permitted"])
        self.assertIn("Command   : Get-Date", result.message)
        self.assertIn(f"Directory : {self.workdir.resolve()}", result.message)
        self.assertIn("Timeout   : 30s", result.message)
        self.assertEqual(self.runner.calls, [])

    def test_plan_reports_permission_off_without_running(self):
        handler = _handler(self.paths, _permitted(False), self.runner, cwd=self.workdir)
        result = handler.shell_plan("hostname")
        self.assertTrue(result.ok)
        self.assertFalse(result.data["permitted"])
        self.assertIn("will be refused", result.message)
        self.assertEqual(self.runner.calls, [])

    def test_plan_timeout_is_clamped(self):
        result = self.handler.shell_plan("hostname", timeout=900)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["timeout"], 300.0)

    def test_execute_without_confirm_records_refusal(self):
        result = self.handler.shell_execute("hostname")
        self.assertFalse(result.ok)
        self.assertIn("needs interactive approval", result.message)
        self.assertEqual(self.runner.calls, [])
        history = self._history()
        self.assertEqual(history["total"], 1)
        record = history["records"][0]
        self.assertEqual(record["kind"], "shell")
        self.assertEqual(record["target"], "hostname")
        self.assertEqual(record["status"], "failed")
        self.assertIn("approval not given", record["detail"])

    def test_execute_permission_off_refuses_and_records(self):
        handler = _handler(self.paths, _permitted(False), self.runner, cwd=self.workdir)
        result = handler.shell_execute("hostname", confirm=True)
        self.assertFalse(result.ok)
        self.assertIn("shell.arbitrary_commands", result.message)
        self.assertIn("config.yaml", result.message)
        self.assertEqual(self.runner.calls, [])
        history = self._history(handler)
        self.assertEqual(history["total"], 1)
        record = history["records"][0]
        self.assertEqual(record["status"], "failed")
        self.assertIn("off", record["detail"])

    def test_execute_permission_missing_refuses(self):
        handler = _handler(self.paths, {}, self.runner, cwd=self.workdir)
        result = handler.shell_execute("hostname", confirm=True)
        self.assertFalse(result.ok)
        self.assertIn("shell.arbitrary_commands", result.message)
        self.assertEqual(self.runner.calls, [])

    def test_execute_permission_unclear_refuses(self):
        handler = _handler(
            self.paths, _permitted("yes"), self.runner, cwd=self.workdir
        )
        result = handler.shell_execute("hostname", confirm=True)
        self.assertFalse(result.ok)
        self.assertIn("unclear", result.message)
        self.assertEqual(self.runner.calls, [])
        history = self._history(handler)
        self.assertEqual(history["total"], 1)
        self.assertIn("unclear", history["records"][0]["detail"])

    def test_execute_without_config_manager_refuses(self):
        handler = _handler(self.paths, None, self.runner, cwd=self.workdir)
        result = handler.shell_execute("hostname", confirm=True)
        self.assertFalse(result.ok)
        self.assertIn("no configuration is loaded", result.message)
        self.assertEqual(self.runner.calls, [])

    def test_execute_approved_runs_and_records(self):
        result = self.handler.shell_execute("hostname", confirm=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["exit_code"], 0)
        self.assertEqual(result.data["stdout"], "DESKTOP-1\n")
        self.assertIn("Shell: hostname", result.message)
        self.assertIn("DESKTOP-1", result.message)
        self.assertIn("Exit code : 0", result.message)
        self.assertEqual(len(self.runner.calls), 1)
        history = self._history()
        self.assertEqual(history["total"], 1)
        record = history["records"][0]
        self.assertEqual(record["kind"], "shell")
        self.assertEqual(record["target"], "hostname")
        self.assertEqual(record["status"], "success")
        self.assertIn("exit 0", record["detail"])
        self.assertIn(str(self.workdir.resolve()), record["detail"])
        self.assertIn("DESKTOP-1", record["detail"])

    def test_execute_nonzero_exit_is_warning_and_recorded_failed(self):
        runner = _FakeRunner(returncode=3, stderr="boom\n")
        handler = _handler(self.paths, _permitted(), runner, cwd=self.workdir)
        result = handler.shell_execute("exit 3", confirm=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "warning")
        self.assertEqual(result.data["exit_code"], 3)
        self.assertIn("exited with code 3", result.warnings[0])
        self.assertIn("boom", result.message)
        history = self._history(handler)
        self.assertEqual(history["total"], 1)
        self.assertEqual(history["records"][0]["status"], "failed")
        self.assertIn("exit 3", history["records"][0]["detail"])

    def test_execute_timeout_is_failure_and_recorded(self):
        runner = _FakeRunner(timeout=True, stdout="partial\n")
        handler = _handler(self.paths, _permitted(), runner, cwd=self.workdir)
        result = handler.shell_execute("sleep 60", timeout=5, confirm=True)
        self.assertFalse(result.ok)
        self.assertIn("Timed out", result.message)
        self.assertIn("timed out after 5s", result.errors[0])
        self.assertTrue(result.data["timed_out"])
        self.assertIsNone(result.data["exit_code"])
        history = self._history(handler)
        self.assertEqual(history["total"], 1)
        record = history["records"][0]
        self.assertEqual(record["status"], "failed")
        self.assertIn("timed out", record["detail"])

    def test_execute_unavailable_shell_is_failure_and_recorded(self):
        runner = _FakeRunner(missing=True)
        handler = _handler(self.paths, _permitted(), runner, cwd=self.workdir)
        result = handler.shell_execute("hostname", confirm=True)
        self.assertFalse(result.ok)
        self.assertIn("not found", result.message)
        history = self._history(handler)
        self.assertEqual(history["total"], 1)
        self.assertEqual(history["records"][0]["status"], "failed")

    def test_execute_empty_command_is_failure_without_record(self):
        result = self.handler.shell_execute("   ", confirm=True)
        self.assertFalse(result.ok)
        self.assertIn("required", result.message)
        self.assertEqual(self._history()["total"], 0)

    def test_execute_bad_timeout_is_failure_without_record(self):
        result = self.handler.shell_execute("hostname", timeout="abc", confirm=True)
        self.assertFalse(result.ok)
        self.assertIn("number of seconds", result.message)
        self.assertEqual(self._history()["total"], 0)

    def test_workspace_root_is_default_cwd_without_injected_service(self):
        subdir = self.workdir / "project"
        subdir.mkdir()
        services = SimpleNamespace(
            find=lambda name: (
                SimpleNamespace(root=str(subdir)) if name == "workspace" else None
            )
        )
        handler = ObservabilityApplicationHandler(
            paths=self.paths,
            config_manager=_FakeConfig(_permitted()),
            services=services,
            clock=_Clock(),
        )
        result = handler.shell_plan("hostname")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["cwd"], str(subdir.resolve()))

    def test_record_target_caps_long_commands(self):
        long_command = "echo " + "x" * 500
        result = self.handler.shell_execute(long_command)
        self.assertFalse(result.ok)
        history = self._history()
        target = history["records"][0]["target"]
        self.assertLessEqual(len(target), 200)
        self.assertTrue(target.endswith("..."))


class ShellCliTests(TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = OrionPaths(install_root=root, user_root=root / ".orion")
        self.paths.ensure()
        self.workdir = root / "work"
        self.workdir.mkdir()
        self.runner = _FakeRunner(stdout="DESKTOP-1\n")
        self.handler = _handler(
            self.paths, _permitted(), self.runner, cwd=self.workdir
        )
        self.output = StringIO()
        self.answers: list[str] = []

    def _run(self, payload, handler=None):
        runtime = SimpleNamespace(observability_application=handler or self.handler)
        adapter = ObservabilityCliAdapter(
            runtime,
            output_provider=self.output.write,
            input_provider=lambda prompt: self.answers.pop(0),
        )
        self.output.truncate(0)
        self.output.seek(0)
        return adapter.handle(payload)

    def _history(self):
        result = self.handler.recovery_list(kind="shell")
        self.assertTrue(result.ok)
        return result.data

    def test_shell_approve_runs_command(self):
        self.answers = ["y"]
        result = self._run("shell hostname")
        self.assertTrue(result.ok)
        self.assertEqual(len(self.runner.calls), 1)
        rendered = self.output.getvalue()
        self.assertIn("Shell Command (not run yet)", rendered)
        self.assertIn("Command   : hostname", rendered)
        self.assertIn("Sensitive action: run the shell command above.", rendered)
        self.assertIn("DESKTOP-1", rendered)

    def test_shell_approve_accepts_yes(self):
        self.answers = ["yes"]
        result = self._run("shell hostname")
        self.assertTrue(result.ok)
        self.assertEqual(len(self.runner.calls), 1)

    def test_shell_decline_records_refusal(self):
        self.answers = ["n"]
        result = self._run("shell hostname")
        self.assertFalse(result.ok)
        self.assertEqual(self.runner.calls, [])
        history = self._history()
        self.assertEqual(history["total"], 1)
        self.assertIn("approval not given", history["records"][0]["detail"])

    def test_shell_empty_answer_declines(self):
        self.answers = [""]
        result = self._run("shell hostname")
        self.assertFalse(result.ok)
        self.assertEqual(self.runner.calls, [])

    def test_shell_permission_off_never_prompts(self):
        handler = _handler(
            self.paths, _permitted(False), self.runner, cwd=self.workdir
        )
        runtime = SimpleNamespace(observability_application=handler)
        adapter = ObservabilityCliAdapter(
            runtime,
            output_provider=self.output.write,
            input_provider=lambda prompt: (_ for _ in ()).throw(
                AssertionError("approval must not be asked")
            ),
        )
        result = adapter.handle("shell hostname")
        self.assertFalse(result.ok)
        self.assertIn("shell.arbitrary_commands", result.message)
        self.assertEqual(self.runner.calls, [])
        history = handler.recovery_list(kind="shell")
        self.assertEqual(history.data["total"], 1)

    def test_shell_json_flag_prints_json_result(self):
        self.answers = ["y"]
        result = self._run("shell --json hostname")
        self.assertTrue(result.ok)
        rendered = self.output.getvalue()
        self.assertIn('"command": "hostname"', rendered)
        self.assertIn('"exit_code": 0', rendered)
        self.assertIn('"stdout": "DESKTOP-1\\n"', rendered)

    def test_shell_timeout_option(self):
        self.answers = ["y"]
        result = self._run("shell --timeout 60 hostname")
        self.assertTrue(result.ok)
        self.assertEqual(self.runner.calls[0][1], 60.0)

    def test_shell_timeout_equals_form(self):
        self.answers = ["y"]
        result = self._run("shell --timeout=45 hostname")
        self.assertTrue(result.ok)
        self.assertEqual(self.runner.calls[0][1], 45.0)

    def test_shell_timeout_missing_value_is_usage(self):
        self.assertFalse(self._run("shell --timeout").ok)
        self.assertEqual(self.runner.calls, [])

    def test_shell_timeout_non_numeric_is_usage(self):
        self.assertFalse(self._run("shell --timeout abc hostname").ok)
        self.assertEqual(self.runner.calls, [])

    def test_shell_command_flags_and_pipes_survive_verbatim(self):
        self.answers = ["y"]
        result = self._run("shell Get-ChildItem --force | Sort-Object Name")
        self.assertTrue(result.ok)
        argv = self.runner.calls[0][0]
        self.assertEqual(argv[-1], "Get-ChildItem --force | Sort-Object Name")

    def test_shell_quoted_paths_survive_verbatim(self):
        self.answers = ["y"]
        result = self._run('shell Get-Content "C:\\My File.txt"')
        self.assertTrue(result.ok)
        argv = self.runner.calls[0][0]
        self.assertEqual(argv[-1], 'Get-Content "C:\\My File.txt"')

    def test_shell_empty_command_is_usage(self):
        self.assertFalse(self._run("shell").ok)
        self.assertFalse(self._run("shell --json").ok)
        self.assertEqual(self.runner.calls, [])

    def test_shell_options_after_command_are_command_text(self):
        self.answers = ["y"]
        result = self._run("shell echo --json")
        self.assertTrue(result.ok)
        argv = self.runner.calls[0][0]
        self.assertEqual(argv[-1], "echo --json")
        self.assertNotIn('"exit_code": 0', self.output.getvalue())

    def test_shell_zero_timeout_is_rejected_by_plan(self):
        result = self._run("shell --timeout 0 hostname")
        self.assertFalse(result.ok)
        self.assertIn("greater than zero", result.message)
        self.assertEqual(self.runner.calls, [])


class ShellCoverageTests(TestCase):
    def test_shell_in_router_help_base_commands_and_guide(self):
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        help_text = "\n".join(
            str(call.args[0]) for call in output.call_args_list if call.args
        )
        self.assertIn("shell <command>", help_text)
        self.assertIn("shell", BASE_COMMANDS)
        guide = Path(__file__).resolve().parents[1] / "docs" / "USER_GUIDE.md"
        guide_text = guide.read_text(encoding="utf-8")
        self.assertIn("shell <command>", guide_text)

    def test_shell_feature_page_exists(self):
        page = Path(__file__).resolve().parents[1] / "docs" / "SHELL.md"
        text = page.read_text(encoding="utf-8")
        self.assertIn("shell.arbitrary_commands", text)
        self.assertIn("Approve? [y/N]", text)
        self.assertIn("recovery --kind shell", text)
        self.assertIn("300", text)
        self.assertIn("8,192", text)

    def test_roadmap_checks_off_shell_execution(self):
        roadmap = Path(__file__).resolve().parents[1] / "docs" / "ROADMAP.md"
        text = roadmap.read_text(encoding="utf-8")
        self.assertIn("- [x] Approval-based shell execution", text)

    def test_shell_permission_defaults_off_in_config(self):
        defaults = Path(__file__).resolve().parents[1] / "config" / "default.yaml"
        data = yaml.safe_load(defaults.read_text(encoding="utf-8"))
        self.assertIs(data["shell"]["arbitrary_commands"], False)
        self.assertIn("shell", ConfigManager.PERSISTENT_ROOTS)
