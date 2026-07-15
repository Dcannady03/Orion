"""Persistent, user-owned project context for Orion workspaces."""
from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any


class ProjectContext:
    """Manage portable project metadata, checkpoints, and rules in ``.orion``."""

    FILES = {
        "project": "project.json",
        "history": "history.json",
        "tasks": "tasks.json",
        "metrics": "metrics.json",
        "settings": "settings.json",
        "notes": "notes.md",
        "memory": "memory.db",
    }

    def __init__(self, workspace_root: str | Path) -> None:
        self._lock = RLock()
        self._root = Path(workspace_root).expanduser().resolve()

    @property
    def workspace_root(self) -> Path:
        return self._root

    @property
    def context_dir(self) -> Path:
        return self._root / ".orion"

    @property
    def database_path(self) -> Path:
        return self.context_dir / self.FILES["memory"]

    @property
    def initialized(self) -> bool:
        return (self.context_dir / self.FILES["project"]).is_file()

    def bind(self, workspace_root: str | Path) -> None:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {root}")
        self._root = root
        if self.initialized:
            self._ensure_database()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @contextmanager
    def _database(self):
        self.context_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _ensure_database(self) -> None:
        with self._database() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    current_task TEXT NOT NULL DEFAULT '',
                    next_step TEXT NOT NULL DEFAULT '',
                    completed_work TEXT NOT NULL DEFAULT '',
                    open_questions TEXT NOT NULL DEFAULT '',
                    important_files TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    rule TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                """
            )

    def initialize(
        self,
        *,
        name: str | None = None,
        description: str = "",
        version: str = "0.2.5",
        phase: str = "Knowledge",
        current_goal: str = "Build portable project handoff memory",
        preferred_model: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self.context_dir.mkdir(parents=True, exist_ok=True)
            project_path = self.context_dir / self.FILES["project"]
            if project_path.exists():
                self._ensure_database()
                return self.project()

            now = self._now()
            project = {
                "name": (name or self._root.name).strip(),
                "description": description.strip(),
                "version": version,
                "phase": phase,
                "current_goal": current_goal,
                "preferred_model": preferred_model,
                "workspace": str(self._root),
                "created_at": now,
                "updated_at": now,
            }
            self._write_json(project_path, project)
            self._write_json(self.context_dir / self.FILES["history"], [])
            self._write_json(self.context_dir / self.FILES["tasks"], [])
            self._write_json(self.context_dir / self.FILES["metrics"], {"history_entries": 0, "tasks_open": 0, "tasks_completed": 0})
            self._write_json(self.context_dir / self.FILES["settings"], {})
            (self.context_dir / self.FILES["notes"]).write_text(f"# {project['name']} Notes\n\n", encoding="utf-8")
            self._ensure_database()
            self.add_history("project_initialized", f"Initialized project {project['name']}")
            return deepcopy(project)

    def _require_initialized(self) -> None:
        if not self.initialized:
            raise FileNotFoundError("Project context is not initialized. Run 'project init'.")

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return deepcopy(default)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"Could not read valid project data from {path.name}: {exc}") from exc

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp.replace(path)

    def project(self) -> dict[str, Any]:
        self._require_initialized()
        data = self._read_json(self.context_dir / self.FILES["project"], {})
        if not isinstance(data, dict):
            raise ValueError("project.json must contain a JSON object.")
        return deepcopy(data)


    def matches_workspace(self) -> bool:
        """Return False when portable metadata was copied from another workspace."""
        if not self.initialized:
            return False
        try:
            stored = str(self.project().get("workspace", "")).strip()
            return not stored or Path(stored).expanduser().resolve() == self._root
        except (FileNotFoundError, ValueError, OSError):
            return False

    def set_field(self, field: str, value: str) -> dict[str, Any]:
        self._require_initialized()
        normalized = field.strip().lower().replace("-", "_").replace(" ", "_")
        allowed = {"name", "description", "version", "phase", "current_goal", "preferred_model"}
        aliases = {"goal": "current_goal", "model": "preferred_model"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in allowed:
            raise ValueError(f"Unsupported project field: {field}")
        if not value.strip():
            raise ValueError("Project value cannot be empty.")
        with self._lock:
            data = self.project()
            data[normalized] = value.strip()
            data["workspace"] = str(self._root)
            data["updated_at"] = self._now()
            self._write_json(self.context_dir / self.FILES["project"], data)
            self.add_history("project_updated", f"Set {normalized} to {value.strip()}")
            return deepcopy(data)

    def add_note(self, text: str) -> None:
        self._require_initialized()
        note = text.strip()
        if not note:
            raise ValueError("Note cannot be empty.")
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with (self.context_dir / self.FILES["notes"]).open("a", encoding="utf-8") as handle:
            handle.write(f"## {stamp}\n\n{note}\n\n")
        self.add_history("note_added", note[:120])

    def add_checkpoint(self, summary: str, *, current_task: str = "", next_step: str = "", completed_work: str = "", open_questions: str = "", important_files: str = "") -> dict[str, Any]:
        self._require_initialized()
        if not summary.strip():
            raise ValueError("Checkpoint summary cannot be empty.")
        self._ensure_database()
        values = (self._now(), summary.strip(), current_task.strip(), next_step.strip(), completed_work.strip(), open_questions.strip(), important_files.strip())
        with self._database() as connection:
            cursor = connection.execute(
                "INSERT INTO checkpoints (created_at, summary, current_task, next_step, completed_work, open_questions, important_files) VALUES (?, ?, ?, ?, ?, ?, ?)", values,
            )
            checkpoint_id = cursor.lastrowid
        self.add_history("checkpoint_saved", summary.strip()[:120])
        return {"id": checkpoint_id, "created_at": values[0], "summary": values[1], "current_task": values[2], "next_step": values[3], "completed_work": values[4], "open_questions": values[5], "important_files": values[6]}

    def latest_checkpoint(self) -> dict[str, Any] | None:
        self._require_initialized()
        self._ensure_database()
        with self._database() as connection:
            row = connection.execute("SELECT * FROM checkpoints ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def add_rule(self, rule: str) -> dict[str, Any]:
        self._require_initialized()
        text = rule.strip()
        if not text:
            raise ValueError("Project rule cannot be empty.")
        self._ensure_database()
        try:
            with self._database() as connection:
                cursor = connection.execute("INSERT INTO rules (created_at, rule, enabled) VALUES (?, ?, 1)", (self._now(), text))
                rule_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            raise ValueError("That project rule already exists.") from exc
        self.add_history("rule_added", text[:120])
        return {"id": rule_id, "rule": text, "enabled": True}

    def rules(self, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        self._require_initialized()
        self._ensure_database()
        query = "SELECT id, created_at, rule, enabled FROM rules"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id"
        with self._database() as connection:
            rows = connection.execute(query).fetchall()
        return [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]

    def remove_rule(self, rule_id: int) -> bool:
        self._require_initialized()
        self._ensure_database()
        with self._database() as connection:
            cursor = connection.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        if cursor.rowcount:
            self.add_history("rule_removed", f"Removed project rule {rule_id}")
            return True
        return False

    def resume(self) -> dict[str, Any]:
        self._require_initialized()
        return {"project": self.project(), "checkpoint": self.latest_checkpoint(), "rules": self.rules()}

    def history(self) -> list[dict[str, Any]]:
        self._require_initialized()
        data = self._read_json(self.context_dir / self.FILES["history"], [])
        if not isinstance(data, list):
            raise ValueError("history.json must contain a JSON array.")
        return deepcopy(data)

    def add_history(self, event_type: str, summary: str) -> dict[str, Any]:
        self._require_initialized()
        with self._lock:
            entries = self.history()
            entry = {"timestamp": self._now(), "type": event_type, "summary": summary.strip()}
            entries.append(entry)
            self._write_json(self.context_dir / self.FILES["history"], entries)
            metrics_path = self.context_dir / self.FILES["metrics"]
            metrics = self._read_json(metrics_path, {})
            metrics["history_entries"] = len(entries)
            self._write_json(metrics_path, metrics)
            return deepcopy(entry)

    def tasks(self) -> list[dict[str, Any]]:
        self._require_initialized()
        data = self._read_json(self.context_dir / self.FILES["tasks"], [])
        if not isinstance(data, list):
            raise ValueError("tasks.json must contain a JSON array.")
        return deepcopy(data)

    def metrics(self) -> dict[str, Any]:
        self._require_initialized()
        data = self._read_json(self.context_dir / self.FILES["metrics"], {})
        if not isinstance(data, dict):
            raise ValueError("metrics.json must contain a JSON object.")
        tasks = self.tasks()
        data["history_entries"] = len(self.history())
        data["tasks_open"] = sum(task.get("status") != "completed" for task in tasks)
        data["tasks_completed"] = sum(task.get("status") == "completed" for task in tasks)
        data["rules"] = len(self.rules())
        data["has_checkpoint"] = self.latest_checkpoint() is not None
        return deepcopy(data)
