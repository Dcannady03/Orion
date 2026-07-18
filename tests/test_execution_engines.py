import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orion.core.router import CommandRouter
from orion.services.execution_engines import (
    ENGINE_STATUS_INSTALLED,
    ENGINE_STATUS_NOT_INSTALLED,
    ENGINE_STATUS_READY,
    ExecutionEngineService,
    ExecutionEngineUnavailable,
    resolve_codex_executable,
)


class FlatConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class ExecutionEngineServiceTests(unittest.TestCase):
    def service(self, root, *, commands=None, runnable=None, applications=(), values=None):
        commands = commands or {}
        runnable = {str(Path(value).expanduser()) for value in (runnable or ())}
        catalog = SimpleNamespace(applications=lambda: tuple(
            SimpleNamespace(name=name) for name in applications
        ))
        python = Path(root) / "python.exe"
        python.write_text("runtime", encoding="utf-8")
        return ExecutionEngineService(
            FlatConfig(values),
            catalog,
            which=lambda command: commands.get(command),
            probe=lambda executable: executable in runnable,
            environment={},
            platform_name="Windows",
            python_executable=str(python),
        )

    def test_windows_codex_resolver_uses_cmd_exe_then_extensionless_order(self):
        cases = (
            ({"codex.cmd": "C:/tools/codex.cmd"}, ["codex.cmd"], "codex.cmd"),
            (
                {"codex.exe": "C:/tools/codex.exe"},
                ["codex.cmd", "codex.exe"],
                "codex.exe",
            ),
            (
                {"codex": "C:/tools/codex"},
                ["codex.cmd", "codex.exe", "codex"],
                "codex",
            ),
        )
        for commands, expected_calls, expected_name in cases:
            with self.subTest(expected_name=expected_name):
                which = Mock(side_effect=lambda candidate: commands.get(candidate))
                resolved = resolve_codex_executable(
                    which=which,
                    platform_name="Windows",
                )
                self.assertEqual(resolved, Path(commands[expected_name]))
                self.assertEqual(
                    [call.args[0] for call in which.call_args_list],
                    expected_calls,
                )

    def test_non_windows_codex_resolver_checks_only_extensionless_command(self):
        which = Mock(return_value="/usr/local/bin/codex")
        resolved = resolve_codex_executable(which=which, platform_name="Linux")
        self.assertEqual(resolved, Path("/usr/local/bin/codex"))
        which.assert_called_once_with("codex")

    def test_status_distinguishes_runnable_clis_desktop_and_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service(
                tmp,
                commands={
                    "codex": "C:/tools/codex.exe",
                    "claude": "C:/tools/claude.exe",
                    "gemini": "C:/tools/gemini.exe",
                },
                runnable={"C:/tools/codex.exe", "C:/tools/gemini.exe"},
                applications=("ChatGPT",),
            )
            engines = {engine.engine_id: engine for engine in service.status()}

            self.assertEqual(engines["codex"].status, ENGINE_STATUS_INSTALLED)
            self.assertTrue(engines["codex"].ready_for_implementation)
            self.assertEqual(engines["chatgpt_desktop"].status, ENGINE_STATUS_INSTALLED)
            self.assertFalse(engines["chatgpt_desktop"].cli_support)
            self.assertEqual(engines["claude_code"].status, ENGINE_STATUS_NOT_INSTALLED)
            self.assertEqual(engines["claude_code"].reason, "command_not_runnable")
            self.assertEqual(engines["gemini_cli"].status, ENGINE_STATUS_INSTALLED)
            self.assertFalse(engines["gemini_cli"].implementation_supported)
            self.assertEqual(engines["python"].status, ENGINE_STATUS_READY)

    def test_command_must_run_not_merely_exist_on_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service(
                tmp,
                commands={
                    "codex": "C:/Program Files/WindowsApps/OpenAI.Codex_1.0/app/resources/codex.exe"
                },
                runnable=(),
            )
            codex = service.engine("codex")
            self.assertFalse(codex.installed)
            self.assertEqual(codex.status, ENGINE_STATUS_NOT_INSTALLED)
            self.assertEqual(codex.reason, "command_not_runnable")
            self.assertTrue(service.engine("chatgpt_desktop").installed)
            with self.assertRaises(ExecutionEngineUnavailable):
                service.require_codex()

    def test_selected_engine_requires_an_implemented_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service(
                tmp,
                commands={"claude": "C:/tools/claude.exe"},
                runnable={"C:/tools/claude.exe"},
                values={"execution.default_engine": "claude_code"},
            )
            self.assertEqual(service.selected_engine_id, "claude_code")
            self.assertIsNone(service.selected_engine())

    def test_chatgpt_desktop_can_be_detected_from_host_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shortcut = root / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "ChatGPT.lnk"
            shortcut.parent.mkdir(parents=True)
            shortcut.write_text("shortcut", encoding="utf-8")
            python = root / "python.exe"
            python.write_text("runtime", encoding="utf-8")
            service = ExecutionEngineService(
                FlatConfig(),
                which=lambda _command: None,
                probe=lambda _executable: False,
                environment={"APPDATA": str(root)},
                platform_name="Windows",
                python_executable=str(python),
            )
            desktop = service.engine("chatgpt_desktop")
            self.assertTrue(desktop.installed)
            self.assertFalse(desktop.cli_support)

    def test_detection_errors_are_isolated_and_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = ExecutionEngineService(
                FlatConfig(),
                application_catalog=SimpleNamespace(applications=Mock(side_effect=ValueError("secret"))),
                which=Mock(side_effect=OSError("private path")),
                environment={},
                platform_name="Windows",
                python_executable=str(Path(tmp) / "missing-python"),
            )
            engines = service.status()
            self.assertTrue(all("secret" not in engine.reason for engine in engines))
            self.assertTrue(all("private" not in engine.reason for engine in engines))

    def test_execution_status_renders_clear_engine_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service(tmp, applications=("ChatGPT",))
            router = CommandRouter(SimpleNamespace(execution_engines=service))
            with patch("builtins.print") as output:
                router.handle("execution status")
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Execution Engines", rendered)
            self.assertIn("Codex CLI\nStatus:\nNot Installed", rendered)
            self.assertIn("ChatGPT Desktop\nStatus:\nInstalled\nCLI Support:\nNo", rendered)
            self.assertIn("Claude Code\nStatus:\nNot Installed", rendered)
            self.assertIn("Gemini CLI\nStatus:\nNot Installed", rendered)
            self.assertIn("Python Executor\nStatus:\nReady", rendered)

    def test_team_implement_explains_missing_engine_without_calling_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service(tmp, applications=("ChatGPT",))
            bridge = Mock()
            router = CommandRouter(SimpleNamespace(execution_engines=service, codex_bridge=bridge))
            with patch("builtins.print") as output:
                router.handle("team implement team-test-001 approval-test-001")
            bridge.execute.assert_not_called()
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("No execution engine is currently available", rendered)
            self.assertIn("✓ ChatGPT Desktop", rendered)
            self.assertIn("✗ Codex CLI", rendered)
            self.assertIn("✗ Claude Code", rendered)
            self.assertIn("✗ Gemini CLI", rendered)
            self.assertIn("execution status", rendered)
            self.assertNotIn("Codex not found", rendered)


if __name__ == "__main__":
    unittest.main()
