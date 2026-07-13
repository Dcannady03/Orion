"""Safe workspace search services for Orion.

The search skill is deliberately read-only. It searches file names and UTF-8
text content while respecting the active workspace boundary and conservative
resource limits.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from orion.services.workspace import WorkspaceManager


@dataclass(frozen=True)
class SearchMatch:
    """One content match inside a workspace file."""

    relative_path: str
    line_number: int
    line: str


@dataclass(frozen=True)
class SearchReport:
    """Results and scan statistics for a search operation."""

    matches: tuple[SearchMatch, ...]
    files_scanned: int
    files_skipped: int
    truncated: bool


class SearchSkill:
    """Search file names and text content inside the active workspace."""

    DEFAULT_IGNORED_DIRECTORIES = frozenset(
        {
            ".git",
            ".hg",
            ".svn",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".venv",
            "venv",
            "env",
            "node_modules",
            "build",
            "dist",
        }
    )

    TYPE_SUFFIXES = {
        "py": frozenset({".py"}),
        "python": frozenset({".py"}),
        "js": frozenset({".js", ".jsx"}),
        "javascript": frozenset({".js", ".jsx"}),
        "ts": frozenset({".ts", ".tsx"}),
        "typescript": frozenset({".ts", ".tsx"}),
        "json": frozenset({".json"}),
        "yaml": frozenset({".yaml", ".yml"}),
        "md": frozenset({".md"}),
        "markdown": frozenset({".md"}),
        "toml": frozenset({".toml"}),
        "text": frozenset({".txt"}),
    }

    def __init__(
        self,
        workspace_manager: WorkspaceManager,
        *,
        max_file_bytes: int = 1_000_000,
        max_results: int = 100,
        ignored_directories: Iterable[str] | None = None,
    ):
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive.")
        if max_results <= 0:
            raise ValueError("max_results must be positive.")
        self.workspace_manager = workspace_manager
        self.max_file_bytes = max_file_bytes
        self.max_results = max_results
        self.ignored_directories = frozenset(
            ignored_directories or self.DEFAULT_IGNORED_DIRECTORIES
        )

    def search_text(
        self,
        query: str,
        *,
        relative_path: str | Path = ".",
        regex: bool = False,
        case_sensitive: bool = False,
        file_type: str | None = None,
        max_results: int | None = None,
    ) -> SearchReport:
        """Search UTF-8 text files and return line-level matches."""
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Search query cannot be empty.")
        limit = self._validated_limit(max_results)
        matcher = self._compile_matcher(normalized_query, regex, case_sensitive)
        suffixes = self._suffixes_for_type(file_type)

        matches: list[SearchMatch] = []
        scanned = 0
        skipped = 0
        truncated = False

        for path in self._iter_files(relative_path, suffixes):
            try:
                if path.stat().st_size > self.max_file_bytes:
                    skipped += 1
                    continue
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped += 1
                continue

            scanned += 1
            relative = str(path.relative_to(self.workspace_manager.root))
            for line_number, line in enumerate(content.splitlines(), start=1):
                if matcher(line):
                    matches.append(
                        SearchMatch(
                            relative_path=relative,
                            line_number=line_number,
                            line=line.strip(),
                        )
                    )
                    if len(matches) >= limit:
                        truncated = True
                        return SearchReport(tuple(matches), scanned, skipped, truncated)

        return SearchReport(tuple(matches), scanned, skipped, truncated)

    def search_files(
        self,
        query: str,
        *,
        relative_path: str | Path = ".",
        regex: bool = False,
        case_sensitive: bool = False,
        file_type: str | None = None,
        max_results: int | None = None,
    ) -> tuple[str, ...]:
        """Search workspace file names and relative paths."""
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Search query cannot be empty.")
        limit = self._validated_limit(max_results)
        matcher = self._compile_matcher(normalized_query, regex, case_sensitive)
        suffixes = self._suffixes_for_type(file_type)
        results: list[str] = []
        for path in self._iter_files(relative_path, suffixes):
            relative = str(path.relative_to(self.workspace_manager.root))
            if matcher(relative):
                results.append(relative)
                if len(results) >= limit:
                    break
        return tuple(results)

    def _iter_files(
        self, relative_path: str | Path, suffixes: frozenset[str] | None
    ) -> Iterable[Path]:
        root = self.workspace_manager.resolve(relative_path)
        if not root.exists():
            raise FileNotFoundError(f"Workspace path not found: {relative_path}")
        if root.is_file():
            if suffixes is None or root.suffix.lower() in suffixes:
                yield root
            return
        if not root.is_dir():
            raise NotADirectoryError(f"Not a directory: {relative_path}")

        for path in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
            try:
                relative_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if any(part in self.ignored_directories for part in relative_parts):
                continue
            if not path.is_file():
                continue
            if suffixes is not None and path.suffix.lower() not in suffixes:
                continue
            yield path

    def _compile_matcher(self, query: str, regex: bool, case_sensitive: bool):
        if regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(query, flags)
            except re.error as exc:
                raise ValueError(f"Invalid regular expression: {exc}") from exc
            return lambda value: pattern.search(value) is not None

        needle = query if case_sensitive else query.casefold()
        if case_sensitive:
            return lambda value: needle in value
        return lambda value: needle in value.casefold()

    def _suffixes_for_type(self, file_type: str | None) -> frozenset[str] | None:
        if file_type is None:
            return None
        normalized = file_type.strip().lower().lstrip(".")
        if not normalized:
            return None
        if normalized in self.TYPE_SUFFIXES:
            return self.TYPE_SUFFIXES[normalized]
        if re.fullmatch(r"[a-z0-9_+-]+", normalized):
            return frozenset({f".{normalized}"})
        raise ValueError(f"Invalid file type: {file_type}")

    def _validated_limit(self, requested: int | None) -> int:
        limit = self.max_results if requested is None else requested
        if limit <= 0:
            raise ValueError("Search result limit must be positive.")
        return min(limit, self.max_results)
