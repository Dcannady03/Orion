"""Tests for Safeguard approval and policy enforcement."""
import tempfile
import unittest

from orion.actions import ActionHistory, ActionService, ActionStatus, PolicyDecision


class SafeguardTests(unittest.TestCase):
    def make_service(self, root):
        service = ActionService(ActionHistory(root))
        service.register_handler("safe", lambda action: "safe")
        service.register_handler("protected", lambda action: "approved")
        service.register_handler("blocked", lambda action: "never")
        service.approval.set_policy("protected", PolicyDecision.REQUIRE_APPROVAL, "confirmation required")
        service.approval.set_policy("blocked", PolicyDecision.DENY, "blocked by test policy")
        return service

    def test_policy_requires_approval_and_prevents_bypass(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.make_service(root)
            action = service.create("protected")
            self.assertEqual(action.status, ActionStatus.PENDING_APPROVAL)
            self.assertEqual(service.pending(), (action,))
            with self.assertRaises(PermissionError):
                service.execute(action)

    def test_approved_action_executes_once(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.make_service(root)
            action = service.create("protected")
            service.approve(action.id)
            result = service.execute(action)
            self.assertTrue(result.success)
            self.assertEqual(result.output, "approved")
            with self.assertRaises(RuntimeError):
                service.execute(action)

    def test_denied_action_cannot_be_approved_or_executed(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.make_service(root)
            action = service.create("protected")
            service.deny(action.id, "user declined")
            self.assertEqual(action.status, ActionStatus.DENIED)
            with self.assertRaises(RuntimeError):
                service.approve(action.id)
            with self.assertRaises(PermissionError):
                service.execute(action)

    def test_policy_can_deny_at_creation(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.make_service(root)
            with self.assertRaisesRegex(PermissionError, "blocked by test policy"):
                service.create("blocked")
            self.assertEqual(service.history.entries()[-1]["event"], "denied_by_policy")

    def test_safe_policy_runs_without_approval(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.make_service(root)
            result = service.run("safe")
            self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
