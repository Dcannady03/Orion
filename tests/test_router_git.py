"""Tests for Orion's router-level git commands and their approval gates."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orion.core.router import CommandRouter
from orion.services.workspace import WorkspaceCapabilities, WorkspaceManager


def _git_capabilities(root):
    return WorkspaceCapabilities(
        root=str(Path(root).resolve()),
        mode="git",
        is_git_repository=True,
        git_root=str(Path(root).resolve()),
        branch="main",
        commit="abc123",
        supports_git_diff=True,
        supports_git_commands=True,
    )


def _standard_capabilities(root):
    return WorkspaceCapabilities(
        root=str(Path(root).resolve()),
        mode="standard",
        is_git_repository=False,
        git_root="",
        branch="",
        commit="",
        supports_git_diff=False,
        supports_git_commands=False,
    )


def _router(manager, git_service):
    project_context = Mock()
    project_context.initialized = False
    return CommandRouter(SimpleNamespace(
        workspace_manager=manager,
        project_context=project_context,
        task_manager=Mock(),
        codex_bridge=Mock(),
        conversation=Mock(),
        knowledge_index=Mock(),
        action_history=Mock(),
        application_catalog=Mock(),
        companion_settings=Mock(),
        action_trust=Mock(),
        git_service=git_service,
    ))


class RouterGitTests(unittest.TestCase):
    def _git_router(self, root):
        manager = WorkspaceManager(root, capability_detector=_git_capabilities)
        service = Mock()
        return _router(manager, service), service

    def _standard_router(self, root):
        manager = WorkspaceManager(root, capability_detector=_standard_capabilities)
        service = Mock()
        return _router(manager, service), service

    def _run(self, func, answers=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with patch("builtins.input", side_effect=answers or []):
                func()
        return buf.getvalue()

    def test_git_pull_decline_cancels_without_calling_service(self):
        with tempfile.TemporaryDirectory() as root:
            router, service = self._git_router(root)
            output = self._run(router.git_pull, answers=["n"])
            service.pull.assert_not_called()
            self.assertIn("Sensitive action: fast-forward pull from origin", output)
            self.assertIn("Git pull cancelled.", output)

    def test_git_pull_approve_calls_service(self):
        with tempfile.TemporaryDirectory() as root:
            router, service = self._git_router(root)
            service.pull.return_value = "Already up to date."
            output = self._run(router.git_pull, answers=["y"])
            service.pull.assert_called_once_with()
            self.assertIn("Already up to date.", output)

    def test_git_pull_full_yes_approves(self):
        with tempfile.TemporaryDirectory() as root:
            router, service = self._git_router(root)
            self._run(router.git_pull, answers=["yes"])
            service.pull.assert_called_once_with()

    def test_git_push_decline_cancels_without_calling_service(self):
        with tempfile.TemporaryDirectory() as root:
            router, service = self._git_router(root)
            output = self._run(router.git_push, answers=["n"])
            service.push.assert_not_called()
            self.assertIn("Sensitive action: push the current branch to origin", output)
            self.assertIn("Git push cancelled.", output)

    def test_git_push_empty_answer_cancels(self):
        with tempfile.TemporaryDirectory() as root:
            router, service = self._git_router(root)
            output = self._run(router.git_push, answers=[""])
            service.push.assert_not_called()
            self.assertIn("Git push cancelled.", output)

    def test_git_push_approve_calls_service(self):
        with tempfile.TemporaryDirectory() as root:
            router, service = self._git_router(root)
            service.push.return_value = "Pushed."
            output = self._run(router.git_push, answers=["y"])
            service.push.assert_called_once_with()
            self.assertIn("Pushed.", output)

    def test_git_pull_in_standard_workspace_explains_without_calling_git(self):
        with tempfile.TemporaryDirectory() as root:
            router, service = self._standard_router(root)
            output = self._run(router.git_pull)
            service.pull.assert_not_called()
            self.assertIn("requires Git Workspace Mode", output)

    def test_git_push_in_standard_workspace_explains_without_calling_git(self):
        with tempfile.TemporaryDirectory() as root:
            router, service = self._standard_router(root)
            output = self._run(router.git_push)
            service.push.assert_not_called()
            self.assertIn("requires Git Workspace Mode", output)

    def test_git_status_in_git_workspace_renders_service_result(self):
        with tempfile.TemporaryDirectory() as root:
            router, service = self._git_router(root)
            service.status.return_value = SimpleNamespace(
                branch="main",
                upstream="origin/main",
                ahead=1,
                behind=0,
                dirty=True,
                changes=(" M README.md",),
            )
            output = self._run(router.git_status)
            service.status.assert_called_once_with()
            self.assertIn("Branch", output)
            self.assertIn("main", output)
            self.assertIn("Ahead", output)
            self.assertIn("Changes present", output)
            self.assertIn("M README.md", output)

    def test_git_log_and_diff_render_service_output(self):
        with tempfile.TemporaryDirectory() as root:
            router, service = self._git_router(root)
            service.log.return_value = "abc1234 Latest commit"
            service.diff.return_value = "diff --git a/x b/x"
            log_output = self._run(router.git_log)
            diff_output = self._run(router.git_diff)
            service.log.assert_called_once_with()
            service.diff.assert_called_once_with(staged=False)
            self.assertIn("abc1234 Latest commit", log_output)
            self.assertIn("diff --git a/x b/x", diff_output)


if __name__ == "__main__":
    unittest.main()
