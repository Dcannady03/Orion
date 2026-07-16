import subprocess
import tempfile
import unittest
from pathlib import Path

from orion.services.git_service import GitError, GitService
from orion.services.update import UpdateService


def run(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


class GitUpdateTests(unittest.TestCase):
    def make_repo(self, root):
        run(root, "init")
        run(root, "config", "user.email", "orion@example.test")
        run(root, "config", "user.name", "Orion Tests")
        Path(root, "README.md").write_text("Orion\n")
        run(root, "add", "README.md")
        run(root, "commit", "-m", "Initial")

    def test_status_detects_clean_and_dirty_tree(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_repo(root)
            service = GitService(root)
            self.assertFalse(service.status().dirty)
            Path(root, "README.md").write_text("Changed\n")
            self.assertTrue(service.status().dirty)

    def test_pull_refuses_dirty_working_tree(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_repo(root)
            Path(root, "README.md").write_text("Changed\n")
            with self.assertRaisesRegex(GitError, "uncommitted"):
                GitService(root).pull()

    def test_update_backup_preserves_config_and_runtime_without_recursion(self):
        with tempfile.TemporaryDirectory() as root:
            self.make_repo(root)
            Path(root, "config").mkdir()
            Path(root, "config", "default.yaml").write_text("orion: true\n")
            Path(root, ".orion").mkdir()
            Path(root, ".orion", "vault.json").write_text("secret")
            backup = UpdateService(GitService(root)).backup()
            self.assertTrue((backup / "config" / "default.yaml").exists())
            self.assertTrue((backup / ".orion" / "vault.json").exists())
            self.assertFalse((backup / ".orion" / "backups").exists())

    def test_non_repository_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(GitError, "not a Git repository"):
                GitService(root).status()


if __name__ == "__main__":
    unittest.main()
