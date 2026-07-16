"""Provider-neutral Home Center snapshots for Orion interfaces."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from orion.services.briefing import Briefing, BriefingService


@dataclass(frozen=True, slots=True)
class HomeCard:
    """One interface-ready card displayed by Orion Home."""

    title: str
    message: str
    source: str
    icon: str = "-"


@dataclass(frozen=True, slots=True)
class HomeSnapshot:
    """A detached Home Center view that any interface can render."""

    greeting: str
    user_name: str
    location: str
    generated_at: datetime
    cards: tuple[HomeCard, ...]
    provider_errors: tuple[tuple[str, str], ...] = ()


class HomeService:
    """Build Home Center snapshots without coupling data to a specific UI."""

    def __init__(self, orion, briefing_service: BriefingService) -> None:
        self.orion = orion
        self.briefing_service = briefing_service

    @staticmethod
    def _greeting(now: datetime) -> str:
        if now.hour < 12:
            return "Good morning"
        if now.hour < 18:
            return "Good afternoon"
        return "Good evening"

    def _tasks_card(self) -> HomeCard:
        if not self.orion.project_context.initialized:
            return HomeCard("Tasks", "Project context is not initialized", "Home", "[i]")
        tasks = self.orion.project_context.tasks()
        open_tasks = [task for task in tasks if str(task.get("status", "")).lower() != "completed"]
        if not open_tasks:
            return HomeCard("Tasks", "No open project tasks", "Home", "[OK]")
        next_task = str(open_tasks[0].get("title") or open_tasks[0].get("task") or "Next task").strip()
        suffix = f"; next: {next_task}" if next_task else ""
        return HomeCard("Tasks", f"{len(open_tasks)} open{suffix}", "Home", "[TASK]")

    def _project_card(self) -> HomeCard:
        if not self.orion.project_context.initialized:
            return HomeCard("Project", f"{self.orion.workspace_manager.root.name} is active", "Home", "[PRJ]")
        project = self.orion.project_context.project()
        name = str(project.get("name") or self.orion.workspace_manager.root.name).strip()
        goal = str(project.get("current_goal") or project.get("phase") or "Ready").strip()
        return HomeCard("Project", f"{name}: {goal}", "Home", "[PRJ]")

    def _activity_card(self) -> HomeCard:
        candidates: list[tuple[str, str]] = []
        if self.orion.project_context.initialized:
            history = self.orion.project_context.history()
            if history:
                latest = history[-1]
                candidates.append((str(latest.get("timestamp", "")), str(latest.get("summary", "Activity recorded"))))
        action_entries = self.orion.action_history.entries(limit=1)
        if action_entries:
            latest = action_entries[-1]
            action = latest.get("action", {})
            action_type = str(action.get("type", "action")).replace("_", " ")
            event = str(latest.get("event", "recorded")).replace("_", " ")
            candidates.append((str(latest.get("timestamp", "")), f"{action_type}: {event}"))
        if not candidates:
            return HomeCard("Activity", "No recent activity", "Home", "[REC]")
        _, message = max(candidates, key=lambda item: item[0])
        return HomeCard("Activity", message[:100], "Home", "[REC]")

    def _diagnostics_card(self) -> HomeCard:
        service_count = len(self.orion.services.names())
        plugin_count = self.orion.plugin_manager.loaded_count()
        exists = getattr(self.orion.knowledge_index, "exists", None)
        index_built = bool(exists()) if callable(exists) else bool(getattr(self.orion.knowledge_index, "built", False))
        index_state = "index built" if index_built else "index not built"
        return HomeCard(
            "System",
            f"{self.orion.status}; {service_count} services, {plugin_count} plugins, {index_state}",
            "Home",
            "[SYS]",
        )

    def _center_cards(self) -> tuple[tuple[HomeCard, ...], tuple[tuple[str, str], ...]]:
        cards: list[HomeCard] = []
        errors: list[tuple[str, str]] = []
        builders: tuple[tuple[str, Callable[[], HomeCard]], ...] = (
            ("Tasks", self._tasks_card),
            ("Project", self._project_card),
            ("Activity", self._activity_card),
            ("System", self._diagnostics_card),
        )
        for name, builder in builders:
            try:
                cards.append(builder())
            except Exception as exc:  # Home cards must never prevent Orion startup
                errors.append((name, str(exc)))
        return tuple(cards), tuple(errors)

    def build(self, *, now: datetime | None = None) -> HomeSnapshot:
        generated_at = now or datetime.now()
        briefing: Briefing = self.briefing_service.build()
        cards = [
            HomeCard(item.title, item.message, item.source, item.icon)
            for item in briefing.items
        ]
        center_cards, center_errors = self._center_cards()
        cards.extend(center_cards)
        errors = [(error.provider, error.message) for error in briefing.errors]
        errors.extend(center_errors)
        location = self.orion.profile_manager.get("location", "") or "Location not set"
        return HomeSnapshot(
            greeting=self._greeting(generated_at),
            user_name=self.orion.user_name,
            location=location,
            generated_at=generated_at,
            cards=tuple(cards),
            provider_errors=tuple(errors),
        )
