import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orion.core.router import CommandRouter
from orion.services.execution_engines import (
    ENGINE_STATUS_DETECTION_ERROR,
    ENGINE_STATUS_INSTALLED,
    ENGINE_STATUS_INSTALLED_NOT_EXECUTABLE,
    ENGINE_STATUS_NOT_INSTALLED,
    ENGINE_STATUS_READY,
    ExecutableResolver,
    ExecutionEngineService,
    ExecutionEngineUnavailable,
    WindowsAppDetection,
    WindowsAppDetector,
    resolve_codex_executable,
)


class FlatConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeAppDetector:
    def __init__(self, packages=(), *, available=True):
        self.result = WindowsAppDetection(tuple(packages), available=available)
        self.calls = 0

    def detect(self):
        self.calls += 1
        return self.result


class ExecutionEngineServiceTests(unittest.TestCase):
    def service(
        self,
        root,
        *,
        commands=None,
        runnable=None,
        applications=(),
        values=None,
        packages=(),
        app_detection_available=True,
    ):
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
            windows_app_detector=FakeAppDetector(
                packages,
                available=app_detection_available,
            ),
        )

    def test_windows_resolver_accepts_extensionless_npm_shim(self):
        which = Mock(side_effect=lambda candidate: {
            "codex": "C:/Users/test/AppData/Roaming/npm/codex"
        }.get(candidate))
        resolved = resolve_codex_executable(which=which, platform_name="Windows")
        self.assertEqual(resolved, Path("C:/Users/test/AppData/Roaming/npm/codex"))
        self.assertEqual(
            [call.args[0] for call in which.call_args_list],
            ["codex.cmd", "codex.exe", "codex", "codex.ps1"],
        )

    def test_windows_resolver_detects_cmd_and_prefers_it_over_ps1(self):
        commands = {
            "codex.cmd": "C:/tools/codex.cmd",
            "codex.ps1": "C:/tools/codex.ps1",
        }
        resolver = ExecutableResolver(
            which=lambda candidate: commands.get(candidate),
            probe=lambda _executable: True,
            environment={},
            platform_name="Windows",
        )
        result = resolver.resolve("codex")
        self.assertEqual(result.executable, str(Path("C:/tools/codex.cmd")))
        self.assertEqual(result.source, "PATH")
        self.assertTrue(result.path_visible)

    def test_windows_resolver_can_use_ps1_when_it_is_the_only_wrapper(self):
        completed = SimpleNamespace(returncode=0, stdout="codex 1.0", stderr="")
        run = Mock(return_value=completed)
        resolver = ExecutableResolver(
            which=lambda candidate: "C:/tools/codex.ps1" if candidate == "codex.ps1" else None,
            run=run,
            environment={},
            platform_name="Windows",
        )
        result = resolver.resolve("codex")
        self.assertTrue(result.runnable)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("-File", command)
        self.assertEqual(command[-2:], [str(Path("C:/tools/codex.ps1")), "--version"])
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_appdata_npm_fallback_works_when_path_lookup_fails(self):
        expected = Path("C:/Users/test/AppData/Roaming/npm/codex.cmd")
        resolver = ExecutableResolver(
            which=lambda _candidate: None,
            probe=lambda executable: executable == str(expected),
            environment={"APPDATA": "C:/Users/test/AppData/Roaming"},
            platform_name="Windows",
            path_exists=lambda candidate: candidate == expected,
        )
        result = resolver.resolve("codex")
        self.assertTrue(result.runnable)
        self.assertEqual(result.executable, str(expected))
        self.assertIn("%APPDATA%", result.source)
        self.assertFalse(result.path_visible)

    def test_npm_prefix_global_fallback_works(self):
        expected = Path("C:/custom/npm/codex.cmd")
        run = Mock(return_value=SimpleNamespace(
            returncode=0,
            stdout="C:/custom/npm\n",
            stderr="",
        ))
        resolver = ExecutableResolver(
            which=lambda candidate: "C:/node/npm.cmd" if candidate == "npm.cmd" else None,
            run=run,
            probe=lambda executable: executable == str(expected),
            environment={},
            platform_name="Windows",
            path_exists=lambda candidate: candidate == expected,
        )
        result = resolver.resolve("codex")
        self.assertTrue(result.runnable)
        self.assertEqual(result.executable, str(expected))
        self.assertIn("npm prefix -g", result.source)
        npm_command = run.call_args.args[0]
        self.assertEqual(npm_command[0], "cmd.exe")
        self.assertEqual(npm_command[-2:], ["prefix", "-g"])

    def test_version_probe_uses_bounded_no_shell_invocation(self):
        run = Mock(return_value=SimpleNamespace(
            returncode=0,
            stdout="codex-cli 1.2.3\n",
            stderr="",
        ))
        resolver = ExecutableResolver(
            which=lambda candidate: "C:/tools/codex.cmd" if candidate == "codex.cmd" else None,
            run=run,
            environment={"COMSPEC": "C:/Windows/System32/cmd.exe"},
            platform_name="Windows",
        )
        result = resolver.resolve("codex")
        self.assertTrue(result.runnable)
        self.assertEqual(result.version, "codex-cli 1.2.3")
        self.assertEqual(run.call_args.kwargs["timeout"], 3.0)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.args[0][-1], "--version")

    def test_version_text_on_stderr_is_accepted(self):
        resolver = ExecutableResolver(
            which=lambda candidate: "/usr/local/bin/codex" if candidate == "codex" else None,
            run=Mock(return_value=SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="codex-cli 2.0\n",
            )),
            environment={},
            platform_name="Linux",
        )
        result = resolver.resolve("codex")
        self.assertTrue(result.runnable)
        self.assertEqual(result.version, "codex-cli 2.0")

    def test_launch_failure_retains_path_and_safe_diagnostic(self):
        resolver = ExecutableResolver(
            which=lambda candidate: "C:/tools/codex.cmd" if candidate == "codex.cmd" else None,
            run=Mock(side_effect=OSError("private host detail")),
            environment={},
            platform_name="Windows",
        )
        result = resolver.resolve("codex")
        self.assertFalse(result.runnable)
        self.assertEqual(result.diagnostic, "launch_failed")
        self.assertNotIn("private", result.diagnostic)

    def test_non_windows_behavior_checks_only_extensionless_command(self):
        which = Mock(return_value="/usr/local/bin/codex")
        resolved = resolve_codex_executable(which=which, platform_name="Linux")
        self.assertEqual(resolved, Path("/usr/local/bin/codex"))
        which.assert_called_once_with("codex")

    def test_status_distinguishes_ready_broken_clis_desktops_and_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service(
                tmp,
                commands={
                    "codex.cmd": "C:/tools/codex.cmd",
                    "claude.cmd": "C:/tools/claude.cmd",
                    "gemini.cmd": "C:/tools/gemini.cmd",
                },
                runnable={"C:/tools/codex.cmd", "C:/tools/gemini.cmd"},
                applications=("ChatGPT",),
            )
            engines = {engine.engine_id: engine for engine in service.status()}

            self.assertEqual(engines["codex"].status, ENGINE_STATUS_READY)
            self.assertTrue(engines["codex"].ready_for_implementation)
            self.assertEqual(engines["chatgpt_desktop"].status, ENGINE_STATUS_INSTALLED)
            self.assertFalse(engines["chatgpt_desktop"].cli_support)
            self.assertEqual(
                engines["claude_code"].status,
                ENGINE_STATUS_INSTALLED_NOT_EXECUTABLE,
            )
            self.assertTrue(engines["claude_code"].installed)
            self.assertEqual(engines["gemini_cli"].status, ENGINE_STATUS_READY)
            self.assertFalse(engines["gemini_cli"].implementation_supported)
            self.assertEqual(engines["python"].status, ENGINE_STATUS_READY)

    def test_codex_alias_that_cannot_launch_is_installed_but_not_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service(
                tmp,
                commands={
                    "codex.exe": "C:/Program Files/WindowsApps/OpenAI.Codex_1.0/codex.exe"
                },
                runnable=(),
                packages=("OpenAI.Codex",),
            )
            codex = service.engine("codex")
            self.assertTrue(codex.installed)
            self.assertEqual(codex.status, ENGINE_STATUS_INSTALLED_NOT_EXECUTABLE)
            self.assertEqual(codex.reason, "launch_failed")
            self.assertTrue(service.engine("codex_desktop").installed)
            with self.assertRaises(ExecutionEngineUnavailable):
                service.require_codex()

    def test_store_codex_package_is_not_misreported_as_chatgpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service(tmp, packages=("OpenAI.Codex",))
            engines = {engine.engine_id: engine for engine in service.status()}
            self.assertTrue(engines["codex_desktop"].installed)
            self.assertEqual(engines["codex_desktop"].discovery_source, "Store package")
            self.assertFalse(engines["chatgpt_desktop"].installed)
            self.assertEqual(engines["chatgpt_desktop"].status, ENGINE_STATUS_NOT_INSTALLED)

    def test_windows_app_detector_uses_bounded_no_shell_package_query(self):
        run = Mock(return_value=SimpleNamespace(
            returncode=0,
            stdout="OpenAI.Codex\nMicrosoft.WindowsCalculator\n",
            stderr="",
        ))
        detector = WindowsAppDetector(
            which=lambda candidate: "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            if candidate == "powershell.exe" else None,
            run=run,
            environment={},
            platform_name="Windows",
        )
        result = detector.detect()
        self.assertTrue(result.available)
        self.assertIn("OpenAI.Codex", result.packages)
        self.assertEqual(run.call_args.kwargs["timeout"], 5.0)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertIn("Get-AppxPackage", run.call_args.args[0][-1])

    def test_chatgpt_absence_and_appx_query_failure_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            unavailable = self.service(tmp, app_detection_available=False)
            self.assertEqual(
                unavailable.engine("chatgpt_desktop").status,
                ENGINE_STATUS_DETECTION_ERROR,
            )
            available = self.service(tmp)
            self.assertEqual(
                available.engine("chatgpt_desktop").status,
                ENGINE_STATUS_NOT_INSTALLED,
            )

    def test_gemini_and_claude_use_the_reusable_windows_resolver(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service(
                tmp,
                commands={
                    "claude.ps1": "C:/npm/claude.ps1",
                    "gemini.cmd": "C:/npm/gemini.cmd",
                },
                runnable={"C:/npm/claude.ps1", "C:/npm/gemini.cmd"},
            )
            self.assertEqual(service.engine("claude_code").executable, str(Path("C:/npm/claude.ps1")))
            self.assertEqual(service.engine("gemini_cli").executable, str(Path("C:/npm/gemini.cmd")))

    def test_selected_engine_requires_an_implemented_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service(
                tmp,
                commands={"claude.cmd": "C:/tools/claude.cmd"},
                runnable={"C:/tools/claude.cmd"},
                values={"execution.default_engine": "claude_code"},
            )
            self.assertEqual(service.selected_engine_id, "claude_code")
            self.assertIsNone(service.selected_engine())

    def test_detection_errors_are_isolated_and_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = ExecutionEngineService(
                FlatConfig(),
                application_catalog=SimpleNamespace(
                    applications=Mock(side_effect=ValueError("secret"))
                ),
                which=Mock(side_effect=OSError("private path")),
                environment={},
                platform_name="Windows",
                python_executable=str(Path(tmp) / "missing-python"),
                windows_app_detector=FakeAppDetector(available=False),
            )
            engines = service.status()
            self.assertTrue(all("secret" not in engine.reason for engine in engines))
            self.assertTrue(all("private" not in engine.reason for engine in engines))

    def test_execution_status_renders_capabilities_paths_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self.service(
                tmp,
                commands={"codex.cmd": "C:/npm/codex.cmd"},
                runnable={"C:/npm/codex.cmd"},
                packages=("OpenAI.Codex",),
            )
            router = CommandRouter(SimpleNamespace(execution_engines=service))
            with patch("builtins.print") as output:
                router.handle("execution status")
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Codex CLI\nStatus:\nReady", rendered)
            self.assertIn("Executable:\n" + str(Path("C:/npm/codex.cmd")), rendered)
            self.assertIn("PATH Visibility:\nYes", rendered)
            self.assertIn("Discovery Source:\nPATH", rendered)
            self.assertIn("Version Probe:\nSucceeded", rendered)
            self.assertIn(
                "Codex Desktop\nStatus:\nInstalled\nCLI Support:\nSeparate CLI detected",
                rendered,
            )
            self.assertIn("ChatGPT Desktop\nStatus:\nNot Installed\nCLI Support:\nNo", rendered)

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
