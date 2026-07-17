import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orion.core.router import CommandRouter
from orion.services.project_context import ProjectContext
from orion.services.task_manager import (
    APPROVAL_APPROVED,
    APPROVAL_CANCELLED,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_READY,
    TaskManager,
)


class TaskManagerTests(unittest.TestCase):
    def manager(self, root, *, task_ids=None):
        ProjectContext(root).initialize(name="Task Test")
        ids = iter(task_ids or ["task-test-001"])
        event_number = iter(range(1, 100))
        artifact_number = iter(range(1, 100))
        return TaskManager(
            root,
            now=lambda: datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
            id_factory=lambda: next(ids),
            event_id_factory=lambda: f"event-test-{next(event_number):03d}",
            artifact_id_factory=lambda: f"artifact-test-{next(artifact_number):03d}",
        )

    def test_create_persists_strict_task_and_append_only_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            task = manager.create(
                "Add a project task manager",
                assigned_role="architect",
                assigned_agent="security-reviewer",
            )

            self.assertEqual(task.task_id, "task-test-001")
            self.assertEqual(task.status, "proposed")
            self.assertEqual(task.approval, "pending")
            self.assertEqual(manager.get(task.task_id), task)
            self.assertEqual(json.loads(manager.tasks_path.read_text(encoding="utf-8")), [task.to_dict()])
            events = manager.events(task.task_id)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_type, "created")
            self.assertEqual(events[0].status, "proposed")
            self.assertFalse(manager.tasks_path.with_suffix(".json.tmp").exists())

    def test_approve_is_explicit_and_cannot_be_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            task = manager.create("Review an implementation plan")

            manager._event_id_factory = lambda: "event-test-001"
            with self.assertRaises(ValueError):
                manager.approve(task.task_id)
            self.assertEqual(manager.get(task.task_id).status, "proposed")
            manager._event_id_factory = lambda: "event-test-002"

            approved = manager.approve(task.task_id)

            self.assertEqual(approved.status, TASK_STATUS_READY)
            self.assertEqual(approved.approval, APPROVAL_APPROVED)
            self.assertEqual(
                [event.event_type for event in manager.events(task.task_id)],
                ["created", "approved"],
            )
            with self.assertRaises(ValueError):
                manager.approve(task.task_id)

    def test_cancel_is_terminal_and_project_metrics_exclude_it_from_open_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            task = manager.create("Cancel this safely")
            cancelled = manager.cancel(task.task_id)

            self.assertEqual(cancelled.status, TASK_STATUS_CANCELLED)
            self.assertEqual(cancelled.approval, APPROVAL_CANCELLED)
            metrics = ProjectContext(tmp).metrics()
            self.assertEqual(metrics["tasks_open"], 0)
            self.assertEqual(metrics["tasks_cancelled"], 1)
            with self.assertRaises(ValueError):
                manager.cancel(task.task_id)
            with self.assertRaises(ValueError):
                manager.approve(task.task_id)

    def test_dependencies_must_exist_and_cannot_duplicate_or_reference_self(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp, task_ids=[
                "task-first-001",
                "task-second-001",
                "task-missing-001",
                "task-self-001",
                "task-duplicate-001",
            ])
            first = manager.create("First task")
            second = manager.create("Second task", dependencies=[first.task_id])
            self.assertEqual(second.dependencies, (first.task_id,))
            with self.assertRaises(ValueError):
                manager.create("Missing dependency", dependencies=["task-unknown-001"])
            with self.assertRaises(ValueError):
                manager.create("Self dependency", dependencies=["task-self-001"])
            with self.assertRaises(ValueError):
                manager.create(
                    "Duplicate dependency",
                    dependencies=[first.task_id, first.task_id],
                )
            value = json.loads(manager.tasks_path.read_text(encoding="utf-8"))
            value[0]["dependencies"] = [second.task_id]
            manager.tasks_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ValueError):
                manager.all()

    def test_linked_ai_team_plan_is_a_unique_persisted_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            task = manager.create("Add image generation")
            linked = manager.link_team_plan(
                task.task_id,
                "team-test-001",
                summary="Bounded image generation plan",
            )

            self.assertEqual(len(linked.artifacts), 1)
            self.assertEqual(linked.artifacts[0].kind, "ai_team_plan")
            self.assertEqual(linked.artifacts[0].reference, "team-test-001")
            self.assertEqual(manager.get(task.task_id).artifacts, linked.artifacts)
            self.assertEqual(manager.events(task.task_id)[-1].event_type, "team_plan_linked")
            with self.assertRaises(ValueError):
                manager.link_team_plan(
                    task.task_id,
                    "team-test-001",
                    summary="Duplicate plan",
                )

    def test_corrupt_task_documents_are_rejected_without_being_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.create("Valid task")
            valid = json.loads(manager.tasks_path.read_text(encoding="utf-8"))
            mutations = {
                "missing identity": lambda value: value[0].pop("task_id"),
                "unknown field": lambda value: value[0].update(unexpected=True),
                "unknown status": lambda value: value[0].update(status="implementing"),
                "naive timestamp": lambda value: value[0].update(updated_at="2026-07-18T12:00:00"),
            }
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    value = deepcopy(valid)
                    mutate(value)
                    serialized = json.dumps(value)
                    manager.tasks_path.write_text(serialized, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        manager.all()
                    self.assertEqual(manager.tasks_path.read_text(encoding="utf-8"), serialized)

    def test_malformed_or_duplicate_events_are_rejected_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.create("Valid event")
            valid_line = manager.events_path.read_text(encoding="utf-8").strip()
            orphan_plan_event = {
                **json.loads(valid_line),
                "event_id": "event-test-999",
                "event_type": "team_plan_linked",
                "previous_status": "proposed",
            }
            documents = {
                "invalid JSON": "{broken\n",
                "unknown field": json.dumps({**json.loads(valid_line), "unexpected": True}) + "\n",
                "duplicate ID": valid_line + "\n" + valid_line + "\n",
                "invalid transition": json.dumps({
                    **json.loads(valid_line),
                    "previous_status": "ready",
                }) + "\n",
                "unknown task": json.dumps({
                    **json.loads(valid_line),
                    "task_id": "task-unknown-001",
                }) + "\n",
                "missing creation event": json.dumps(orphan_plan_event) + "\n",
            }
            for label, document in documents.items():
                with self.subTest(label=label):
                    manager.events_path.write_text(document, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        manager.events()
                    self.assertEqual(manager.events_path.read_text(encoding="utf-8"), document)

    def test_tasks_are_isolated_when_the_active_workspace_changes(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            ProjectContext(first).initialize(name="First")
            ProjectContext(second).initialize(name="Second")
            task_ids = iter(["task-first-001", "task-second-001"])
            manager = TaskManager(
                first,
                now=lambda: datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
                id_factory=lambda: next(task_ids),
            )
            first_task = manager.create("First workspace task")
            manager.bind(second)
            self.assertEqual(manager.all(), ())
            second_task = manager.create("Second workspace task")
            manager.bind(first)
            self.assertEqual(manager.get(first_task.task_id).goal, "First workspace task")
            with self.assertRaises(FileNotFoundError):
                manager.get(second_task.task_id)

    def test_router_runs_bounded_task_lifecycle_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            router = CommandRouter(SimpleNamespace(task_manager=manager))
            with patch("builtins.print") as output:
                router.handle('task create "Add task commands"')
                router.handle("task list")
                router.handle("task show task-test-001")
                router.handle("task approve task-test-001")
                router.handle("task events task-test-001")
                router.handle("task cancel task-test-001")

            task = manager.get("task-test-001")
            self.assertEqual(task.status, TASK_STATUS_CANCELLED)
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("Task created: task-test-001", rendered)
            self.assertIn("Approval does not start planning", rendered)
            self.assertIn("approved", rendered)
            self.assertIn("No implementation was performed", rendered)

    def test_router_links_only_reviewed_ai_team_plans(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.manager(tmp)
            manager.create("Use a reviewed plan")
            team_task = SimpleNamespace(
                task_id="team-test-001",
                goal="Reviewed implementation plan",
                status="awaiting_approval",
            )
            team = Mock()
            team.task.return_value = team_task
            router = CommandRouter(SimpleNamespace(task_manager=manager, team=team))
            with patch("builtins.print") as output:
                router.handle("task link-plan task-test-001 team-test-001")

            linked = manager.get("task-test-001")
            self.assertEqual(linked.artifacts[0].reference, "team-test-001")
            team.task.assert_called_once_with("team-test-001")
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("remains an artifact", rendered)


if __name__ == "__main__":
    unittest.main()
