"""Observation-only Mission creation, history, validation, and reconciliation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from orion.application.events import OrionEvent
from orion.application.goals.proposals.integrity import proposal_plan_hash
from orion.application.goals.proposals.models import GoalProposalStatus
from orion.application.missions.models import (
    MISSION_SCHEMA_VERSION,
    Mission,
    MissionLink,
    MissionStage,
    MissionStatus,
    MissionValidation,
)
from orion.application.missions.projection import MissionProjectionEngine
from orion.application.missions.repository import MissionRepository


@dataclass(frozen=True)
class MissionCreation:
    mission: Mission
    created: bool
    reconciled: bool


@dataclass(frozen=True)
class MissionReconciliation:
    mission: Mission
    changed: bool
    events: tuple[OrionEvent, ...]


class MissionService:
    """Project persistent mission state without calling mutation boundaries."""

    ELIGIBLE_PROPOSAL_STATUSES = frozenset({
        GoalProposalStatus.ACCEPTED,
        GoalProposalStatus.CONSUMED,
    })

    def __init__(
        self,
        repository: MissionRepository,
        proposal_repository,
        event_store,
        *,
        projection: MissionProjectionEngine | None = None,
        history_limit: int = 1_000,
        clock=None,
        id_factory=None,
    ) -> None:
        if not 1 <= int(history_limit) <= 10_000:
            raise ValueError("Mission history limit must be between 1 and 10000.")
        self.repository = repository
        self.proposal_repository = proposal_repository
        self.event_store = event_store
        self.projection = projection or MissionProjectionEngine()
        store_limit = getattr(event_store, "history_max_limit", history_limit)
        self.history_limit = min(int(history_limit), int(store_limit))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: f"mission-{uuid4().hex}")
        self._lock = RLock()

    def create(self, proposal_id: str) -> MissionCreation:
        with self._lock:
            existing = self.repository.find_by_proposal(proposal_id)
            if existing is not None:
                reconciled = self.reconcile(existing.mission_id)
                return MissionCreation(
                    reconciled.mission,
                    created=False,
                    reconciled=reconciled.changed,
                )
            proposal = self.proposal_repository.get(proposal_id)
            self._verify_proposal(proposal)
            mission = self._mission_from_proposal(proposal)
            self.repository.create(mission)
            reconciled = self.reconcile(mission.mission_id)
            return MissionCreation(
                reconciled.mission,
                created=True,
                reconciled=reconciled.changed,
            )

    def get(self, mission_id: str) -> Mission:
        return self.repository.get(mission_id)

    def list(
        self,
        *,
        status: MissionStatus | str | None = None,
        goal_id: str = "",
        proposal_id: str = "",
        limit: int = 100,
    ) -> tuple[Mission, ...]:
        return self.repository.list(
            status=status,
            goal_id=goal_id,
            proposal_id=proposal_id,
            limit=limit,
        )

    def history(
        self,
        mission_id: str,
        *,
        limit: int = 100,
    ) -> tuple[tuple[OrionEvent, ...], tuple[str, ...]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError("Mission history limit must be between 1 and 1000.")
        mission = self.repository.get(mission_id)
        events, warnings, _history_available = self._events_for_mission(mission)
        return tuple(reversed(events[-limit:])), warnings

    def reconcile(self, mission_id: str) -> MissionReconciliation:
        with self._lock:
            mission = self.repository.get(mission_id)
            events, warnings, history_available = self._events_for_mission(mission)
            if not history_available:
                combined_warnings = tuple(dict.fromkeys((*mission.warnings, *warnings)))
                if combined_warnings == mission.warnings:
                    return MissionReconciliation(mission, False, events)
                updated = replace(
                    mission,
                    updated_at=self._now(),
                    warnings=combined_warnings,
                )
                self.repository.replace(updated, expected_updated_at=mission.updated_at)
                return MissionReconciliation(updated, True, events)
            projection = self.projection.project(
                mission,
                events,
                warnings=warnings,
            )
            changed = (
                self.projection.signature(mission)
                != self.projection.projected_signature(projection)
            )
            if not changed:
                return MissionReconciliation(mission, False, events)
            updated = replace(
                mission,
                proposal_status=projection.proposal_status,
                status=projection.status,
                stage=projection.stage,
                progress=projection.progress,
                updated_at=self._now(),
                failed_at=projection.failed_at,
                current_action=projection.current_action,
                next_action=projection.next_action,
                next_action_reason=projection.next_action_reason,
                links=projection.links,
                event_refs=projection.event_refs,
                event_cursor=len(projection.event_refs),
                last_event_id=projection.last_event_id,
                last_event_at=projection.last_event_at,
                warnings=projection.warnings,
            )
            self.repository.replace(updated, expected_updated_at=mission.updated_at)
            return MissionReconciliation(updated, True, events)

    def validate(self, mission_id: str) -> MissionValidation:
        checked_at = self._now()
        mission = self.repository.get(mission_id)
        errors: list[str] = []
        warnings: list[str] = []
        path_safe = self.repository.validate_location(mission.mission_id)
        if not path_safe:
            errors.append("Mission persistence path is unsafe.")
        proposal_exists = True
        proposal_identity_valid = False
        goal_identity_valid = False
        try:
            proposal = self.proposal_repository.get(mission.proposal_id)
        except (FileNotFoundError, OSError, PermissionError, TypeError, ValueError) as exc:
            proposal_exists = False
            errors.append(f"Referenced Goal Proposal is unavailable: {exc}")
            proposal = None
        if proposal is not None:
            proposal_identity_valid = (
                proposal.proposal_id == mission.proposal_id
                and proposal.version == mission.proposal_version
            )
            goal_identity_valid = proposal.goal_id == mission.goal_id
            if not proposal_identity_valid:
                errors.append("Mission Goal Proposal identity or version does not match.")
            if not goal_identity_valid:
                errors.append("Mission Goal ID does not match its Goal Proposal.")
            if proposal_plan_hash(proposal.snapshot()) != proposal.plan_hash:
                errors.append("Referenced Goal Proposal immutable plan hash is invalid.")
        links_valid = len({item.link_type for item in mission.links}) == len(mission.links)
        if not links_valid:
            errors.append("Mission links are invalid.")
        event_cursor_valid = mission.event_cursor == len(mission.event_refs)
        if not event_cursor_valid:
            errors.append("Mission event cursor is invalid.")
        events, history_warnings, history_available = self._events_for_mission(mission)
        warnings.extend(history_warnings)
        if history_available:
            rebuilt = self.projection.project(
                mission,
                events,
                warnings=history_warnings,
            )
            projection_matches = (
                self.projection.signature(mission)
                == self.projection.projected_signature(rebuilt)
            )
        else:
            projection_matches = False
            errors.append("Mission projection cannot be rebuilt without Event history.")
        if history_available and not projection_matches:
            errors.append("Persisted Mission projection is stale; reconcile explicitly.")
        valid = not errors
        return MissionValidation(
            mission_id=mission.mission_id,
            valid=valid,
            checked_at=checked_at,
            schema_valid=mission.schema_version == MISSION_SCHEMA_VERSION,
            proposal_exists=proposal_exists,
            proposal_identity_valid=proposal_identity_valid,
            goal_identity_valid=goal_identity_valid,
            path_safe=path_safe,
            links_valid=links_valid,
            event_cursor_valid=event_cursor_valid,
            projection_matches=projection_matches,
            warnings=tuple(dict.fromkeys(warnings)),
            errors=tuple(dict.fromkeys(errors)),
        )

    def _mission_from_proposal(self, proposal: Any) -> Mission:
        now = self._now()
        linked_at = str(proposal.accepted_at or proposal.consumed_at or now)
        links = {
            "goal": MissionLink("goal", proposal.goal_id, "goal_proposal", linked_at),
            "proposal": MissionLink(
                "proposal",
                proposal.proposal_id,
                "goal_proposal",
                linked_at,
            ),
        }
        summary = proposal.dispatch_summary
        task_id = str(summary.get("team_task_id", "")).strip()
        if task_id:
            links["team_task"] = MissionLink(
                "team_task",
                task_id,
                "goal_proposal.dispatch_summary",
                str(proposal.consumed_at or linked_at),
            )
        approval_id = str(summary.get("approval_id", "")).strip()
        if approval_id:
            links["approval"] = MissionLink(
                "approval",
                approval_id,
                "goal_proposal.dispatch_summary",
                str(proposal.consumed_at or linked_at),
            )
        status = MissionStatus.PLANNING
        stage = MissionStage.TEAM_PLANNING
        progress = 15 if proposal.status is GoalProposalStatus.CONSUMED else 10
        current_action = "team.plan"
        next_action = ""
        next_reason = "Waiting for an authoritative Team plan event."
        if task_id:
            current_action = "team.show"
            next_reason = (
                "A persisted Team task is linked, but no authoritative event "
                "establishes an actionable approval state."
            )
        metadata = proposal.metadata
        return Mission(
            schema_version=MISSION_SCHEMA_VERSION,
            mission_id=str(self._id_factory()).strip().lower(),
            goal_id=proposal.goal_id,
            proposal_id=proposal.proposal_id,
            proposal_version=proposal.version,
            title=self._title(proposal.goal_text),
            goal_text=proposal.goal_text,
            classification=proposal.classification,
            workspace=proposal.workspace,
            department=proposal.department_name,
            priority=proposal.priority,
            proposal_status=proposal.status.value,
            status=status,
            stage=stage,
            progress=progress,
            created_at=now,
            updated_at=now,
            current_action=current_action,
            next_action=next_action,
            next_action_reason=next_reason,
            links=tuple(sorted(links.values(), key=lambda item: item.link_type)),
            event_refs=(),
            event_cursor=0,
            warnings=self._metadata_strings(metadata.get("goal_warnings", ())),
            risks=self._metadata_strings(metadata.get("goal_risks", ())),
        )

    def _events_for_mission(
        self,
        mission: Mission,
    ) -> tuple[tuple[OrionEvent, ...], tuple[str, ...], bool]:
        if self.event_store is None:
            return (
                (),
                ("Event Store is disabled; persisted Mission projection was preserved.",),
                False,
            )
        collected: dict[str, OrionEvent] = {}
        warnings: list[str] = []
        history_available = True
        filters = [
            {"correlation_id": mission.goal_id},
            {"subject_id": mission.proposal_id},
        ]
        filters.extend(
            {"subject_id": link.subject_id}
            for link in mission.links
            if link.link_type not in {"goal", "proposal"}
        )
        try:
            for selected in filters:
                history = self.event_store.history(
                    **selected,
                    limit=self.history_limit,
                )
                warnings.extend(history.warnings)
                for event in history.events:
                    collected[event.event_id] = event
        except (OSError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
            warnings.append(f"Mission event history is unavailable: {exc}")
            history_available = False
        ordered = tuple(sorted(
            collected.values(),
            key=lambda event: (event.occurred_at, event.event_id),
        ))
        return (
            self.projection.relevant_events(mission, ordered),
            tuple(dict.fromkeys(warnings)),
            history_available,
        )

    @staticmethod
    def _verify_proposal(proposal: Any) -> None:
        if proposal.status not in MissionService.ELIGIBLE_PROPOSAL_STATUSES:
            raise ValueError(
                "Mission creation requires an accepted or consumed Goal Proposal; "
                f"current status is {proposal.status.value}."
            )
        if proposal_plan_hash(proposal.snapshot()) != proposal.plan_hash:
            raise ValueError("Goal Proposal immutable plan hash does not match.")

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime):
            raise TypeError("Mission clock must return a datetime.")
        if value.tzinfo is None:
            raise ValueError("Mission clock must be timezone-aware.")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _title(goal_text: str) -> str:
        compact = " ".join(str(goal_text).split())
        return compact[:200]

    @staticmethod
    def _metadata_strings(value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(dict.fromkeys(
            str(item).strip() for item in value if str(item).strip()
        ))
