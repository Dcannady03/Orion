"""Config and runtime-data backup export/import with integrity checks.

Exports are ZIP archives under ``user_root/backups/`` holding the user's
configuration and runtime data, plus a SHA-256 manifest. Secrets are never
exported: ``vault/`` and ``tokens/`` are always excluded, and the manifest
says so. Import verifies the manifest before touching anything, writes a
pre-import safety backup of the files it will replace, and only proceeds
with explicit confirmation.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from orion.core.paths import OrionPaths

BACKUP_FORMAT_VERSION = 1
BACKUP_FILE_PREFIX = "orion-backup"
PRE_IMPORT_PREFIX = "pre-import"
MANIFEST_NAME = "manifest.json"

# Relative paths (files or directories) included in an export, in order.
EXPORTED_PATHS = (
    "config.yaml",
    "profile.yaml",
    "events",
    "missions",
    "goals",
    "command-center",
    "team",
    "agents",
    "memory",
    "observability",
)

# Relative paths never exported, for security and hygiene.
EXCLUDED_PATHS = (
    "vault",
    "tokens",
    "logs",
    "cache",
    "backups",
)

_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_MAX_NAME_LENGTH = 64


def _orion_version() -> str:
    try:
        from orion.version import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001 - version is best-effort in a manifest
        return "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(name: str) -> str | None:
    """Return a safe relative archive path, or None when it is unsafe."""
    candidate = name.replace("\\", "/").strip()
    if not candidate or candidate.startswith("/") or candidate.startswith("~"):
        return None
    parts = candidate.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


@dataclass(frozen=True)
class BackupExport:
    """The result of creating a backup archive."""

    path: Path
    name: str
    file_count: int
    total_bytes: int
    exported_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "name": self.name,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "exported_at": self.exported_at,
        }


@dataclass(frozen=True)
class BackupInfo:
    """One backup archive found in the backups directory."""

    path: Path
    name: str
    size_bytes: int
    modified_at: str
    format_version: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "name": self.name,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "format_version": self.format_version,
        }


@dataclass(frozen=True)
class BackupVerification:
    """The outcome of verifying an archive against its manifest."""

    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    manifest: Mapping[str, Any] = field(default_factory=dict)
    file_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "manifest": dict(self.manifest),
            "file_count": self.file_count,
        }


@dataclass(frozen=True)
class BackupImportPlan:
    """What an import would change, computed without changing anything."""

    path: Path
    files: tuple[str, ...] = ()
    overwrite: tuple[str, ...] = ()
    new: tuple[str, ...] = ()
    total_bytes: int = 0
    safety_backup_name: str = ""
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "files": list(self.files),
            "overwrite": list(self.overwrite),
            "new": list(self.new),
            "total_bytes": self.total_bytes,
            "safety_backup_name": self.safety_backup_name,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class BackupImportResult:
    """The outcome of a completed import."""

    path: Path
    restored: int
    safety_backup: Path | None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "restored": self.restored,
            "safety_backup": str(self.safety_backup) if self.safety_backup else "",
        }


class BackupService:
    """Export, verify, list, and import Orion data backups."""

    def __init__(
        self,
        paths: OrionPaths,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(paths, OrionPaths):
            raise TypeError("Backups require OrionPaths.")
        self._paths = paths
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def backups_dir(self) -> Path:
        return self._paths.backups

    def export(self, name: str | None = None) -> BackupExport:
        """Write a backup archive of config and runtime data."""
        label = (name or "").strip()
        if label:
            if len(label) > _MAX_NAME_LENGTH or not _NAME_PATTERN.fullmatch(label):
                raise ValueError(
                    "Backup name must be 1-64 characters of letters, digits, "
                    "dots, underscores, or dashes."
                )
        stamp = self._clock().strftime("%Y%m%d-%H%M%S")
        filename = (
            f"{BACKUP_FILE_PREFIX}-{label}-{stamp}.zip"
            if label
            else f"{BACKUP_FILE_PREFIX}-{stamp}.zip"
        )
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        target = self.backups_dir / filename
        if target.exists():
            raise ValueError(f"Backup already exists: {filename}")

        files: list[dict[str, Any]] = []
        total_bytes = 0
        exported_at = self._clock().isoformat()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for relative in self._exported_files():
                source = self._paths.user_root / relative
                archive.write(source, relative)
                size = source.stat().st_size
                files.append(
                    {
                        "path": relative,
                        "size_bytes": size,
                        "sha256": _sha256(source),
                    }
                )
                total_bytes += size
            manifest = {
                "format_version": BACKUP_FORMAT_VERSION,
                "orion_version": _orion_version(),
                "exported_at": exported_at,
                "exported_from": str(self._paths.user_root),
                "excluded": list(EXCLUDED_PATHS),
                "files": files,
            }
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, indent=2, sort_keys=True),
            )
        return BackupExport(
            path=target,
            name=filename,
            file_count=len(files),
            total_bytes=total_bytes,
            exported_at=exported_at,
        )

    def list_backups(self) -> list[BackupInfo]:
        """List backup archives in the backups directory, newest first."""
        if not self.backups_dir.is_dir():
            return []
        found: list[BackupInfo] = []
        for path in sorted(
            self.backups_dir.glob("*.zip"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            version = None
            try:
                with zipfile.ZipFile(path) as archive:
                    if MANIFEST_NAME in archive.namelist():
                        manifest = json.loads(
                            archive.read(MANIFEST_NAME).decode("utf-8")
                        )
                        if isinstance(manifest, dict):
                            raw = manifest.get("format_version")
                            version = int(raw) if isinstance(raw, int) else None
            except (OSError, ValueError, zipfile.BadZipFile):
                version = None
            modified = datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).isoformat()
            found.append(
                BackupInfo(
                    path=path,
                    name=path.name,
                    size_bytes=path.stat().st_size,
                    modified_at=modified,
                    format_version=version,
                )
            )
        return found

    def verify(self, path: str | Path) -> BackupVerification:
        """Verify an archive against its manifest without changing anything."""
        target = Path(path)
        if not target.is_file():
            return BackupVerification(
                False, (f"Backup not found: {target}",)
            )
        try:
            archive = zipfile.ZipFile(target)
        except zipfile.BadZipFile:
            return BackupVerification(False, (f"Not a valid ZIP archive: {target}",))
        except OSError as exc:
            return BackupVerification(False, (f"Could not read backup: {exc}",))
        with archive:
            names = archive.namelist()
            if MANIFEST_NAME not in names:
                return BackupVerification(
                    False, ("Backup has no manifest.json.",)
                )
            try:
                manifest = json.loads(
                    archive.read(MANIFEST_NAME).decode("utf-8")
                )
            except (ValueError, UnicodeError):
                return BackupVerification(
                    False, ("Backup manifest is not valid JSON.",)
                )
            if not isinstance(manifest, dict):
                return BackupVerification(
                    False, ("Backup manifest is not a JSON object.",)
                )
            errors: list[str] = []
            warnings: list[str] = []
            version = manifest.get("format_version")
            if version != BACKUP_FORMAT_VERSION:
                errors.append(
                    f"Unsupported backup format version: {version!r} "
                    f"(expected {BACKUP_FORMAT_VERSION})."
                )
            entries = manifest.get("files")
            if not isinstance(entries, list):
                errors.append("Backup manifest has no file list.")
                return BackupVerification(False, tuple(errors), tuple(warnings), manifest)
            seen: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    errors.append("Backup manifest has a malformed file entry.")
                    continue
                relative = _safe_relative(str(entry.get("path", "")))
                if relative is None:
                    errors.append(
                        f"Backup manifest has an unsafe path: {entry.get('path')!r}."
                    )
                    continue
                seen.add(relative)
                if relative not in names:
                    errors.append(f"Backup is missing file: {relative}.")
                    continue
                try:
                    data = archive.read(relative)
                except (KeyError, OSError, zipfile.BadZipFile) as exc:
                    errors.append(f"Could not read {relative}: {exc}.")
                    continue
                digest = hashlib.sha256(data).hexdigest()
                if digest != entry.get("sha256"):
                    errors.append(f"Checksum mismatch: {relative}.")
                if len(data) != entry.get("size_bytes"):
                    errors.append(f"Size mismatch: {relative}.")
            extras = [
                name
                for name in names
                if name != MANIFEST_NAME and name not in seen
            ]
            if extras:
                warnings.append(
                    f"{len(extras)} extra file(s) in the archive are not in "
                    "the manifest and will be ignored on import."
                )
            exported_from = manifest.get("exported_from")
            if exported_from and str(exported_from) != str(self._paths.user_root):
                warnings.append(
                    "Backup was exported from a different user-data directory "
                    f"({exported_from})."
                )
            return BackupVerification(
                not errors,
                tuple(errors),
                tuple(warnings),
                manifest,
                len(entries),
            )

    def plan_import(self, path: str | Path) -> BackupImportPlan:
        """Describe what an import would change, without changing anything."""
        verification = self.verify(path)
        if not verification.valid:
            raise ValueError(
                "Backup failed verification: " + "; ".join(verification.errors)
            )
        manifest = verification.manifest
        entries = manifest.get("files", [])
        files: list[str] = []
        overwrite: list[str] = []
        new: list[str] = []
        total_bytes = 0
        for entry in entries:
            relative = str(entry.get("path", ""))
            files.append(relative)
            total_bytes += int(entry.get("size_bytes", 0))
            if (self._paths.user_root / relative).exists():
                overwrite.append(relative)
            else:
                new.append(relative)
        stamp = self._clock().strftime("%Y%m%d-%H%M%S")
        return BackupImportPlan(
            path=Path(path),
            files=tuple(files),
            overwrite=tuple(overwrite),
            new=tuple(new),
            total_bytes=total_bytes,
            safety_backup_name=f"{PRE_IMPORT_PREFIX}-{stamp}.zip",
            warnings=verification.warnings,
        )

    def import_backup(
        self, path: str | Path, *, confirm: bool = False
    ) -> BackupImportResult:
        """Restore a verified backup, after writing a safety backup.

        Requires confirm=True; without explicit confirmation nothing changes.
        """
        if not confirm:
            raise ValueError("Backup import requires explicit confirmation.")
        plan = self.plan_import(path)
        safety_backup: Path | None = None
        if plan.overwrite:
            safety_backup = self._write_safety_backup(plan)
        target = Path(path)
        restored = 0
        with zipfile.ZipFile(target) as archive:
            for relative in plan.files:
                destination = self._paths.user_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                data = archive.read(relative)
                destination.write_bytes(data)
                restored += 1
        return BackupImportResult(
            path=target, restored=restored, safety_backup=safety_backup
        )

    def _write_safety_backup(self, plan: BackupImportPlan) -> Path:
        """Archive the current versions of files the import will replace."""
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        target = self.backups_dir / plan.safety_backup_name
        files: list[dict[str, Any]] = []
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for relative in plan.overwrite:
                source = self._paths.user_root / relative
                archive.write(source, relative)
                files.append(
                    {
                        "path": relative,
                        "size_bytes": source.stat().st_size,
                        "sha256": _sha256(source),
                    }
                )
            manifest = {
                "format_version": BACKUP_FORMAT_VERSION,
                "orion_version": _orion_version(),
                "exported_at": self._clock().isoformat(),
                "exported_from": str(self._paths.user_root),
                "excluded": list(EXCLUDED_PATHS),
                "files": files,
                "pre_import_for": str(plan.path),
            }
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, indent=2, sort_keys=True),
            )
        return target

    def _exported_files(self) -> list[str]:
        """Collect the relative paths to export, skipping excluded roots."""
        collected: list[str] = []
        for entry in EXPORTED_PATHS:
            source = self._paths.user_root / entry
            if source.is_file():
                collected.append(entry)
            elif source.is_dir():
                for path in sorted(source.rglob("*")):
                    if path.is_file():
                        collected.append(path.relative_to(self._paths.user_root).as_posix())
        return collected
