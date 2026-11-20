"""Tests for explicit retry with end-to-end verification (Orion v0.4 item 10)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orion.application.commands.observability_cli import (
    ObservabilityCliAdapter,
    dispatch_observability,
)
from orion.application.observability.backup import BackupService
from orion.application.observability.handler import ObservabilityApplicationHandler
from orion.application.observability.health import (
    HealthCheck,
    HealthReport,
    HealthState,
)
from orion.application.observability.retry import (
    DEFAULT_MAX_ATTEMPTS,
    MAX_ATTEMPTS_LIMIT,
    RetryOutcome,
    RetryPolicy,
    RetryRunner,
)
from orion.core.paths import OrionPaths


def _report(states):
    checks = [
        HealthCheck(name=name, state=state, detail=f"{name} is {state.value}")
        for name, state in states.items()
    ]
    return HealthReport(
        generated_at="2026-11-20T00:00:00",
        checks=tuple(checks),
    )


def _make_handler(tmpdir, provider_manager=None, config_manager=None):
    paths = OrionPaths(user_root=Path(tmpdir))
    return ObservabilityApplicationHandler(
        paths=paths,
        config_manager=config_manager
        or SimpleNamespace(get=lambda key, default=None: ""),
        provider_manager=provider_manager or SimpleNamespace(
            statuses=lambda: [],
            test_connection=lambda key: [],
        ),
        services=SimpleNamespace(names=lambda: []),
        event_store=SimpleNamespace(
            is_enabled=lambda: True,
            latest=None,
            history=lambda **kwargs: SimpleNamespace(events=[], total=0, limit=0),
        ),
    )


class RetryPolicyTests(unittest.TestCase):
    def test_defaults(self):
        policy = RetryPolicy()
        self.assertEqual(policy.max_attempts, DEFAULT_MAX_ATTEMPTS)
        self.assertEqual(policy.delay_seconds, 0.0)

    def test_rejects_out_of_range_attempts(self):
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=0)
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=MAX_ATTEMPTS_LIMIT + 1)

    def test_rejects_negative_delay(self):
        with self.assertRaises(ValueError):
            RetryPolicy(delay_seconds=-1.0)


class RetryRunnerTests(unittest.TestCase):
    def test_first_attempt_success_stops_immediately(self):
        calls = []

        def attempt():
            calls.append(1)
            return RetryOutcome(True, "done")

        result = RetryRunner().run(
            "op", "target", attempt_fn=attempt,
            verify_fn=lambda outcome: RetryOutcome(True, "verified"),
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.succeeded)
        self.assertTrue(result.verified)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(result.attempts), 1)

    def test_retries_until_success(self):
        calls = []

        def attempt():
            calls.append(1)
            if len(calls) < 3:
                return RetryOutcome(False, "not yet")
            return RetryOutcome(True, "done")

        result = RetryRunner().run(
            "op", "target", attempt_fn=attempt,
            verify_fn=lambda outcome: RetryOutcome(True, "verified"),
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.attempts), 3)
        self.assertFalse(result.attempts[0].ok)
        self.assertTrue(result.attempts[2].ok)

    def test_all_attempts_fail_reports_unverified(self):
        result = RetryRunner(policy=RetryPolicy(max_attempts=2)).run(
            "op", "target",
            attempt_fn=lambda: RetryOutcome(False, "still broken"),
            verify_fn=lambda outcome: RetryOutcome(True, "never reached"),
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.succeeded)
        self.assertFalse(result.verified)
        self.assertEqual(len(result.attempts), 2)
        self.assertIn("nothing to verify", result.verification)

    def test_exception_in_attempt_is_recorded_not_raised(self):
        def attempt():
            raise ConnectionError("boom")

        result = RetryRunner(policy=RetryPolicy(max_attempts=2)).run(
            "op", "target", attempt_fn=attempt,
        )
        self.assertFalse(result.ok)
        self.assertEqual(len(result.attempts), 2)
        self.assertIn("ConnectionError", result.attempts[0].detail)

    def test_verification_failure_marks_not_ok(self):
        result = RetryRunner().run(
            "op", "target",
            attempt_fn=lambda: RetryOutcome(True, "done"),
            verify_fn=lambda outcome: RetryOutcome(False, "check failed"),
        )
        self.assertTrue(result.succeeded)
        self.assertFalse(result.verified)
        self.assertFalse(result.ok)
        self.assertEqual(result.verification, "check failed")

    def test_exception_in_verify_is_not_verified(self):
        def verify(outcome):
            raise RuntimeError("verify crashed")

        result = RetryRunner().run(
            "op", "target",
            attempt_fn=lambda: RetryOutcome(True, "done"),
            verify_fn=verify,
        )
        self.assertFalse(result.verified)
        self.assertIn("RuntimeError", result.verification)

    def test_delay_sleeps_between_attempts_only(self):
        sleeps = []
        calls = []

        def attempt():
            calls.append(1)
            return RetryOutcome(len(calls) == 3, "done" if len(calls) == 3 else "no")

        RetryRunner(
            policy=RetryPolicy(max_attempts=3, delay_seconds=2.0),
            sleep=sleeps.append,
        ).run("op", "target", attempt_fn=attempt)
        self.assertEqual(sleeps, [2.0, 2.0])

    def test_result_to_dict_is_json_safe(self):
        result = RetryRunner().run(
            "op", "target",
            attempt_fn=lambda: RetryOutcome(True, "done"),
            verify_fn=lambda outcome: RetryOutcome(True, "verified"),
        )
        data = json.loads(json.dumps(result.to_dict()))
        self.assertEqual(data["operation"], "op")
        self.assertTrue(data["verified"])


class RetryProviderTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.addCleanup(self._env.stop)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def _provider_manager(self, attempts, model="model-a"):
        calls = {"count": 0}

        def test_connection(key):
            calls["count"] += 1
            if calls["count"] < attempts:
                raise ConnectionError("network down")
            return ["model-a", "model-b"]

        return SimpleNamespace(
            statuses=lambda: [
                SimpleNamespace(key="ollama", configured=True, active=True, model=model)
            ],
            test_connection=test_connection,
        )

    def test_provider_retry_succeeds_after_transient_failure(self):
        handler = _make_handler(
            self._tmpdir.name,
            provider_manager=self._provider_manager(2),
            config_manager=SimpleNamespace(
                get=lambda key, default=None: "model-a"
            ),
        )
        result = handler.retry_provider("ollama")
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["attempts"]), 2)
        self.assertTrue(result.data["verified"])
        self.assertIn("succeeded and verified", result.message)

    def test_provider_retry_all_fail(self):
        handler = _make_handler(
            self._tmpdir.name,
            provider_manager=self._provider_manager(99),
        )
        result = handler.retry_provider("ollama", max_attempts=2)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.data["attempts"]), 2)
        self.assertIn("failed after all attempts", result.message)

    def test_provider_verify_fails_when_model_missing(self):
        handler = _make_handler(
            self._tmpdir.name,
            provider_manager=self._provider_manager(1),
            config_manager=SimpleNamespace(
                get=lambda key, default=None: "model-z"
            ),
        )
        result = handler.retry_provider("ollama")
        self.assertFalse(result.ok)
        self.assertTrue(result.data["succeeded"])
        self.assertFalse(result.data["verified"])
        self.assertIn("model-z", result.message)

    def test_unknown_provider_fails(self):
        handler = _make_handler(self._tmpdir.name)
        result = handler.retry_provider("nope")
        self.assertFalse(result.ok)

    def test_invalid_attempts_fails(self):
        handler = _make_handler(self._tmpdir.name)
        result = handler.retry_provider("ollama", max_attempts=0)
        self.assertFalse(result.ok)


class RetryHealthTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.addCleanup(self._env.stop)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.handler = _make_handler(self._tmpdir.name)

    def test_health_retry_succeeds_when_ok(self):
        with patch.object(self.handler.checker, "check") as check:
            check.return_value = _report({"disk": HealthState.OK})
            result = self.handler.retry_health("disk")
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["attempts"]), 1)

    def test_health_retry_recovers_on_second_attempt(self):
        reports = [
            _report({"disk": HealthState.WARNING}),
            _report({"disk": HealthState.OK}),
            _report({"disk": HealthState.OK}),
        ]
        with patch.object(self.handler.checker, "check", side_effect=reports):
            result = self.handler.retry_health("disk")
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["attempts"]), 2)

    def test_health_retry_fails_when_problem_persists(self):
        with patch.object(self.handler.checker, "check") as check:
            check.return_value = _report({"disk": HealthState.ERROR})
            result = self.handler.retry_health("disk", max_attempts=2)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.data["attempts"]), 2)
        self.assertIn("runbooks diagnose", result.next_actions)

    def test_health_verify_fails_when_recovery_does_not_hold(self):
        reports = [
            _report({"disk": HealthState.OK}),
            _report({"disk": HealthState.WARNING}),
        ]
        with patch.object(self.handler.checker, "check", side_effect=reports):
            result = self.handler.retry_health("disk")
        self.assertFalse(result.ok)
        self.assertTrue(result.data["succeeded"])
        self.assertFalse(result.data["verified"])

    def test_unknown_section_fails(self):
        result = self.handler.retry_health("nope")
        self.assertFalse(result.ok)


class RetryBackupTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.addCleanup(self._env.stop)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.paths = OrionPaths(user_root=Path(self._tmpdir.name))
        self.handler = _make_handler(self._tmpdir.name)
        (Path(self._tmpdir.name) / "config.yaml").write_text(
            "ai:\n  default_provider: ollama\n", encoding="utf-8"
        )

    def test_backup_retry_verifies_good_archive(self):
        service = BackupService(self.paths)
        export = service.export()
        result = self.handler.retry_backup(str(export.path))
        self.assertTrue(result.ok)
        self.assertTrue(result.data["verified"])
        self.assertIn("succeeded and verified", result.message)

    def test_backup_retry_fails_for_missing_archive(self):
        missing = str(Path(self._tmpdir.name) / "nope.zip")
        result = self.handler.retry_backup(missing, max_attempts=2)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.data["attempts"]), 2)

    def test_backup_retry_needs_a_path(self):
        result = self.handler.retry_backup("")
        self.assertFalse(result.ok)


class RetryCliTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.addCleanup(self._env.stop)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.handler = _make_handler(self._tmpdir.name)
        # CLI tests exercise parsing and dispatch, not the checker itself.
        self._check = patch.object(
            self.handler.checker,
            "check",
            return_value=_report({"disk": HealthState.OK}),
        )
        self._check.start()
        self.addCleanup(self._check.stop)
        self.runtime = SimpleNamespace(observability_application=self.handler)
        self.output = []
        self.adapter = ObservabilityCliAdapter(
            self.runtime, output_provider=self.output.append
        )

    def _run(self, raw):
        self.output.clear()
        return self.adapter.handle(raw)

    def test_retry_health_runs(self):
        result = self._run("retry health")
        self.assertTrue(result.ok)

    def test_retry_health_with_section_and_attempts(self):
        result = self._run("retry health disk --attempts 1")
        self.assertTrue(result.ok)

    def test_retry_provider_runs(self):
        result = self._run("retry provider ollama --attempts 1")
        # The fake provider returns no models and no configured model,
        # so the connection test "succeeds" and verification passes.
        self.assertTrue(result.ok)

    def test_retry_backup_missing_fails(self):
        result = self._run("retry backup C:\\nope.zip --attempts 1")
        self.assertFalse(result.ok)

    def test_retry_rejects_bad_attempts(self):
        self.assertFalse(self._run("retry health --attempts 9").ok)
        self.assertFalse(self._run("retry health --attempts nope").ok)
        self.assertFalse(self._run("retry health --attempts").ok)

    def test_retry_rejects_unknown_operation(self):
        self.assertFalse(self._run("retry frobnicate").ok)

    def test_retry_rejects_unknown_option(self):
        self.assertFalse(self._run("retry health --nope").ok)

    def test_retry_needs_operation(self):
        self.assertFalse(self._run("retry").ok)

    def test_retry_json(self):
        result = self._run("retry health disk --attempts 1 --json")
        self.assertTrue(result.ok)
        data = json.loads("".join(self.output))
        self.assertEqual(data["data"]["operation"], "health")

    def test_dispatch_retry(self):
        self.assertTrue(dispatch_observability(self.runtime, "retry health disk --attempts 1"))


class RetryCoverageTests(unittest.TestCase):
    def test_help_and_completion_cover_retry(self):
        from orion.core.router import CommandRouter
        from orion.ui.console import BASE_COMMANDS

        self.assertIn("retry", BASE_COMMANDS)
        router = CommandRouter(
            SimpleNamespace(
                plugin_manager=SimpleNamespace(help_lines=lambda: ()),
            )
        )
        with patch("builtins.print") as mock_print:
            router.show_help()
        text = "\n".join(
            " ".join(str(arg) for arg in call.args)
            for call in mock_print.call_args_list
        )
        self.assertIn("retry", text)


if __name__ == "__main__":
    unittest.main()
