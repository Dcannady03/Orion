"""Crash-tolerance tests for the stores the server writes (Phase A item 6).

Verifies the hardening: Team task saves use a unique temp file (no
collision under concurrent saves of the same task), and the Command
Center activity log tolerates a torn final line left by a crash
mid-flush while still rejecting genuine corruption.
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from orion.command_center import (
    ActivityEvent,
    ActivitySeverity,
    ActivitySourceType,
    FileCommandCenterRepository,
)
from orion.services.team import TeamTask, TeamTaskStore

NOW = "2026-11-20T12:00:00+00:00"


def _event(event_id: str, message: str, timestamp: str = NOW) -> ActivityEvent:
    return ActivityEvent.create(
        event_id=event_id,
        event_type="job.created",
        source_type=ActivitySourceType.JOB,
        source_id="job-1",
        message=message,
        timestamp=timestamp,
        severity=ActivitySeverity.INFO,
        job_id="job-1",
    )


def _task(task_id: str, goal: str) -> TeamTask:
    return TeamTask(
        task_id=task_id,
        goal=goal,
        status="planning",
        created_at=NOW,
        updated_at=NOW,
    )


class TeamTaskStoreCrashToleranceTest(unittest.TestCase):
    def test_concurrent_saves_of_same_task_all_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TeamTaskStore(Path(tmp) / "tasks")
            errors: list[BaseException] = []

            def save_many(marker: str) -> None:
                try:
                    for index in range(20):
                        store.save(_task("task-1", f"{marker}-{index}"))
                except BaseException as exc:  # noqa: BLE001 - collected
                    errors.append(exc)

            threads = [
                threading.Thread(target=save_many, args=(f"writer-{n}",))
                for n in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            self.assertEqual(errors, [])
            # The final record is one complete write, never torn.
            loaded = store.load("task-1")
            self.assertTrue(loaded.goal.startswith("writer-"))
            self.assertEqual(loaded.task_id, "task-1")

    def test_no_temp_files_linger_after_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tasks"
            store = TeamTaskStore(root)
            store.save(_task("task-1", "goal"))
            leftovers = [
                path for path in root.iterdir() if path.suffix == ".tmp"
            ]
            self.assertEqual(leftovers, [])

    def test_stray_temp_file_does_not_affect_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tasks"
            store = TeamTaskStore(root)
            store.save(_task("task-1", "goal"))
            # A temp file left behind by a crashed save is inert: loads
            # read only the final record path.
            stray = root / ".task-1.json.deadbeef.tmp"
            stray.write_text('{"partial": ', encoding="utf-8")
            self.assertEqual(store.load("task-1").goal, "goal")


class ActivityLogCrashToleranceTest(unittest.TestCase):
    def test_torn_final_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = FileCommandCenterRepository(Path(tmp) / "cc")
            repo.append_activity(_event("event-1", "first"))
            # Simulate a crash mid-flush: a partial line, no newline.
            with repo.activity_path.open("a", encoding="utf-8") as handle:
                handle.write('{"schema_version": 1, "event_id": "eve')
            events = repo.list_activity(limit=10)
            self.assertEqual([item.event_id for item in events], ["event-1"])

    def test_append_after_torn_line_succeeds_and_reads_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = FileCommandCenterRepository(Path(tmp) / "cc")
            repo.append_activity(_event("event-1", "first"))
            with repo.activity_path.open("a", encoding="utf-8") as handle:
                handle.write('{"schema_version": 1, "event_id": "eve')
            repo.append_activity(
                _event("event-2", "second", "2026-11-20T12:01:00+00:00")
            )
            events = repo.list_activity(limit=10)
            self.assertEqual(
                [item.event_id for item in events], ["event-1", "event-2"]
            )

    def test_malformed_middle_line_still_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = FileCommandCenterRepository(Path(tmp) / "cc")
            repo.append_activity(_event("event-1", "first"))
            # Write the malformed line and a valid second event directly:
            # append_activity itself reads first and would raise.
            second = _event("event-2", "second", "2026-11-20T12:01:00+00:00")
            line = json.dumps(
                second.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            with repo.activity_path.open("a", encoding="utf-8") as handle:
                handle.write('{"not": "an event"}\n')
                handle.write(line + "\n")
            with self.assertRaises(ValueError):
                repo.list_activity(limit=10)

    def test_complete_malformed_final_line_still_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = FileCommandCenterRepository(Path(tmp) / "cc")
            repo.append_activity(_event("event-1", "first"))
            # Newline-terminated: the whole line reached disk, so this is
            # corruption, not a crash artifact.
            with repo.activity_path.open("a", encoding="utf-8") as handle:
                handle.write('{"not": "an event"}\n')
            with self.assertRaises(ValueError):
                repo.list_activity(limit=10)


if __name__ == "__main__":
    unittest.main()
