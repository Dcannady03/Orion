"""Persistent, user-owned project context for Orion workspaces."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any


class ProjectContext:
    """Manage persistent metadata stored in a workspace's ``.orion`` folder."""

    FILES = {
        "project": "project.json",
        "history": "history.json",
        "tasks": "tasks.json",
        "metrics": "metrics.json",
        "settings": "settings.json",
        "notes": "notes.md",
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
    def initialized(self) -> bool:
        return (self.context_dir / self.FILES["project"]).is_file()

    def bind(self, workspace_root: str | Path) -> None:
        """Point the service at another workspace without creating files."""
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {root}")
        self._root = root

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def initialize(
        self,
        *,
        name: str | None = None,
        description: str = "",
        version: str = "0.2.1",
        phase: str = "Intelligence Core",
        current_goal: str = "Build File Search",
        preferred_model: str = "",
    ) -> dict[str, Any]:
        """Create a portable project context, preserving existing data."""
        with self._lock:
            self.context_dir.mkdir(parents=True, exist_ok=True)
            project_path = self.context_dir / self.FILES["project"]
            if project_path.exists():
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
            self._write_json(
                self.context_dir / self.FILES["metrics"],
                {"history_entries": 0, "tasks_open": 0, "tasks_completed": 0},
            )
            self._write_json(self.context_dir / self.FILES["settings"], {})
            (self.context_dir / self.FILES["notes"]).write_text(
                f"# {project['name']} Notes\n\n", encoding="utf-8"
            )
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
        return deepcopy(data)
