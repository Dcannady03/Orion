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

    def test_update_backup_preserves_external_user_data_without_recursion(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent, "Orion")
            user_root = Path(parent, "user-data")
            root.mkdir()
            self.make_repo(root)
            user_root.mkdir()
            (user_root / "config.yaml").write_text("orion: true\n")
            (user_root / "vault").mkdir()
            (user_root / "vault" / "vault.yaml").write_text("secret")
            (user_root / "backups").mkdir()
            (user_root / "backups" / "old.txt").write_text("skip")

            backup = UpdateService(GitService(root), runtime_root=user_root).backup()

            self.assertTrue((backup / "user-data" / "config.yaml").exists())
            self.assertTrue((backup / "user-data" / "vault" / "vault.yaml").exists())
            self.assertFalse((backup / "user-data" / "backups").exists())

    def test_update_backup_does_not_dirty_repository(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent, "Orion")
            user_root = Path(parent, "user-data")
            root.mkdir()
            user_root.mkdir()
            self.make_repo(root)
            (user_root / "config.yaml").write_text("orion: true\n")

            service = GitService(root)
            backup = UpdateService(service, runtime_root=user_root).backup()

            self.assertFalse(service.status().dirty)
            with self.assertRaises(ValueError):
                backup.relative_to(root)
            self.assertTrue((backup / "user-data" / "config.yaml").exists())

    def test_non_repository_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(GitError, "not a Git repository"):
                GitService(root).status()


if __name__ == "__main__":
    unittest.main()
