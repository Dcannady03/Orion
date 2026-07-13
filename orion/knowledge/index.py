"""Persistent, lightweight workspace knowledge index."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass(frozen=True)
class IndexStats:
    files: int
    python_files: int
    classes: int
    functions: int
    imports: int
    todos: int
    tests: int


class KnowledgeIndex:
    """Build and query a portable structural index for the active workspace."""

    INDEX_NAME = "knowledge-index.json"
    IGNORED_DIRECTORIES = {
        ".git", ".orion", ".idea", ".vscode", "__pycache__", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", "node_modules", "venv", ".venv", "dist", "build",
    }
    TODO_PATTERN = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b\s*[:\-]", re.IGNORECASE)
    MAX_FILE_BYTES = 2_000_000

    def __init__(self, workspace: str | Path):
        self._lock = RLock()
        self.bind(workspace)

    def bind(self, workspace: str | Path) -> None:
        root = Path(workspace).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise NotADirectoryError(f"Knowledge index workspace is not a directory: {root}")
        self.root = root
        self.path = root / ".orion" / self.INDEX_NAME

    def exists(self) -> bool:
        return self.path.is_file()

    def build(self) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        symbols: list[dict[str, Any]] = []
        imports: list[dict[str, Any]] = []
        todos: list[dict[str, Any]] = []
        tests = 0
        python_files = 0

        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or self._ignored(path):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            relative = path.relative_to(self.root).as_posix()
            suffix = path.suffix.lower()
            files.append({"path": relative, "extension": suffix, "size": size})
            if self._is_test(relative):
                tests += 1
            if size > self.MAX_FILE_BYTES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                match = self.TODO_PATTERN.search(line)
                if match:
                    todos.append({"path": relative, "line": number, "marker": match.group(1).upper(), "text": line.strip()[:240]})
            if suffix != ".py":
                continue
            python_files += 1
            try:
                tree = ast.parse(text, filename=relative)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"
                    symbols.append({"kind": kind, "name": node.name, "path": relative, "line": node.lineno})
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append({"module": alias.name, "path": relative, "line": node.lineno})
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    imports.append({"module": module, "path": relative, "line": node.lineno})

        document = {
            "schema_version": 1,
            "workspace": str(self.root),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "stats": {
                "files": len(files),
                "python_files": python_files,
                "classes": sum(item["kind"] == "class" for item in symbols),
                "functions": sum(item["kind"] == "function" for item in symbols),
                "imports": len(imports),
                "todos": len(todos),
                "tests": tests,
            },
            "files": files,
            "symbols": symbols,
            "imports": imports,
            "todos": todos,
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            temp.replace(self.path)
        return document

    def load(self) -> dict[str, Any]:
        if not self.exists():
            raise FileNotFoundError("Knowledge index has not been built. Run 'index build'.")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"Could not read knowledge index: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("Knowledge index must contain a JSON object.")
        return value

    def status(self) -> dict[str, Any]:
        data = self.load()
        return {"built_at": data.get("built_at", ""), "workspace": data.get("workspace", ""), **data.get("stats", {})}

    def symbols(self, kind: str | None = None) -> list[dict[str, Any]]:
        items = self.load().get("symbols", [])
        return [item for item in items if not kind or item.get("kind") == kind]

    def todos(self) -> list[dict[str, Any]]:
        return list(self.load().get("todos", []))

    def imports(self) -> list[dict[str, Any]]:
        return list(self.load().get("imports", []))

    def query(self, text: str) -> list[dict[str, Any]]:
        needle = text.strip().lower()
        if not needle:
            raise ValueError("Index query cannot be empty.")
        data = self.load()
        results: list[dict[str, Any]] = []
        for item in data.get("symbols", []):
            if needle in item.get("name", "").lower() or needle in item.get("path", "").lower():
                results.append({"type": "symbol", **item})
        for item in data.get("files", []):
            if needle in item.get("path", "").lower():
                results.append({"type": "file", **item})
        for item in data.get("todos", []):
            if needle in item.get("text", "").lower() or needle in item.get("path", "").lower():
                results.append({"type": "todo", **item})
        return results

    def summary(self) -> str:
        if not self.exists():
            return ""
        stats = self.status()
        return (
            "Workspace knowledge index: "
            f"{stats.get('files', 0)} files, {stats.get('classes', 0)} classes, "
            f"{stats.get('functions', 0)} functions, {stats.get('todos', 0)} TODO/FIXME items, "
            f"{stats.get('tests', 0)} test files."
        )

    def _ignored(self, path: Path) -> bool:
        relative_parts = path.relative_to(self.root).parts
        return any(part in self.IGNORED_DIRECTORIES for part in relative_parts)

    @staticmethod
    def _is_test(relative: str) -> bool:
        path = Path(relative)
        return "tests" in path.parts or path.name.startswith("test_") or path.name.endswith("_test.py")
