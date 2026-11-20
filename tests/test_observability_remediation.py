"""Tests for approval-based remediation (Orion v0.4 item 11)."""

import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta
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
from orion.application.observability.recovery import RecoveryHistoryStore
from orion.application.observability.remediation import (
    EVENT_FILE_MAX_AGE_DAYS,
    KEEP_BACKUPS,
    PROPOSAL_CREATE_WORKSPACE,
    PROPOSAL_PRUNE_BACKUPS,
    PROPOSAL_PRUNE_EVENTS,
    PROPOSAL_SWITCH_PROVIDER,
    RemediationEngine,
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


class _FakeChecker:
    """Return one report for full checks and another for section checks."""

    def __init__(self, full_report, section_report=None):
        self.full_report = full_report
        self.section_report = (
            section_report if section_report is not None else full_report
        )
        self.calls = []

    def check(self, section=None):
        self.calls.append(section)
        return self.full_report if section is None else self.section_report


def _provider_manager(active="openai", activate_ok=True):
    def activate(key):
        if not activate_ok:
            raise ConnectionError("Ollama is not running")
        return SimpleNamespace(name=lambda: key.title())

    return SimpleNamespace(
        statuses=lambda: [
            SimpleNamespace(
                key="ollama", configured=True, active=(active == "ollama"),
                model="llama3",
            ),
            SimpleNamespace(
                key="openai", configured=True, active=(active == "openai"),
                model="gpt-4o",
            ),
        ],
        test_connection=lambda key: ["llama3"],
        activate=activate,
    )


def _make_handler(tmpdir, checker=None, config_manager=None, provider_manager=None):
    paths = OrionPaths(user_root=Path(tmpdir))
    handler = ObservabilityApplicationHandler(
        paths=paths,
        config_manager=config_manager
        or SimpleNamespace(get=lambda key, default=None: ""),
        provider_manager=provider_manager or _provider_manager(),
        services=SimpleNamespace(names=lambda: []),
        event_store=SimpleNamespace(
            is_enabled=lambda: True,
            latest=None,
            history=lambda **kwargs: SimpleNamespace(events=[], total=0, limit=0),
        ),
    )
    if checker is not None:
        handler.checker = checker
    return handler


def _write_zip(path, mtime):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "{}")
    os.utime(path, (mtime, mtime))


class ProposeTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.addCleanup(self._env.stop)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.paths = OrionPaths(user_root=Path(self._tmpdir.name))

    def _engine(self, full, section=None, **kwargs):
        checker = _FakeChecker(full, section)
        return RemediationEngine(
            paths=self.paths,
            checker=checker,
            config_manager=kwargs.get("config_manager"),
            provider_manager=kwargs.get("provider_manager", _provider_manager()),
        )

    def test_all_ok_proposes_nothing(self):
        engine = self._engine(_report({"disk": HealthState.OK}))
        plan = engine.propose()
        self.assertEqual(plan.proposals, ())

    def test_providers_problem_proposes_ollama_switch(self):
        engine = self._engine(_report({"providers": HealthState.ERROR}))
        plan = engine.propose()
        self.assertEqual(len(plan.proposals), 1)
        proposal = plan.proposals[0]
        self.assertEqual(proposal.proposal_id, PROPOSAL_SWITCH_PROVIDER)
        self.assertEqual(proposal.risk, "low")
        self.assertTrue(proposal.reversible)

    def test_no_switch_when_ollama_already_active(self):
        engine = self._engine(
            _report({"providers": HealthState.ERROR}),
            provider_manager=_provider_manager(active="ollama"),
        )
        self.assertEqual(engine.propose().proposals, ())

    def test_disk_problem_with_many_backups_proposes_prune(self):
        backups = self.paths.backups
        backups.mkdir(parents=True, exist_ok=True)
        now = datetime.now().timestamp()
        for index in range(4):
            _write_zip(backups / f"backup-{index}.zip", now - index * 86400)
        engine = self._engine(_report({"disk": HealthState.ERROR}))
        plan = engine.propose()
        ids = [item.proposal_id for item in plan.proposals]
        self.assertIn(PROPOSAL_PRUNE_BACKUPS, ids)
        proposal = next(
            item for item in plan.proposals
            if item.proposal_id == PROPOSAL_PRUNE_BACKUPS
        )
        self.assertEqual(len(proposal.details["files"]), 2)
        self.assertFalse(proposal.reversible)
        self.assertEqual(proposal.risk, "medium")

    def test_disk_problem_with_few_backups_proposes_no_prune(self):
        backups = self.paths.backups
        backups.mkdir(parents=True, exist_ok=True)
        now = datetime.now().timestamp()
        for index in range(KEEP_BACKUPS):
            _write_zip(backups / f"backup-{index}.zip", now - index * 86400)
        engine = self._engine(_report({"disk": HealthState.ERROR}))
        ids = [item.proposal_id for item in engine.propose().proposals]
        self.assertNotIn(PROPOSAL_PRUNE_BACKUPS, ids)

    def test_disk_ok_proposes_no_prune(self):
        backups = self.paths.backups
        backups.mkdir(parents=True, exist_ok=True)
        now = datetime.now().timestamp()
        for index in range(4):
            _write_zip(backups / f"backup-{index}.zip", now - index * 86400)
        engine = self._engine(_report({"disk": HealthState.OK}))
        ids = [item.proposal_id for item in engine.propose().proposals]
        self.assertNotIn(PROPOSAL_PRUNE_BACKUPS, ids)

    def test_disk_problem_with_old_events_proposes_prune(self):
        events = self.paths.events
        events.mkdir(parents=True, exist_ok=True)
        old = datetime.now() - timedelta(days=EVENT_FILE_MAX_AGE_DAYS + 10)
        fresh = datetime.now() - timedelta(days=1)
        old_file = events / "2026-08-01.jsonl"
        old_file.write_text("{}\n", encoding="utf-8")
        os.utime(old_file, (old.timestamp(), old.timestamp()))
        fresh_file = events / "2026-11-19.jsonl"
        fresh_file.write_text("{}\n", encoding="utf-8")
        os.utime(fresh_file, (fresh.timestamp(), fresh.timestamp()))
        engine = self._engine(_report({"disk": HealthState.WARNING}))
        plan = engine.propose()
        proposal = next(
            item for item in plan.proposals
            if item.proposal_id == PROPOSAL_PRUNE_EVENTS
        )
        self.assertEqual(proposal.details["files"], ["2026-08-01.jsonl"])

    def test_workspace_problem_proposes_create(self):
        missing = Path(self._tmpdir.name) / "workspace"
        config = SimpleNamespace(
            get=lambda key, default=None: str(missing)
            if key == "workspace.default_path" else ""
        )
        engine = self._engine(
            _report({"workspace": HealthState.WARNING}),
            config_manager=config,
        )
        plan = engine.propose()
        self.assertEqual(len(plan.proposals), 1)
        self.assertEqual(plan.proposals[0].proposal_id, PROPOSAL_CREATE_WORKSPACE)
        self.assertTrue(plan.proposals[0].reversible)

    def test_workspace_problem_with_existing_path_proposes_nothing(self):
        existing = Path(self._tmpdir.name)
        config = SimpleNamespace(
            get=lambda key, default=None: str(existing)
            if key == "workspace.default_path" else ""
        )
        engine = self._engine(
            _report({"workspace": HealthState.WARNING}),
            config_manager=config,
        )
        self.assertEqual(engine.propose().proposals, ())

    def test_plan_to_dict_is_json_safe(self):
        engine = self._engine(_report({"providers": HealthState.ERROR}))
        data = json.loads(json.dumps(engine.propose().to_dict()))
        self.assertEqual(len(data["proposals"]), 1)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.addCleanup(self._env.stop)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.paths = OrionPaths(user_root=Path(self._tmpdir.name))

    def _engine(self, full, section=None, **kwargs):
        checker = _FakeChecker(full, section)
        return RemediationEngine(
            paths=self.paths,
            checker=checker,
            config_manager=kwargs.get("config_manager"),
            provider_manager=kwargs.get("provider_manager", _provider_manager()),
        )

    def test_apply_without_confirm_does_nothing(self):
        engine = self._engine(_report({"providers": HealthState.ERROR}))
        outcome = engine.apply(PROPOSAL_SWITCH_PROVIDER, confirm=False)
        self.assertFalse(outcome.applied)
        self.assertIn("Approval required", outcome.detail)

    def test_apply_unknown_proposal(self):
        engine = self._engine(_report({"providers": HealthState.ERROR}))
        outcome = engine.apply("nope", confirm=True)
        self.assertFalse(outcome.applied)
        self.assertIn("Unknown", outcome.detail)

    def test_apply_when_problem_gone_reports_no_longer_needed(self):
        engine = self._engine(_report({"providers": HealthState.OK}))
        outcome = engine.apply(PROPOSAL_SWITCH_PROVIDER, confirm=True)
        self.assertFalse(outcome.applied)
        self.assertIn("No longer needed", outcome.detail)

    def test_apply_switch_provider_verifies(self):
        engine = self._engine(
            _report({"providers": HealthState.ERROR}),
            _report({"providers": HealthState.OK}),
        )
        outcome = engine.apply(PROPOSAL_SWITCH_PROVIDER, confirm=True)
        self.assertTrue(outcome.applied)
        self.assertTrue(outcome.verified)
        self.assertIn("Ollama", outcome.detail)

    def test_apply_switch_provider_activation_failure(self):
        engine = self._engine(
            _report({"providers": HealthState.ERROR}),
            provider_manager=_provider_manager(activate_ok=False),
        )
        outcome = engine.apply(PROPOSAL_SWITCH_PROVIDER, confirm=True)
        self.assertFalse(outcome.applied)
        self.assertIn("ConnectionError", outcome.detail)

    def test_apply_switch_provider_unverified_when_check_still_bad(self):
        engine = self._engine(
            _report({"providers": HealthState.ERROR}),
            _report({"providers": HealthState.ERROR}),
        )
        outcome = engine.apply(PROPOSAL_SWITCH_PROVIDER, confirm=True)
        self.assertTrue(outcome.applied)
        self.assertFalse(outcome.verified)

    def test_apply_prune_backups_keeps_newest_two(self):
        backups = self.paths.backups
        backups.mkdir(parents=True, exist_ok=True)
        now = datetime.now().timestamp()
        for index in range(4):
            _write_zip(backups / f"backup-{index}.zip", now - index * 86400)
        engine = self._engine(_report({"disk": HealthState.ERROR}))
        outcome = engine.apply(PROPOSAL_PRUNE_BACKUPS, confirm=True)
        self.assertTrue(outcome.applied)
        self.assertTrue(outcome.verified)
        remaining = sorted(item.name for item in backups.glob("*.zip"))
        self.assertEqual(remaining, ["backup-0.zip", "backup-1.zip"])

    def test_apply_prune_events_deletes_only_old_files(self):
        events = self.paths.events
        events.mkdir(parents=True, exist_ok=True)
        old = datetime.now() - timedelta(days=EVENT_FILE_MAX_AGE_DAYS + 10)
        fresh = datetime.now() - timedelta(days=1)
        old_file = events / "2026-08-01.jsonl"
        old_file.write_text("{}\n", encoding="utf-8")
        os.utime(old_file, (old.timestamp(), old.timestamp()))
        fresh_file = events / "2026-11-19.jsonl"
        fresh_file.write_text("{}\n", encoding="utf-8")
        os.utime(fresh_file, (fresh.timestamp(), fresh.timestamp()))
        engine = self._engine(_report({"disk": HealthState.ERROR}))
        outcome = engine.apply(PROPOSAL_PRUNE_EVENTS, confirm=True)
        self.assertTrue(outcome.applied)
        self.assertTrue(outcome.verified)
        self.assertFalse(old_file.exists())
        self.assertTrue(fresh_file.exists())

    def test_apply_create_workspace(self):
        missing = Path(self._tmpdir.name) / "workspace"
        config = SimpleNamespace(
            get=lambda key, default=None: str(missing)
            if key == "workspace.default_path" else ""
        )
        engine = self._engine(
            _report({"workspace": HealthState.WARNING}),
            _report({"workspace": HealthState.OK}),
            config_manager=config,
        )
        outcome = engine.apply(PROPOSAL_CREATE_WORKSPACE, confirm=True)
        self.assertTrue(outcome.applied)
        self.assertTrue(outcome.verified)
        self.assertTrue(missing.is_dir())


class RemediateHandlerTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.addCleanup(self._env.stop)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.missing = Path(self._tmpdir.name) / "workspace"
        self.config = SimpleNamespace(
            get=lambda key, default=None: str(self.missing)
            if key == "workspace.default_path" else ""
        )
        self.checker = _FakeChecker(
            _report({"workspace": HealthState.WARNING}),
            _report({"workspace": HealthState.OK}),
        )
        self.handler = _make_handler(
            self._tmpdir.name,
            checker=self.checker,
            config_manager=self.config,
        )

    def test_plan_lists_proposals_and_applies_nothing(self):
        result = self.handler.remediate_plan()
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["proposals"]), 1)
        self.assertFalse(self.missing.exists())

    def test_plan_empty_when_healthy(self):
        self.handler.checker = _FakeChecker(_report({"disk": HealthState.OK}))
        result = self.handler.remediate_plan()
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["proposals"]), 0)

    def test_apply_without_confirm_fails_and_applies_nothing(self):
        result = self.handler.remediate_apply(PROPOSAL_CREATE_WORKSPACE)
        self.assertFalse(result.ok)
        self.assertFalse(self.missing.exists())

    def test_apply_with_confirm_creates_and_records(self):
        result = self.handler.remediate_apply(
            PROPOSAL_CREATE_WORKSPACE, confirm=True
        )
        self.assertTrue(result.ok)
        self.assertTrue(self.missing.is_dir())
        store = RecoveryHistoryStore(self.handler._paths.observability)
        records = store.query(kind="remediation").records
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].target, PROPOSAL_CREATE_WORKSPACE)
        self.assertEqual(records[0].status, "success")

    def test_apply_no_longer_needed_is_success(self):
        self.handler.checker = _FakeChecker(_report({"disk": HealthState.OK}))
        result = self.handler.remediate_apply(
            PROPOSAL_CREATE_WORKSPACE, confirm=True
        )
        self.assertTrue(result.ok)
        self.assertIn("No longer needed", result.message)

    def test_apply_all_with_confirm(self):
        result = self.handler.remediate_apply_all(confirm=True)
        self.assertTrue(result.ok)
        self.assertTrue(self.missing.is_dir())

    def test_apply_all_without_confirm_applies_nothing(self):
        result = self.handler.remediate_apply_all()
        self.assertFalse(result.ok)
        self.assertFalse(self.missing.exists())


