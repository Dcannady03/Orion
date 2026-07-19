"""Bounded, Git-independent workspace baselines, diffs, and safe rollback."""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import os
import re
import stat
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from orion.services.workspace import (
    DEFAULT_WORKSPACE_IGNORED_DIRECTORIES,
    WorkspaceCapabilities,
)


SNAPSHOT_SCHEMA_VERSION = 1
CHANGE_KINDS = frozenset({"created", "modified", "deleted"})
SENSITIVE_NAMES = frozenset({
    ".env", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml",
    "vault.yaml", "vault.yml", "id_rsa", "id_ed25519",
})
SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".jks"})
SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~+/=-]{8,}|sk-[a-z0-9_-]{8,}|AIza[a-z0-9_-]{12,}|"
    r"gh[pousr]_[a-z0-9]{12,}|((api[_-]?key|secret|token|password|authorization)\s*[:=]\s*)\S+)"
)


class WorkspaceSnapshotError(RuntimeError):
    """Raised before execution when Orion cannot capture a safe bounded baseline."""


class WorkspaceRollbackError(RuntimeError):
    """Raised when rollback would overwrite work created after the Codex run."""


@dataclass(frozen=True)
class SnapshotLimits:
    max_files: int = 10_000
    max_file_bytes: int = 25_000_000
    max_total_bytes: int = 250_000_000
    max_diff_bytes: int = 2_000_000

    @classmethod
    def from_config(cls, config) -> "SnapshotLimits":
        values = {
            "max_files": config.get("codex_bridge.snapshot_max_files", cls.max_files),
            "max_file_bytes": config.get(
                "codex_bridge.snapshot_max_file_bytes", cls.max_file_bytes
            ),
            "max_total_bytes": config.get(
                "codex_bridge.snapshot_max_total_bytes", cls.max_total_bytes
            ),
            "max_diff_bytes": config.get("codex_bridge.diff_max_bytes", cls.max_diff_bytes),
        }
        bounds = {
            "max_files": (1, 100_000),
            "max_file_bytes": (1, 1_000_000_000),
            "max_total_bytes": (1, 5_000_000_000),
            "max_diff_bytes": (1, 25_000_000),
        }
        for name, value in values.items():
            minimum, maximum = bounds[name]
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(
                    f"Codex Bridge {name.replace('_', ' ')} must be between "
                    f"{minimum:,} and {maximum:,}."
                )
        return cls(**values)


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    size: int
    sha256: str
    binary: bool
    mode: int
    blob: str

    @classmethod
    def from_value(cls, value: Any) -> "SnapshotFile":
        fields = {"path", "size", "sha256", "binary", "mode", "blob"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Workspace snapshot file has an invalid schema.")
        path = _relative_path(value["path"])
        size = value["size"]
        mode = value["mode"]
        digest = str(value["sha256"])
        blob = str(value["blob"])
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("Workspace snapshot file size is invalid.")
        if isinstance(mode, bool) or not isinstance(mode, int) or mode < 0:
            raise ValueError("Workspace snapshot file mode is invalid.")
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or blob != f"{digest}.zlib":
            raise ValueError("Workspace snapshot file digest is invalid.")
        if not isinstance(value["binary"], bool):
            raise ValueError("Workspace snapshot binary flag is invalid.")
        return cls(path, size, digest, value["binary"], mode, blob)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "sha256": self.sha256,
            "binary": self.binary,
            "mode": self.mode,
            "blob": self.blob,
        }


