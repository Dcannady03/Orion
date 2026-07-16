"""Package-based self-update workflow for Orion installations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile

from orion.core.paths import OrionPaths


class UpdateError(RuntimeError):
    """Raised when an Orion package update cannot be completed safely."""


@dataclass(frozen=True)
class UpdateCheck:
    current: str
    available: bool
    latest: str
    channel: str
    package_url: str
    published_at: str = ""


@dataclass(frozen=True)
class UpdateResult:
    previous: str
    current: str
    backup: Path
    package_sha256: str


class UrlTransport:
    """Small injectable HTTP transport used by the updater and its tests."""

    def json(self, url: str, *, timeout: float = 15.0) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Orion-Updater",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise UpdateError(f"Could not check for Orion updates: {exc}") from exc
        if not isinstance(value, dict):
            raise UpdateError("Update service returned an invalid response.")
        return value

    def download(self, url: str, destination: Path, *, timeout: float = 60.0) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": "Orion-Updater"})
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UpdateError(f"Could not download the Orion update: {exc}") from exc
        return digest.hexdigest()


class UpdateService:
    """Update Orion from a pinned GitHub source package, without using Git.

    Git remains available through :class:`GitService` for development workspaces.
    Stable Orion installations use this service, which downloads a package for a
    specific remote commit, backs up the application, preserves external user
    data, replaces only application files, and records update state under
    ``~/.orion``.
    """

    PRESERVE_NAMES = {".git", ".venv", "venv", "env"}
    BACKUP_EXCLUDES = PRESERVE_NAMES | {"__pycache__", ".pytest_cache"}

    def __init__(
        self,
        install_root: str | Path,
        runtime_root: str | Path | None = None,
        *,
        repository: str = "Dcannady03/Orion",
        channel: str = "main",
        transport: UrlTransport | None = None,
    ) -> None:
        self.paths = OrionPaths(install_root=install_root, user_root=runtime_root)
        self.paths.ensure()
        self.install_root = self.paths.install_root
        self.repository = repository.strip("/")
        self.channel = channel
        self.transport = transport or UrlTransport()
        self.state_path = self.paths.user_root / "update-state.json"

    @property
    def commit_api_url(self) -> str:
        return f"https://api.github.com/repos/{self.repository}/commits/{self.channel}"

    def check(self, *, fetch: bool = True) -> UpdateCheck:
        current = self.current_revision()
        if not fetch:
            return UpdateCheck(current, False, current, self.channel, "")
        payload = self.transport.json(self.commit_api_url)
        latest = str(payload.get("sha", "")).strip()
        if not latest:
            raise UpdateError("GitHub did not provide a usable Orion revision.")
        package_url = self._archive_url(latest)
        commit = payload.get("commit") if isinstance(payload.get("commit"), dict) else {}
        committer = commit.get("committer") if isinstance(commit.get("committer"), dict) else {}
        published = str(committer.get("date", ""))
        return UpdateCheck(
            current=current,
            available=not self._same_revision(current, latest),
            latest=latest,
            channel=self.channel,
            package_url=package_url,
            published_at=published,
        )

    def _archive_url(self, revision: str) -> str:
        return f"https://codeload.github.com/{self.repository}/zip/{revision}"

    def current_revision(self) -> str:
        state = self._read_state()
        revision = str(state.get("revision", "")).strip()
        if revision:
            return revision
        git_head = self.install_root / ".git" / "HEAD"
        if git_head.is_file():
            try:
                text = git_head.read_text(encoding="utf-8").strip()
                if text.startswith("ref: "):
                    ref = self.install_root / ".git" / text[5:]
                    if ref.is_file():
                        return ref.read_text(encoding="utf-8").strip()
                elif text:
                    return text
            except OSError:
                pass
        return "unknown"

    def backup_application(self, revision: str | None = None) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        label = self._short(revision or self.current_revision())
        backup = self.paths.backups / f"application-{stamp}-{label}"
        backup.mkdir(parents=True, exist_ok=False)
        app = backup / "application"
        self._copy_tree(self.install_root, app, exclude=self.BACKUP_EXCLUDES)
        metadata = {
            "revision": revision or self.current_revision(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "install_root": str(self.install_root),
        }
        (backup / "backup.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return backup

    def apply(self, check: UpdateCheck | None = None) -> UpdateResult:
        selected = check or self.check(fetch=True)
        if not selected.available:
            raise UpdateError("Orion is already up to date.")

        with tempfile.TemporaryDirectory(prefix="orion-update-") as temp:
            temp_root = Path(temp)
            archive = temp_root / "orion-update.zip"
            digest = self.transport.download(selected.package_url, archive)
            payload = self._extract_payload(archive, temp_root / "payload")
            self._validate_payload(payload)
            backup = self.backup_application(selected.current)
            try:
                self._replace_application(payload)
                self._write_state(selected.latest, digest, backup)
            except Exception as exc:
                try:
                    self._restore_backup(backup)
                except Exception as rollback_exc:
                    raise UpdateError(
                        f"Update failed and rollback also failed: {exc}; rollback: {rollback_exc}"
                    ) from exc
                raise UpdateError(f"Update failed; Orion restored the previous application: {exc}") from exc

        return UpdateResult(selected.current, selected.latest, backup, digest)

    def rollback(self, backup: str | Path | None = None) -> UpdateResult:
        selected = Path(backup) if backup else self.latest_backup()
        if selected is None or not selected.is_dir():
            raise UpdateError("No Orion application backup is available for rollback.")
        metadata = self._backup_metadata(selected)
        previous = self.current_revision()
        target = str(metadata.get("revision", "unknown"))
        safety_backup = self.backup_application(previous)
        try:
            self._restore_backup(selected)
            self._write_state(target, "rollback", safety_backup)
        except Exception as exc:
            self._restore_backup(safety_backup)
            raise UpdateError(f"Rollback failed; the current application was restored: {exc}") from exc
        return UpdateResult(previous, target, safety_backup, "rollback")

    def latest_backup(self) -> Path | None:
        candidates = sorted(
            (path for path in self.paths.backups.glob("application-*") if (path / "application").is_dir()),
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _extract_payload(self, archive: Path, destination: Path) -> Path:
        try:
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(destination)
        except (zipfile.BadZipFile, OSError) as exc:
            raise UpdateError(f"Downloaded Orion package is invalid: {exc}") from exc
        children = [item for item in destination.iterdir() if item.name != "__MACOSX"]
        if len(children) == 1 and children[0].is_dir():
            return children[0]
        return destination

    @staticmethod
    def _validate_payload(payload: Path) -> None:
        required = (payload / "orion", payload / "orion" / "main.py", payload / "config" / "default.yaml")
        if not all(path.exists() for path in required):
            raise UpdateError("Downloaded package does not contain a valid Orion application.")

    def _replace_application(self, payload: Path) -> None:
        for item in list(self.install_root.iterdir()):
            if item.name in self.PRESERVE_NAMES:
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in payload.iterdir():
            if item.name in self.PRESERVE_NAMES:
                continue
            destination = self.install_root / item.name
            if item.is_dir():
                shutil.copytree(item, destination)
            else:
                shutil.copy2(item, destination)

    def _restore_backup(self, backup: Path) -> None:
        source = backup / "application"
        if not source.is_dir():
            raise UpdateError(f"Invalid Orion backup: {backup}")
        self._replace_application(source)

    @classmethod
    def _copy_tree(cls, source: Path, destination: Path, *, exclude: set[str]) -> None:
        def ignore(_path: str, names: list[str]) -> set[str]:
            return {name for name in names if name in exclude}
        shutil.copytree(source, destination, ignore=ignore)

    def _write_state(self, revision: str, digest: str, backup: Path) -> None:
        value = {
            "revision": revision,
            "channel": self.channel,
            "repository": self.repository,
            "package_sha256": digest,
            "last_backup": str(backup),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state_path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _backup_metadata(backup: Path) -> dict[str, Any]:
        try:
            value = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _same_revision(current: str, latest: str) -> bool:
        if current == "unknown":
            return False
        return current == latest or latest.startswith(current) or current.startswith(latest)

    @staticmethod
    def _short(value: str) -> str:
        clean = "".join(character for character in value if character.isalnum())
        return (clean or "unknown")[:8]
