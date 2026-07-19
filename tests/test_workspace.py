"""Tests for Orion's Workspace Manager."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orion.core.router import CommandRouter
from orion.services.workspace import WorkspaceCapabilities, WorkspaceManager


class WorkspaceManagerTests(unittest.TestCase):
    @staticmethod
    def router(manager):
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
            git_service=Mock(),
        ))

    def test_lists_directories_before_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "zeta.txt").write_text("hello", encoding="utf-8")
            (root / "alpha").mkdir()

            manager = WorkspaceManager(root)
            entries = manager.list_entries()

            self.assertEqual([entry.name for entry in entries], ["alpha", "zeta.txt"])
            self.assertTrue(entries[0].is_directory)
            self.assertEqual(entries[1].size_bytes, 5)

    def test_prevents_path_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = WorkspaceManager(temp_dir)

            with self.assertRaises(PermissionError):
                manager.resolve("../outside.txt")

    def test_switches_workspace(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            manager = WorkspaceManager(first)
            selected = manager.set_workspace(second)

            self.assertEqual(selected, Path(second).resolve())

    def test_standard_workspace_capabilities_do_not_require_git(self):
        with tempfile.TemporaryDirectory() as root:
            capabilities = WorkspaceCapabilities.detect(root, which=lambda _name: None)
            self.assertEqual(capabilities.mode, "standard")
            self.assertFalse(capabilities.is_git_repository)
            self.assertFalse(capabilities.supports_git_commands)

    def test_git_capabilities_capture_repository_metadata_for_subdirectory(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = Path(temp) / "repository"
            workspace = repository / "nested" / "active"
            workspace.mkdir(parents=True)

            def runner(command, **_kwargs):
                args = tuple(command[-2:])
                if args == ("rev-parse", "--show-toplevel"):
                    output = str(repository)
                elif args == ("branch", "--show-current"):
                    output = "dev-test"
                elif args == ("rev-parse", "HEAD"):
                    output = "a" * 40
                else:
                    return subprocess.CompletedProcess(command, 1, "", "bad")
                return subprocess.CompletedProcess(command, 0, output + "\n", "")

            capabilities = WorkspaceCapabilities.detect(
                workspace,
                which=lambda _name: "git",
                runner=runner,
            )
            self.assertEqual(capabilities.mode, "git")
            self.assertEqual(capabilities.root, str(workspace.resolve()))
            self.assertEqual(capabilities.git_root, str(repository.resolve()))
            self.assertEqual(capabilities.branch, "dev-test")
            self.assertEqual(capabilities.commit, "a" * 40)

    def test_git_marker_preserves_repository_mode_when_git_inspection_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = Path(temp) / "repository"
            workspace = repository / "nested"
            workspace.mkdir(parents=True)
            (repository / ".git").mkdir()

            capabilities = WorkspaceCapabilities.detect(
                workspace,
                which=lambda _name: "git",
                runner=lambda command, **_kwargs: subprocess.CompletedProcess(
                    command, 128, "", "inspection refused"
                ),
            )

            self.assertEqual(capabilities.mode, "git")
            self.assertEqual(capabilities.git_root, str(repository.resolve()))
            self.assertFalse(capabilities.supports_git_diff)
            self.assertFalse(capabilities.supports_git_commands)

    def test_missing_workspace_can_be_created_after_confirmation_without_git(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = WorkspaceManager(root, capability_detector=lambda path: WorkspaceCapabilities.detect(path, which=lambda _name: None))
            requested = root / "new" / "workspace"
            router = self.router(manager)

            with patch("builtins.input", return_value="y"), patch("builtins.print"):
                router.set_workspace(str(requested))

            self.assertEqual(manager.root, requested.resolve())
            self.assertTrue(requested.is_dir())
            self.assertFalse((requested / ".git").exists())
            self.assertEqual(manager.capabilities.mode, "standard")

    def test_declining_workspace_creation_preserves_active_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = WorkspaceManager(root)
            requested = root / "declined"
            router = self.router(manager)

            with patch("builtins.input", return_value="n"), patch("builtins.print"):
                router.set_workspace(str(requested))

            self.assertEqual(manager.root, root.resolve())
            self.assertFalse(requested.exists())

    def test_protected_system_workspace_creation_is_rejected(self):
        protected = (
            Path(os.environ.get("SYSTEMROOT", "C:\\Windows"))
            if os.name == "nt"
            else Path("/etc")
        )
        requested = protected / "orion-must-not-create-workspace-test"
        manager = WorkspaceManager(Path.cwd())
        with self.assertRaises(PermissionError):
            manager.create_workspace(requested)

    def test_git_only_command_explains_standard_mode_without_calling_git(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = WorkspaceManager(
                temp,
                capability_detector=lambda path: WorkspaceCapabilities.detect(path, which=lambda _name: None),
            )
            router = self.router(manager)
            with patch("builtins.print") as output:
                router.git_status()
            router.orion.git_service.status.assert_not_called()
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn("requires Git Workspace Mode", rendered)
            self.assertIn("Team planning and execution remain available", rendered)


if __name__ == "__main__":
    unittest.main()
