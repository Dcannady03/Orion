"""Tests for the diagnostic runbooks command (Orion v0.4 item 9)."""

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
from orion.application.observability.handler import ObservabilityApplicationHandler
from orion.application.observability.health import (
    HealthCheck,
    HealthReport,
    HealthState,
)
from orion.application.observability.runbooks import (
    RUNBOOKS,
    find_runbook,
    match_runbooks,
    suggest_runbooks,
)
from orion.core.paths import OrionPaths


def _make_handler(tmpdir):
    paths = OrionPaths(user_root=Path(tmpdir))
    return ObservabilityApplicationHandler(
        paths=paths,
        config_manager=SimpleNamespace(get=lambda key, default=None: "ollama"),
        provider_manager=SimpleNamespace(statuses=lambda: []),
        services=SimpleNamespace(names=lambda: []),
        event_store=SimpleNamespace(
            is_enabled=lambda: True,
            latest=None,
            history=lambda **kwargs: SimpleNamespace(events=[], total=0, limit=0),
        ),
    )


def _report(states):
    checks = [
        HealthCheck(
            name=name,
            state=state,
            detail=f"{name} is {state.value}",
        )
        for name, state in states.items()
    ]
    return HealthReport(
        generated_at="2026-11-20T00:00:00",
        checks=tuple(checks),
    )


class RunbookCatalogTests(unittest.TestCase):
    def test_catalog_is_not_empty_and_every_runbook_is_complete(self):
        self.assertGreaterEqual(len(RUNBOOKS), 4)
        seen = set()
        for runbook in RUNBOOKS:
            self.assertNotIn(runbook.runbook_id, seen)
            seen.add(runbook.runbook_id)
            self.assertTrue(runbook.title)
            self.assertTrue(runbook.symptom)
            self.assertGreaterEqual(len(runbook.steps), 2)
            self.assertGreaterEqual(len(runbook.causes), 1)
            for step in runbook.steps:
                self.assertTrue(step.title)
                self.assertTrue(step.command)
                self.assertTrue(step.look_for)
            for cause in runbook.causes:
                self.assertTrue(cause.cause)
                self.assertTrue(cause.evidence)
                self.assertTrue(cause.fix)

    def test_find_runbook_is_case_insensitive(self):
        self.assertIsNotNone(find_runbook("DISK-FULL"))
        self.assertEqual(find_runbook("disk-full").runbook_id, "disk-full")
        self.assertIsNone(find_runbook("no-such-runbook"))

    def test_runbook_to_dict_is_json_safe(self):
        data = json.loads(json.dumps(RUNBOOKS[0].to_dict()))
        self.assertEqual(data["id"], RUNBOOKS[0].runbook_id)
        self.assertIn("steps", data)
        self.assertIn("causes", data)

    def test_runbook_steps_reference_real_commands(self):
        # Every step command must start with a real Orion command word.
        known = {
            "health", "logs", "diagnostics", "graphs", "backup",
            "audit", "ai", "runbooks", "ollama",
        }
        for runbook in RUNBOOKS:
            for step in runbook.steps:
                first = step.command.split()[0].strip("`")
                self.assertIn(first, known, step.command)


class MatchRunbooksTests(unittest.TestCase):
    def test_match_by_symptom_keyword(self):
        matched = match_runbooks("provider is unavailable")
        self.assertTrue(matched)
        self.assertEqual(matched[0].runbook_id, "provider-unavailable")

    def test_match_by_disk_keyword(self):
        matched = match_runbooks("disk space is low")
        self.assertTrue(matched)
        self.assertEqual(matched[0].runbook_id, "disk-full")

    def test_no_match_returns_empty(self):
        self.assertEqual(match_runbooks("purple elephants dancing"), [])
        self.assertEqual(match_runbooks(""), [])

    def test_best_match_first(self):
        matched = match_runbooks("orion fails to start config")
        self.assertTrue(matched)
        self.assertEqual(matched[0].runbook_id, "startup-failure")


class SuggestRunbooksTests(unittest.TestCase):
    def test_all_ok_suggests_nothing(self):
        report = _report(
            {
                "disk": HealthState.OK,
                "providers": HealthState.OK,
                "services": HealthState.OK,
                "config": HealthState.OK,
                "events": HealthState.OK,
            }
        )
        self.assertEqual(suggest_runbooks(report), [])

    def test_providers_warning_suggests_provider_runbook(self):
        report = _report({"providers": HealthState.WARNING, "disk": HealthState.OK})
        suggestions = suggest_runbooks(report)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0][0].runbook_id, "provider-unavailable")
        self.assertIn("providers", suggestions[0][1])

    def test_multiple_problems_suggest_in_fixed_order(self):
        report = _report(
            {
                "disk": HealthState.ERROR,
                "providers": HealthState.WARNING,
                "services": HealthState.OK,
                "config": HealthState.OK,
                "events": HealthState.WARNING,
            }
        )
        ids = [runbook.runbook_id for runbook, _ in suggest_runbooks(report)]
        self.assertEqual(
            ids, ["provider-unavailable", "disk-full", "events-unhealthy"]
        )

    def test_unknown_does_not_suggest(self):
        report = _report({"providers": HealthState.UNKNOWN, "disk": HealthState.OK})
        self.assertEqual(suggest_runbooks(report), [])


class RunbooksHandlerTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.addCleanup(self._env.stop)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.handler = _make_handler(self._tmpdir.name)

    def test_list_returns_all_runbooks(self):
        result = self.handler.runbooks_list()
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["runbooks"]), len(RUNBOOKS))
        self.assertTrue(result.data["read_only"])
        for runbook in RUNBOOKS:
            self.assertIn(runbook.runbook_id, result.message)

    def test_show_returns_steps_and_causes(self):
        result = self.handler.runbooks_show("disk-full")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["id"], "disk-full")
        self.assertIn("Checks:", result.message)
        self.assertIn("Likely causes:", result.message)
        self.assertIn("health disk", result.message)

    def test_show_unknown_runbook_fails(self):
        result = self.handler.runbooks_show("nope")
        self.assertFalse(result.ok)
        self.assertIn("nope", result.message)

    def test_match_returns_ranked_runbooks(self):
        result = self.handler.runbooks_match("provider not responding")
        self.assertTrue(result.ok)
        self.assertGreaterEqual(len(result.data["runbooks"]), 1)
        self.assertEqual(
            result.data["runbooks"][0]["id"], "provider-unavailable"
        )

    def test_diagnose_with_clean_health_suggests_nothing(self):
        with patch.object(self.handler.checker, "check") as check:
            check.return_value = _report(
                {
                    "disk": HealthState.OK,
                    "providers": HealthState.OK,
                    "services": HealthState.OK,
                    "config": HealthState.OK,
                    "events": HealthState.OK,
                }
            )
            result = self.handler.runbooks_diagnose()
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["suggestions"]), 0)

    def test_diagnose_with_problems_suggests_runbooks(self):
        with patch.object(self.handler.checker, "check") as check:
            check.return_value = _report(
                {
                    "disk": HealthState.ERROR,
                    "providers": HealthState.OK,
                    "services": HealthState.OK,
                    "config": HealthState.OK,
                    "events": HealthState.OK,
                }
            )
            result = self.handler.runbooks_diagnose()
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["suggestions"]), 1)
        self.assertEqual(result.data["suggestions"][0]["id"], "disk-full")
        self.assertIn("disk-full", result.message)


class RunbooksCliTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.addCleanup(self._env.stop)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.handler = _make_handler(self._tmpdir.name)
        self.runtime = SimpleNamespace(observability_application=self.handler)
        self.output = []
        self.adapter = ObservabilityCliAdapter(
            self.runtime, output_provider=self.output.append
        )

    def _run(self, raw):
        self.output.clear()
        return self.adapter.handle(raw)

    def test_runbooks_lists(self):
        result = self._run("runbooks")
        self.assertTrue(result.ok)
        self.assertIn("disk-full", "".join(self.output))

    def test_runbooks_show_id(self):
        result = self._run("runbooks disk-full")
        self.assertTrue(result.ok)
        self.assertIn("Disk space warnings", "".join(self.output))

    def test_runbooks_for_matches(self):
        result = self._run("runbooks for provider unavailable")
        self.assertTrue(result.ok)
        self.assertIn("provider-unavailable", "".join(self.output))

    def test_runbooks_for_needs_description(self):
        result = self._run("runbooks for")
        self.assertFalse(result.ok)

    def test_runbooks_diagnose_runs(self):
        result = self._run("runbooks diagnose")
        self.assertTrue(result.ok)

    def test_runbooks_json(self):
        result = self._run("runbooks disk-full --json")
        self.assertTrue(result.ok)
        data = json.loads("".join(self.output))
        self.assertEqual(data["data"]["id"], "disk-full")

    def test_runbooks_rejects_unknown_option(self):
        result = self._run("runbooks --nope")
        self.assertFalse(result.ok)

    def test_dispatch_runbooks(self):
        self.assertTrue(dispatch_observability(self.runtime, "runbooks"))
        self.assertTrue(dispatch_observability(self.runtime, "runbooks disk-full"))


class RunbooksCoverageTests(unittest.TestCase):
    def test_help_and_completion_cover_runbooks(self):
        from orion.core.router import CommandRouter
        from orion.ui.console import BASE_COMMANDS

        self.assertIn("runbooks", BASE_COMMANDS)
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
        self.assertIn("runbooks", text)


if __name__ == "__main__":
    unittest.main()