@dataclass(frozen=True)
class WorkspaceBaseline:
    schema_version: int
    workspace: WorkspaceCapabilities
    created_at: str
    files: tuple[SnapshotFile, ...]
    total_bytes: int
    ignored_directories: tuple[str, ...]
    ignored_patterns: tuple[tuple[str, bool], ...]

    @classmethod
    def from_value(cls, value: Any) -> "WorkspaceBaseline":
        fields = {
            "schema_version", "workspace", "created_at", "files", "total_bytes",
            "ignored_directories", "ignored_patterns",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Workspace baseline has an invalid schema.")
        if value["schema_version"] != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("Workspace baseline schema is not supported.")
        if (
            not isinstance(value["files"], list)
            or not isinstance(value["ignored_directories"], list)
            or not isinstance(value["ignored_patterns"], list)
        ):
            raise ValueError("Workspace baseline collections are invalid.")
        files = tuple(SnapshotFile.from_value(item) for item in value["files"])
        paths = [item.path.casefold() for item in files]
        if len(paths) != len(set(paths)):
            raise ValueError("Workspace baseline contains duplicate paths.")
        total = value["total_bytes"]
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise ValueError("Workspace baseline total size is invalid.")
        ignored = tuple(str(item) for item in value["ignored_directories"])
        patterns: list[tuple[str, bool]] = []
        for item in value["ignored_patterns"]:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not isinstance(item[1], bool)
            ):
                raise ValueError("Workspace baseline ignore pattern is invalid.")
            patterns.append((item[0], item[1]))
        return cls(
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            workspace=WorkspaceCapabilities.from_value(value["workspace"]),
            created_at=str(value["created_at"]),
            files=files,
            total_bytes=total,
            ignored_directories=ignored,
            ignored_patterns=tuple(patterns),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace": self.workspace.to_dict(),
            "created_at": self.created_at,
            "files": [item.to_dict() for item in self.files],
            "total_bytes": self.total_bytes,
            "ignored_directories": list(self.ignored_directories),
            "ignored_patterns": [[pattern, negated] for pattern, negated in self.ignored_patterns],
        }


@dataclass(frozen=True)
class WorkspaceChange:
    path: str
    kind: str
    binary: bool
    before_size: int
    after_size: int
    before_sha256: str
    after_sha256: str

    @classmethod
    def from_value(cls, value: Any) -> "WorkspaceChange":
        fields = {
            "path", "kind", "binary", "before_size", "after_size",
            "before_sha256", "after_sha256",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Workspace change has an invalid schema.")
        kind = str(value["kind"]).lower()
        if kind not in CHANGE_KINDS or not isinstance(value["binary"], bool):
            raise ValueError("Workspace change metadata is invalid.")
        sizes = (value["before_size"], value["after_size"])
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in sizes):
            raise ValueError("Workspace change size is invalid.")
        digests = (str(value["before_sha256"]), str(value["after_sha256"]))
        if any(item and not re.fullmatch(r"[a-f0-9]{64}", item) for item in digests):
            raise ValueError("Workspace change digest is invalid.")
        return cls(
            path=_relative_path(value["path"]),
            kind=kind,
            binary=value["binary"],
            before_size=sizes[0],
            after_size=sizes[1],
            before_sha256=digests[0],
            after_sha256=digests[1],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "binary": self.binary,
            "before_size": self.before_size,
            "after_size": self.after_size,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
        }


