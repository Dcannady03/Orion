import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from orion.services.git_service import GitError, GitService
from orion.services.update import UpdateError, UpdateService


def run(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


class FakeTransport:
    def __init__(self, archive: Path, latest: str = "b" * 40):
        self.archive = archive
        self.latest = latest

    def json(self, url, *, timeout=15.0):
        return {
            "sha": self.latest,
            "commit": {"committer": {"date": "2026-07-16T16:00:00Z"}},
        }

    def download(self, url, destination, *, timeout=60.0):
        data = self.archive.read_bytes()
        destination.write_bytes(data)
        return hashlib.sha256(data).hexdigest()


class GitUpdateTests(unittest.TestCase):
    def make_repo(self, root):
        run(root, "init")
        run(root, "config", "user.email", "orion@example.test")
        run(root, "config", "user.name", "Orion Tests")
        Path(root, "README.md").write_text("Orion\n")
        run(root, "add", "README.md")
        run(root, "commit", "-m", "Initial")

    def make_install(self, root: Path, marker="old"):
        (root / "orion").mkdir(parents=True)
        (root / "orion" / "main.py").write_text(marker)
        (root / "config").mkdir()
        (root / "config" / "default.yaml").write_text("orion: true\n")
        (root / "README.md").write_text(marker)

    def make_archive(self, root: Path, marker="new") -> Path:
        payload = root / "source" / "Dcannady03-Orion-test"
        self.make_install(payload, marker)
        archive = root / "orion.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            for path in payload.rglob("*"):
                if path.is_file():
                    bundle.write(path, path.relative_to(root / "source"))
        return archive

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

    def test_package_check_does_not_require_git(self):
        with tempfile.TemporaryDirectory() as parent:
            parent = Path(parent)
            install = parent / "Orion"
            user = parent / "user"
            self.make_install(install)
            archive = self.make_archive(parent)
            service = UpdateService(install, user, transport=FakeTransport(archive))
            check = service.check()
            self.assertTrue(check.available)
            self.assertEqual(check.latest, "b" * 40)
            self.assertEqual(check.channel, "main")
            self.assertEqual(
                check.package_url,
                f"https://codeload.github.com/Dcannady03/Orion/zip/{'b' * 40}",
            )

    def test_package_update_preserves_user_data_and_git_metadata(self):
        with tempfile.TemporaryDirectory() as parent:
            parent = Path(parent)
            install = parent / "Orion"
            user = parent / "user"
            self.make_install(install)
            (install / ".git").mkdir()
            (install / ".git" / "keep").write_text("git")
            user.mkdir()
            (user / "config.yaml").write_text("discord: configured\n")
            archive = self.make_archive(parent)
            service = UpdateService(install, user, transport=FakeTransport(archive))

            result = service.apply(service.check())

            self.assertEqual((install / "README.md").read_text(), "new")
            self.assertEqual((install / ".git" / "keep").read_text(), "git")
            self.assertEqual((user / "config.yaml").read_text(), "discord: configured\n")
            self.assertTrue((result.backup / "application" / "README.md").exists())
            state = json.loads((user / "update-state.json").read_text())
            self.assertEqual(state["revision"], "b" * 40)

    def test_rollback_restores_previous_application(self):
        with tempfile.TemporaryDirectory() as parent:
            parent = Path(parent)
            install = parent / "Orion"
            user = parent / "user"
            self.make_install(install)
            archive = self.make_archive(parent)
            service = UpdateService(install, user, transport=FakeTransport(archive))
            service.apply(service.check())
            self.assertEqual((install / "README.md").read_text(), "new")

            result = service.rollback()

            self.assertEqual((install / "README.md").read_text(), "old")
            self.assertTrue(result.backup.exists())

    def test_invalid_package_is_rejected_before_application_replacement(self):
        with tempfile.TemporaryDirectory() as parent:
            parent = Path(parent)
            install = parent / "Orion"
            user = parent / "user"
            self.make_install(install)
            archive = parent / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("not-orion/readme.txt", "bad")
            service = UpdateService(install, user, transport=FakeTransport(archive))
            with self.assertRaisesRegex(UpdateError, "valid Orion"):
                service.apply(service.check())
            self.assertEqual((install / "README.md").read_text(), "old")

    def test_non_repository_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(GitError, "not a Git repository"):
                GitService(root).status()


if __name__ == "__main__":
    unittest.main()