class RemediateCliTests(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.addCleanup(self._env.stop)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.missing = Path(self._tmpdir.name) / "workspace"
        self.config = SimpleNamespace(
            get=lambda key, default=None: str(self.missing)
            if key == "workspace.default_path" else ""
        )
        self.checker = _FakeChecker(
            _report({"workspace": HealthState.WARNING}),
            _report({"workspace": HealthState.OK}),
        )
        self.handler = _make_handler(
            self._tmpdir.name,
            checker=self.checker,
            config_manager=self.config,
        )
        self.runtime = SimpleNamespace(observability_application=self.handler)
        self.output = []
        self.answers = []
        self.adapter = ObservabilityCliAdapter(
            self.runtime,
            output_provider=self.output.append,
            input_provider=lambda prompt: self.answers.pop(0),
        )

    def _run(self, raw):
        self.output.clear()
        return self.adapter.handle(raw)

    def test_remediate_lists_plan(self):
        result = self._run("remediate")
        self.assertTrue(result.ok)
        self.assertIn(PROPOSAL_CREATE_WORKSPACE, "".join(self.output))
        self.assertFalse(self.missing.exists())

    def test_remediate_json(self):
        result = self._run("remediate --json")
        self.assertTrue(result.ok)
        data = json.loads("".join(self.output))
        self.assertEqual(len(data["data"]["proposals"]), 1)

    def test_apply_yes_applies(self):
        self.answers = ["y"]
        result = self._run(f"remediate apply {PROPOSAL_CREATE_WORKSPACE}")
        self.assertTrue(result.ok)
        self.assertTrue(self.missing.is_dir())

    def test_apply_no_cancels(self):
        self.answers = ["n"]
        result = self._run(f"remediate apply {PROPOSAL_CREATE_WORKSPACE}")
        self.assertFalse(result.ok)
        self.assertFalse(self.missing.exists())

    def test_apply_all_yes_applies(self):
        self.answers = ["yes"]
        result = self._run("remediate apply --all")
        self.assertTrue(result.ok)
        self.assertTrue(self.missing.is_dir())

    def test_apply_all_no_cancels(self):
        self.answers = ["no"]
        result = self._run("remediate apply --all")
        self.assertFalse(result.ok)
        self.assertFalse(self.missing.exists())

    def test_remediate_rejects_unknown_option(self):
        self.assertFalse(self._run("remediate --nope").ok)

    def test_remediate_rejects_bad_apply(self):
        self.assertFalse(self._run("remediate apply").ok)
        self.assertFalse(self._run("remediate frobnicate").ok)

    def test_dispatch_remediate_plan_only(self):
        self.assertTrue(dispatch_observability(self.runtime, "remediate"))


class RemediateCoverageTests(unittest.TestCase):
    def test_help_and_completion_cover_remediate(self):
        from orion.core.router import CommandRouter
        from orion.ui.console import BASE_COMMANDS

        self.assertIn("remediate", BASE_COMMANDS)
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
        self.assertIn("remediate", text)


if __name__ == "__main__":
    unittest.main()