@dataclass(frozen=True)
class WorkspaceChangeSet:
    schema_version: int
    workspace_root: str
    changes: tuple[WorkspaceChange, ...]
    diff_truncated: bool

    @classmethod
    def from_value(cls, value: Any) -> "WorkspaceChangeSet":
        fields = {"schema_version", "workspace_root", "changes", "diff_truncated"}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("Workspace change set has an invalid schema.")
        if value["schema_version"] != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("Workspace change schema is not supported.")
        root = Path(str(value["workspace_root"]))
        if not root.is_absolute() or not isinstance(value["changes"], list):
            raise ValueError("Workspace change set root or changes are invalid.")
        if not isinstance(value["diff_truncated"], bool):
            raise ValueError("Workspace diff truncation flag is invalid.")
        changes = tuple(WorkspaceChange.from_value(item) for item in value["changes"])
        paths = [item.path.casefold() for item in changes]
        if len(paths) != len(set(paths)):
            raise ValueError("Workspace change set contains duplicate paths.")
        return cls(
            SNAPSHOT_SCHEMA_VERSION,
            str(root.resolve()),
            changes,
            value["diff_truncated"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace_root": self.workspace_root,
            "changes": [item.to_dict() for item in self.changes],
            "diff_truncated": self.diff_truncated,
        }

    def by_kind(self, kind: str) -> tuple[WorkspaceChange, ...]:
        return tuple(item for item in self.changes if item.kind == kind)


class WorkspaceSnapshotService:
    """Capture and compare a bounded workspace without depending on Git."""

    def __init__(
        self,
        *,
        ignored_directories: Iterable[str] = DEFAULT_WORKSPACE_IGNORED_DIRECTORIES,
    ) -> None:
        self.ignored_directories = frozenset(str(item).casefold() for item in ignored_directories)

    def capture(
        self,
        workspace: WorkspaceCapabilities,
        blob_root: str | Path,
        limits: SnapshotLimits,
        *,
        created_at: str,
    ) -> WorkspaceBaseline:
        root = Path(workspace.root).resolve()
        blobs = Path(blob_root).resolve()
        blobs.mkdir(parents=True, exist_ok=True)
        _owner_only(blobs)
        ignore_patterns = self._gitignore_patterns(root)
        files: list[SnapshotFile] = []
        total = 0
        for path in self._paths(root, ignore_patterns):
            if len(files) >= limits.max_files:
                raise WorkspaceSnapshotError(
                    f"Workspace baseline exceeds the {limits.max_files:,}-file safety limit."
                )
            try:
                if path.is_symlink():
                    raise WorkspaceSnapshotError(
                        f"Workspace baseline cannot safely capture symbolic link: {path.relative_to(root)}"
                    )
                size = path.stat().st_size
                if size > limits.max_file_bytes:
                    raise WorkspaceSnapshotError(
                        f"Workspace file exceeds the {limits.max_file_bytes:,}-byte snapshot limit: "
                        f"{path.relative_to(root)}"
                    )
                total += size
                if total > limits.max_total_bytes:
                    raise WorkspaceSnapshotError(
                        f"Workspace baseline exceeds the {limits.max_total_bytes:,}-byte total limit."
                    )
                data = path.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                blob_name = f"{digest}.zlib"
                blob_path = blobs / blob_name
                if not blob_path.exists():
                    with blob_path.open("xb") as handle:
                        handle.write(zlib.compress(data, level=9))
                    _owner_only(blob_path)
                relative = path.relative_to(root).as_posix()
                files.append(SnapshotFile(
                    path=relative,
                    size=size,
                    sha256=digest,
                    binary=_is_binary(data) or _is_sensitive(relative),
                    mode=stat.S_IMODE(path.stat().st_mode),
                    blob=blob_name,
                ))
            except WorkspaceSnapshotError:
                raise
            except OSError as exc:
                raise WorkspaceSnapshotError(
                    f"Workspace baseline could not safely read: {path.relative_to(root)}"
                ) from exc
        return WorkspaceBaseline(
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            workspace=workspace,
            created_at=created_at,
            files=tuple(files),
            total_bytes=total,
            ignored_directories=tuple(sorted(self.ignored_directories)),
            ignored_patterns=ignore_patterns,
        )

    def compare(
        self,
        baseline: WorkspaceBaseline,
        blob_root: str | Path,
        limits: SnapshotLimits,
    ) -> tuple[WorkspaceChangeSet, str]:
        root = Path(baseline.workspace.root).resolve()
        before = {item.path: item for item in baseline.files}
        after = self._inventory(root, limits, baseline.ignored_patterns)
        changes: list[WorkspaceChange] = []
        diff_parts: list[str] = []
        truncated = False
        used = 0
        for relative in sorted(set(before) | set(after), key=str.casefold):
            previous = before.get(relative)
            current = after.get(relative)
            if previous is not None and current is not None and previous.sha256 == current[1]:
                continue
            if previous is None:
                data, digest, mode = current
                change = WorkspaceChange(relative, "created", _is_binary(data) or _is_sensitive(relative), 0, len(data), "", digest)
            elif current is None:
                change = WorkspaceChange(relative, "deleted", previous.binary, previous.size, 0, previous.sha256, "")
            else:
                data, digest, mode = current
                change = WorkspaceChange(
                    relative, "modified", previous.binary or _is_binary(data) or _is_sensitive(relative),
                    previous.size, len(data), previous.sha256, digest,
                )
            changes.append(change)
            if change.binary or truncated:
                continue
            old_data = b"" if previous is None else self._blob(blob_root, previous)
            new_data = b"" if current is None else current[0]
            patch = self._text_diff(relative, change.kind, old_data, new_data)
            encoded = patch.encode("utf-8")
            remaining = limits.max_diff_bytes - used
            if len(encoded) > remaining:
                if remaining > 0:
                    diff_parts.append(encoded[:remaining].decode("utf-8", errors="ignore"))
                truncated = True
                continue
            diff_parts.append(patch)
            used += len(encoded)
        if truncated:
            diff_parts.append("\n[Orion diff truncated at configured safety limit.]\n")
        return (
            WorkspaceChangeSet(
                SNAPSHOT_SCHEMA_VERSION,
                str(root),
                tuple(changes),
                truncated,
            ),
            "".join(diff_parts),
        )

    def rollback(
        self,
        baseline: WorkspaceBaseline,
        changes: WorkspaceChangeSet,
        blob_root: str | Path,
    ) -> None:
        root = Path(baseline.workspace.root).resolve()
        if Path(changes.workspace_root).resolve() != root:
            raise WorkspaceRollbackError("Rollback workspace does not match its baseline.")
        before = {item.path: item for item in baseline.files}

        for change in changes.changes:
            path = _inside(root, change.path)
            if change.kind == "deleted":
                if path.exists():
                    raise WorkspaceRollbackError(
                        f"Rollback refused because {change.path} changed after the run."
                    )
                continue
            if not path.is_file() or _hash_file(path) != change.after_sha256:
                raise WorkspaceRollbackError(
                    f"Rollback refused because {change.path} changed after the run."
                )

        for change in changes.changes:
            path = _inside(root, change.path)
            if change.kind == "created":
                path.unlink()
                continue
            previous = before.get(change.path)
            if previous is None:
                raise WorkspaceRollbackError(f"Rollback preimage is missing: {change.path}")
            data = self._blob(blob_root, previous)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.orion-rollback.tmp")
            try:
                temporary.write_bytes(data)
                os.chmod(temporary, previous.mode)
                temporary.replace(path)
            finally:
                if temporary.exists():
                    temporary.unlink()

    def _inventory(
        self,
        root: Path,
        limits: SnapshotLimits,
        patterns: tuple[tuple[str, bool], ...],
    ) -> dict[str, tuple[bytes, str, int]]:
        result: dict[str, tuple[bytes, str, int]] = {}
        total = 0
        try:
            for path in self._paths(root, patterns):
                if len(result) >= limits.max_files:
                    raise WorkspaceSnapshotError("Post-run workspace exceeds the file-count safety limit.")
                if path.is_symlink():
                    raise WorkspaceSnapshotError("Post-run workspace contains an unsupported symbolic link.")
                file_stat = path.stat()
                size = file_stat.st_size
                if size > limits.max_file_bytes:
                    raise WorkspaceSnapshotError("Post-run workspace contains a file above the safety limit.")
                total += size
                if total > limits.max_total_bytes:
                    raise WorkspaceSnapshotError("Post-run workspace exceeds the total-size safety limit.")
                data = path.read_bytes()
                result[path.relative_to(root).as_posix()] = (
                    data,
                    hashlib.sha256(data).hexdigest(),
                    stat.S_IMODE(file_stat.st_mode),
                )
        except WorkspaceSnapshotError:
            raise
        except OSError as exc:
            raise WorkspaceSnapshotError(
                "Post-run workspace could not be scanned safely."
            ) from exc
        return result

    def _paths(self, root: Path, patterns: tuple[tuple[str, bool], ...]):
        def onerror(error: OSError) -> None:
            raise WorkspaceSnapshotError(
                "Workspace contains a directory Orion cannot scan safely."
            ) from error

        for current, directories, files in os.walk(
            root,
            topdown=True,
            followlinks=False,
            onerror=onerror,
        ):
            current_path = Path(current)
            for name in directories:
                if (current_path / name).is_symlink():
                    raise WorkspaceSnapshotError(
                        f"Workspace baseline cannot safely capture symbolic link: "
                        f"{(current_path / name).relative_to(root)}"
                    )
            directories[:] = sorted(
                name for name in directories
                if name.casefold() not in self.ignored_directories
                and not self._ignored_by_pattern(
                    (current_path / name).relative_to(root).as_posix() + "/",
                    patterns,
                )
            )
            for name in sorted(files):
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                if self._ignored_by_pattern(relative, patterns):
                    continue
                yield path

    @staticmethod
    def _gitignore_patterns(root: Path) -> tuple[tuple[str, bool], ...]:
        path = root / ".gitignore"
        if not path.is_file() or path.stat().st_size > 100_000:
            return ()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return ()
        patterns: list[tuple[str, bool]] = []
        for line in lines:
            item = line.strip()
            if not item or item.startswith("#"):
                continue
            negated = item.startswith("!")
            if negated:
                item = item[1:]
            if item:
                patterns.append((item.replace("\\", "/"), negated))
        return tuple(patterns)

    @staticmethod
    def _ignored_by_pattern(relative: str, patterns: tuple[tuple[str, bool], ...]) -> bool:
        ignored = False
        path = relative.lstrip("./")
        for pattern, negated in patterns:
            candidate = pattern.lstrip("/")
            directory_pattern = candidate.endswith("/")
            candidate = candidate.rstrip("/")
            if not candidate:
                continue
            if "/" in candidate:
                matched = fnmatch.fnmatch(path.rstrip("/"), candidate) or fnmatch.fnmatch(path, candidate + "/*")
            else:
                matched = any(fnmatch.fnmatch(part, candidate) for part in path.rstrip("/").split("/"))
            if directory_pattern and not relative.endswith("/") and not path.startswith(candidate + "/"):
                matched = False
            if matched:
                ignored = not negated
        return ignored

    @staticmethod
    def _blob(blob_root: str | Path, item: SnapshotFile) -> bytes:
        path = Path(blob_root).resolve() / item.blob
        try:
            data = zlib.decompress(path.read_bytes())
        except (OSError, zlib.error) as exc:
            raise WorkspaceRollbackError(f"Workspace preimage is unavailable: {item.path}") from exc
        if hashlib.sha256(data).hexdigest() != item.sha256:
            raise WorkspaceRollbackError(f"Workspace preimage failed integrity validation: {item.path}")
        return data

    @staticmethod
    def _text_diff(relative: str, kind: str, old_data: bytes, new_data: bytes) -> str:
        try:
            old_text = old_data.decode("utf-8")
            new_text = new_data.decode("utf-8")
        except UnicodeDecodeError:
            return ""
        old_name = "/dev/null" if kind == "created" else f"a/{relative}"
        new_name = "/dev/null" if kind == "deleted" else f"b/{relative}"
        lines = difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=old_name,
            tofile=new_name,
            lineterm="",
        )
        return "\n".join(_redact(line) for line in lines) + "\n"


def _relative_path(value: Any) -> str:
    raw = str(value).strip()
    path = Path(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Workspace snapshot path must remain inside the workspace.")
    return path.as_posix()


def _inside(root: Path, relative: str) -> Path:
    path = (root / _relative_path(relative)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise WorkspaceRollbackError("Workspace rollback path escapes its root.") from exc
    return path


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_binary(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _is_sensitive(relative: str) -> bool:
    path = Path(relative)
    name = path.name.casefold()
    return name in SENSITIVE_NAMES or name.startswith(".env.") or path.suffix.casefold() in SENSITIVE_SUFFIXES


def _redact(line: str) -> str:
    return SECRET_PATTERN.sub(lambda match: "<redacted>", line)


def _owner_only(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if path.is_dir() else 0))
    except OSError:
        pass


def baseline_json(value: WorkspaceBaseline) -> str:
    return json.dumps(value.to_dict(), indent=2, ensure_ascii=False) + "\n"


def changes_json(value: WorkspaceChangeSet) -> str:
    return json.dumps(value.to_dict(), indent=2, ensure_ascii=False) + "\n"
