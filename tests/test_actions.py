"""Tests for Ignition's Action Core."""
import tempfile
import unittest

from orion.actions import ActionHistory, ActionService, ActionStatus


class ActionCoreTests(unittest.TestCase):
    def make_service(self, root):
        service = ActionService(ActionHistory(root))
        service.register_handler("echo", lambda action: action.parameters["message"])
        return service

    def test_action_ids_are_unique_and_handler_executes(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.make_service(root)
            first = service.create("echo", {"message": "Ignition"})
            second = service.create("echo", {"message": "Online"})
            self.assertNotEqual(first.id, second.id)
            result = service.execute(first)
            self.assertTrue(result.success)
            self.assertEqual(result.output, "Ignition")
            self.assertEqual(first.status, ActionStatus.SUCCEEDED)

    def test_unknown_and_duplicate_handlers_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.make_service(root)
            with self.assertRaises(KeyError):
                service.create("missing")
            with self.assertRaises(KeyError):
                service.register_handler("echo", lambda action: None)

    def test_handler_failure_is_recorded(self):
        with tempfile.TemporaryDirectory() as root:
            service = ActionService(ActionHistory(root))
            service.register_handler("fail", lambda action: (_ for _ in ()).throw(RuntimeError("boom")))
            action = service.create("fail")
            result = service.execute(action)
            self.assertFalse(result.success)
            self.assertEqual(result.error, "boom")
            self.assertEqual(action.status, ActionStatus.FAILED)
            self.assertEqual(service.history.entries()[-1]["event"], "completed")

    def test_action_history_is_isolated_by_workspace(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            alpha = self.make_service(first)
            alpha.run("echo", {"message": "alpha"})
            beta_history = ActionHistory(second)
            self.assertEqual(beta_history.entries(), [])

    def test_approval_required_action_cannot_execute_early(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.make_service(root)
            action = service.create("echo", {"message": "blocked"}, requires_approval=True)
            with self.assertRaises(PermissionError):
                service.execute(action)


if __name__ == "__main__":
    unittest.main()
