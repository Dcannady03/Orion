"""Mission Coordinator Phase 3: append-only per-Mission attempt history."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from orion.application.missions import (
    MissionAdvanceAudit,
    MissionAdvanceRequest,
    MissionAttemptHistoryEntry,
    MissionCoordinationRepository,
    MissionDispatchUncertainError,
    MissionResolutionHistoryEntry,
    parse_attempt_history_entry,
)
from tests.test_mission_coordinator_phase2 import (
    FINAL_REVIEW_ID,
    RUN_ID,
)
from tests.test_mission_coordinator_phase3 import (
    MISSION_ID,
    MissionCoordinatorPhase3Fixture,
)
from tests.test_mission_coordinator_phase3_resolve import (
    MissionCoordinatorResolveFixture,
    dead_pid,
)
from tests.test_mission_engine import TEAM_TASK_ID


def _audit_dict(**overrides):
    value = {
        "schema_version": 1,
        "mission_id": MISSION_ID,
        "attempt_id": "mission-attempt-" + "1" * 32,
        "advance_token": "a" * 64,
        "capability_id": "team.final_review",
        "subject_id": RUN_ID,
        "actor": "operator",
        "state": "reserved",
        "started_at": "2026-07-18T13:10:00Z",
        "finished_at": "",
        "result_status": "",
        "downstream_reference": "",
        "last_advance_event_id": "",
        "safe_message": "",
    }
    value.update(overrides)
    return value


def _attempt_entry_dict(**overrides):
    value = {
        "schema_version": 1,
        "kind": "attempt",
        "recorded_at": "2026-07-18T13:10:00Z",
        "audit": _audit_dict(),
    }
    value.update(overrides)
    return value


def _resolution_entry_dict(**overrides):
    value = {
        "schema_version": 1,
        "kind": "resolution",
        "mission_id": MISSION_ID,
        "actor": "operator",
        "resolved_at": "2026-07-18T13:11:00Z",
        "attempt_id": "mission-attempt-" + "1" * 32,
        "capability_id": "team.final_review",
        "finding": "did_not_run",
        "audit_outcome": "cleared",
        "downstream_reference": "",
        "lock_cleared": False,
        "recovery_record_id": "rec-0123456789ab",
    }
    value.update(overrides)
    return value


class AttemptHistoryModelTests(unittest.TestCase):
    def test_attempt_entry_is_immutable_json_safe_and_schema_versioned(self):
        entry = MissionAttemptHistoryEntry.from_value(_attempt_entry_dict())
        self.assertEqual(entry.kind, "attempt")
        self.assertEqual(entry.audit.capability_id, "team.final_review")
        self.assertEqual(
            json.loads(json.dumps(entry.to_dict())),
            _attempt_entry_dict(),
        )
        with self.assertRaisesRegex(ValueError, "schema"):
            MissionAttemptHistoryEntry.from_value(
                _attempt_entry_dict(schema_version=2)
            )
        with self.assertRaisesRegex(ValueError, "kind"):
            MissionAttemptHistoryEntry.from_value(
                _attempt_entry_dict(kind="resolution")
            )
        with self.assertRaisesRegex(ValueError, "fields are invalid"):
            MissionAttemptHistoryEntry.from_value(
                {key: value for key, value in _attempt_entry_dict().items()
                 if key != "recorded_at"}
            )

    def test_resolution_entry_round_trips_and_validates_its_shape(self):
        entry = MissionResolutionHistoryEntry.from_value(
            _resolution_entry_dict()
        )
        self.assertEqual(entry.kind, "resolution")
        self.assertEqual(entry.finding, "did_not_run")
        self.assertEqual(
            json.loads(json.dumps(entry.to_dict())),
            _resolution_entry_dict(),
        )
        with self.assertRaisesRegex(ValueError, "finding"):
            MissionResolutionHistoryEntry.from_value(
                _resolution_entry_dict(finding="maybe")
            )
        with self.assertRaisesRegex(ValueError, "attempt ID"):
            MissionResolutionHistoryEntry.from_value(
                _resolution_entry_dict(attempt_id="")
            )
        with self.assertRaisesRegex(ValueError, "audit outcome"):
            MissionResolutionHistoryEntry.from_value(
                _resolution_entry_dict(audit_outcome="none")
            )
        with self.assertRaisesRegex(ValueError, "lock-only"):
            MissionResolutionHistoryEntry.from_value(
                _resolution_entry_dict(finding="", audit_outcome="cleared")
            )

    def test_lock_only_resolution_entry_is_valid_without_an_attempt(self):
        entry = MissionResolutionHistoryEntry.from_value(
            _resolution_entry_dict(
                attempt_id="",
                capability_id="",
                finding="",
                audit_outcome="none",
                lock_cleared=True,
                recovery_record_id="",
            )
        )
        self.assertEqual(entry.finding, "")
        self.assertTrue(entry.lock_cleared)

    def test_parse_dispatches_on_kind(self):
        attempt = parse_attempt_history_entry(_attempt_entry_dict())
        self.assertIsInstance(attempt, MissionAttemptHistoryEntry)
        resolution = parse_attempt_history_entry(_resolution_entry_dict())
        self.assertIsInstance(resolution, MissionResolutionHistoryEntry)
        with self.assertRaisesRegex(ValueError, "kind"):
            parse_attempt_history_entry(_attempt_entry_dict(kind="other"))
        with self.assertRaises(TypeError):
            parse_attempt_history_entry(["not", "a", "mapping"])


class AttemptHistoryRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="orion-history-")
        self.addCleanup(self.temp.cleanup)
        self.repository = MissionCoordinationRepository(
            Path(self.temp.name) / "coordination"
        )
        self.audit = MissionAdvanceAudit.from_value(_audit_dict())

    def _completed_audit(self):
        return MissionAdvanceAudit.from_value(_audit_dict(
            state="succeeded",
            finished_at="2026-07-18T13:11:00Z",
            result_status="succeeded",
            downstream_reference=FINAL_REVIEW_ID,
        ))

    def test_read_returns_empty_tuple_when_no_log_exists(self):
        self.assertEqual(
            self.repository.read_attempt_history(MISSION_ID), ()
        )

    def test_append_and_read_return_entries_oldest_first(self):
        self.repository.append_attempt(
            self.audit, recorded_at="2026-07-18T13:10:00Z"
        )
        self.repository.append_attempt(
            self._completed_audit(), recorded_at="2026-07-18T13:11:00Z"
        )
        entries = self.repository.read_attempt_history(MISSION_ID)
        self.assertEqual(len(entries), 2)
        self.assertIsInstance(entries[0], MissionAttemptHistoryEntry)
        self.assertEqual(entries[0].audit.state, "reserved")
        self.assertEqual(entries[1].audit.state, "succeeded")
        self.assertEqual(
            entries[1].audit.downstream_reference, FINAL_REVIEW_ID
        )
        path = Path(self.temp.name) / "coordination" / f"{MISSION_ID}.attempts.jsonl"
        self.assertTrue(path.is_file())
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

    def test_resolution_entries_share_the_log_in_order(self):
        self.repository.append_attempt(self.audit)
        resolution = MissionResolutionHistoryEntry.from_value(
            _resolution_entry_dict()
        )
        self.repository.append_resolution(resolution)
        entries = self.repository.read_attempt_history(MISSION_ID)
        self.assertEqual(len(entries), 2)
        self.assertIsInstance(entries[1], MissionResolutionHistoryEntry)
        self.assertEqual(entries[1].finding, "did_not_run")
        self.assertEqual(entries[1].audit_outcome, "cleared")

    def test_malformed_log_raises_on_read(self):
        path = (
            Path(self.temp.name) / "coordination" / f"{MISSION_ID}.attempts.jsonl"
        )
        path.parent.mkdir(parents=True)
        path.write_text('{"kind": "attempt",\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "malformed"):
            self.repository.read_attempt_history(MISSION_ID)

    def test_symlinked_log_is_refused(self):
        root = Path(self.temp.name) / "coordination"
        root.mkdir(parents=True)
        target = Path(self.temp.name) / "elsewhere.jsonl"
        target.write_text("", encoding="utf-8")
        link = root / f"{MISSION_ID}.attempts.jsonl"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlinks are not available on this platform")
        with self.assertRaises(PermissionError):
            self.repository.append_attempt(self.audit)
        with self.assertRaises(PermissionError):
            self.repository.read_attempt_history(MISSION_ID)

    def test_log_is_bounded_by_the_audit_size_limit(self):
        bounded = MissionCoordinationRepository(
            Path(self.temp.name) / "bounded",
            max_record_bytes=4_096,
        )
        written = 0
        with self.assertRaisesRegex(ValueError, "size limit"):
            while True:
                bounded.append_attempt(self.audit)
                written += 1
        self.assertGreater(written, 0)
        self.assertEqual(len(bounded.read_attempt_history(MISSION_ID)), written)


class MissionCoordinatorHistoryFixture(
    MissionCoordinatorPhase3Fixture,
    MissionCoordinatorResolveFixture,
):
    """Approved mission with final-review dispatch and a recovery recorder."""


class AttemptHistoryCoordinatorTests(MissionCoordinatorHistoryFixture):
    def _history(self):
        return self.audit_repository.read_attempt_history(
            self.mission.mission_id
        )

    def test_advance_appends_reserved_and_terminal_entries(self):
        self.implement()
        entries = self._history()
        self.assertEqual(len(entries), 2)
        self.assertEqual(
            [entry.audit.state for entry in entries],
            ["reserved", "succeeded"],
        )
        self.assertEqual(
            [entry.audit.capability_id for entry in entries],
            ["team.implement", "team.implement"],
        )
        self.assertEqual(
            entries[0].audit.attempt_id, entries[1].audit.attempt_id
        )
        # The blocking logic still reads only the single latest record.
        latest = self.audit_repository.get(self.mission.mission_id)
        self.assertEqual(latest.state, "succeeded")
        self.assertEqual(latest.attempt_id, entries[1].audit.attempt_id)

    def test_full_lifecycle_history_ends_with_the_final_decision(self):
        self.to_final_review()
        self.final_review(decision="accepted")
        entries = self._history()
        self.assertEqual(len(entries), 8)
        self.assertEqual(
            [entry.audit.capability_id for entry in entries],
            [
                "team.implement", "team.implement",
                "team.validate", "team.validate",
                "team.documentation_review", "team.documentation_review",
                "team.final_review", "team.final_review",
            ],
        )
        self.assertEqual(
            [entry.audit.state for entry in entries],
            ["reserved", "succeeded"] * 4,
        )
        self.assertEqual(
            entries[-1].audit.downstream_reference, FINAL_REVIEW_ID
        )
        mission = self.service.reconcile(self.mission.mission_id).mission
        self.assertEqual(mission.status.value, "completed")
        self.assertEqual(mission.progress, 100)

    def test_uncertain_dispatch_appends_reserved_and_uncertain_entries(self):
        self.team_application.raise_on_implement = True
        preview = self.coordinator.preview(self.mission.mission_id)
        self.assertFalse(preview.operation.blocked)
        with self.assertRaises(MissionDispatchUncertainError):
            self.coordinator.advance(MissionAdvanceRequest(
                self.mission.mission_id,
                preview.advance_token,
                confirmed=True,
                actor="operator",
            ))
        entries = self._history()
        self.assertEqual(len(entries), 2)
        self.assertEqual(
            [entry.audit.state for entry in entries],
            ["reserved", "uncertain"],
        )

    def test_resolve_ran_appends_succeeded_attempt_and_resolution(self):
        self.implement()
        prior = self.audit_repository.get(self.mission.mission_id)
        self._write_stuck_audit(
            "team.implement",
            TEAM_TASK_ID,
            attempt_id=prior.attempt_id,
            token=prior.advance_token,
        )
        result = self._resolve("ran")
        self.assertEqual(result.audit_outcome, "succeeded")
        self.assertTrue(result.recovery_record_id)
        entries = self._history()
        self.assertEqual(len(entries), 4)
        self.assertEqual(entries[2].audit.state, "succeeded")
        self.assertEqual(
            entries[2].audit.downstream_reference,
            result.downstream_reference,
        )
        resolution = entries[3]
        self.assertIsInstance(resolution, MissionResolutionHistoryEntry)
        self.assertEqual(resolution.finding, "ran")
        self.assertEqual(resolution.audit_outcome, "succeeded")
        self.assertEqual(resolution.attempt_id, prior.attempt_id)
        self.assertEqual(
            resolution.recovery_record_id, result.recovery_record_id
        )

    def test_resolve_did_not_run_appends_resolution_and_reattempt(self):
        self.to_final_review()
        self._write_stuck_audit("team.final_review", RUN_ID)
        result = self._resolve("did_not_run")
        self.assertEqual(result.audit_outcome, "cleared")
        entries = self._history()
        # Six lifecycle entries plus the resolution; the cleared attempt was
        # written directly to the audit, so it has no history entry of its own.
        self.assertEqual(len(entries), 7)
        resolution = entries[-1]
        self.assertIsInstance(resolution, MissionResolutionHistoryEntry)
        self.assertEqual(resolution.finding, "did_not_run")
        self.assertEqual(resolution.audit_outcome, "cleared")
        self.assertEqual(resolution.capability_id, "team.final_review")
        self.final_review(decision="accepted")
        entries = self._history()
        self.assertEqual(len(entries), 9)
        self.assertEqual(entries[-1].audit.state, "succeeded")
        self.assertEqual(entries[-1].audit.capability_id, "team.final_review")

    def test_lock_only_resolution_appends_a_lock_only_entry(self):
        self._write_lock(dead_pid())
        result = self._resolve("")
        self.assertTrue(result.lock_cleared)
        self.assertEqual(result.audit_outcome, "none")
        entries = self._history()
        self.assertEqual(len(entries), 1)
        resolution = entries[0]
        self.assertIsInstance(resolution, MissionResolutionHistoryEntry)
        self.assertEqual(resolution.finding, "")
        self.assertEqual(resolution.attempt_id, "")
        self.assertEqual(resolution.audit_outcome, "none")
        self.assertTrue(resolution.lock_cleared)

    def test_history_append_failure_warns_but_does_not_break_advance(self):
        bounded = MissionCoordinationRepository(
            self.audit_repository.root,
            forbidden_root=self.install_root,
            max_record_bytes=4_096,
        )
        self.audit_repository = bounded
        self.coordinator = self._coordinator()
        filler = MissionAdvanceAudit.from_value(_audit_dict(
            mission_id=self.mission.mission_id,
        ))
        with self.assertRaisesRegex(ValueError, "size limit"):
            while True:
                bounded.append_attempt(filler)
        _preview, result = self.advance()
        self.assertEqual(result.new_status, "awaiting_validation")
        self.assertTrue(result.operation_dispatched)
        self.assertIn(
            "The attempt could not be appended to the Mission attempt history.",
            result.warnings,
        )
        latest = bounded.get(self.mission.mission_id)
        self.assertEqual(latest.state, "succeeded")


if __name__ == "__main__":
    unittest.main()
