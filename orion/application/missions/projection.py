"""Deterministic, observation-only Mission projection rules."""
from __future__ import annotations

from dataclasses import dataclass
from orion.application.events import EventTypes, OrionEvent
from orion.application.interface_actions import interface_action
from orion.application.missions.models import (
    LINK_ID_PATTERN,
    Mission,
    MissionEventRef,
    MissionLink,
    MissionStage,
    MissionStatus,
)


MISSION_PROGRESS = {
    "created": 5,
    "proposal_accepted": 10,
    "proposal_consumed": 15,
    "team_plan_created": 20,
    "awaiting_approval": 30,
    "approved": 35,
}

_EVENT_ORDER = {
    EventTypes.GOAL_PROPOSAL_CREATED: 10,
    EventTypes.GOAL_PROPOSAL_VALIDATED: 20,
    EventTypes.GOAL_PROPOSAL_ACCEPTED: 30,
    EventTypes.TEAM_PLAN_CREATED: 40,
    EventTypes.GOAL_PROPOSAL_CONSUMED: 50,
    EventTypes.TEAM_PLAN_APPROVED: 60,
    EventTypes.GOAL_PROPOSAL_FAILED: 70,
}


@dataclass(frozen=True)
class MissionProjection:
    proposal_status: str
    status: MissionStatus
    stage: MissionStage
    progress: int
    current_action: str
    next_action: str
    next_action_reason: str
    links: tuple[MissionLink, ...]
    event_refs: tuple[MissionEventRef, ...]
    last_event_id: str
    last_event_at: str
    failed_at: str = ""
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_status": self.proposal_status,
            "status": self.status.value,
            "stage": self.stage.value,
            "progress": self.progress,
            "current_action": self.current_action,
            "next_action": self.next_action,
            "next_action_reason": self.next_action_reason,
            "links": [item.to_dict() for item in self.links],
            "event_refs": [item.to_dict() for item in self.event_refs],
            "last_event_id": self.last_event_id,
            "last_event_at": self.last_event_at,
            "failed_at": self.failed_at,
            "warnings": list(self.warnings),
        }


class MissionProjectionEngine:
    """Project known event facts without invoking any domain operation."""

    def project(
        self,
        mission: Mission,
        events: tuple[OrionEvent, ...],
        *,
        warnings: tuple[str, ...] = (),
    ) -> MissionProjection:
        relevant = self.relevant_events(mission, events)
        links = {link.link_type: link for link in mission.links}
        proposal_status = mission.proposal_status
        status, stage, progress = self._base_state(mission, links)
        current_action = ""
        next_action = ""
        next_reason = "No safe next action is currently observable."
        if status is MissionStatus.AWAITING_APPROVAL:
            task_id = links["team_task"].subject_id
            action = interface_action("team.approve", {"team_task_id": task_id})
            current_action = action.capability_id
            next_action = action.cli_command or ""
            next_reason = "The persisted Team plan requires human approval."
        elif status is MissionStatus.APPROVED:
            task_id = links["team_task"].subject_id
            approval_id = links["approval"].subject_id
            action = interface_action("team.implement", {
                "team_task_id": task_id,
                "approval_id": approval_id,
            })
            current_action = action.capability_id
            next_action = action.cli_command or ""
            next_reason = (
                "The Team plan is approved; implementation remains a separate "
                "explicit operation."
            )
        elif status is MissionStatus.PLANNING:
            if "team_task" in links:
                current_action = "team.show"
                next_reason = (
                    "A persisted Team task is linked, but no authoritative event "
                    "establishes an actionable approval state."
                )
            else:
                current_action = "team.plan"
                next_reason = "Waiting for an authoritative Team plan event."
        elif status is MissionStatus.FAILED:
            next_reason = "The accepted proposal dispatch failed; retries are disabled."
        elif status is MissionStatus.AWAITING_REVIEW:
            next_reason = "Explicit final review completion is not implemented."
        failed_at = mission.failed_at

        for event in relevant:
            if event.event_type == EventTypes.GOAL_PROPOSAL_ACCEPTED:
                if proposal_status not in {"consumed", "failed"}:
                    proposal_status = "accepted"
                if status not in {
                    MissionStatus.FAILED,
                    MissionStatus.AWAITING_APPROVAL,
                    MissionStatus.APPROVED,
                }:
                    status = MissionStatus.PLANNING
                    stage = MissionStage.TEAM_PLANNING
                    progress = max(progress, MISSION_PROGRESS["proposal_accepted"])
                    current_action = "team.plan"
                    next_action = ""
                    next_reason = "Waiting for an authoritative Team plan event."
            elif event.event_type == EventTypes.GOAL_PROPOSAL_CONSUMED:
                if proposal_status != "failed":
                    proposal_status = "consumed"
                if status not in {
                    MissionStatus.FAILED,
                    MissionStatus.AWAITING_APPROVAL,
                    MissionStatus.APPROVED,
                }:
                    status = MissionStatus.PLANNING
                    stage = MissionStage.TEAM_PLANNING
                    progress = max(progress, MISSION_PROGRESS["proposal_consumed"])
                    current_action = "team.plan"
                    next_action = ""
                    next_reason = (
                        "The proposal was consumed, but no later actionable event "
                        "is currently observable."
                    )
            elif event.event_type == EventTypes.TEAM_PLAN_CREATED:
                task_id = self._text(event.data.get("team_task_id"))
                if task_id:
                    links["team_task"] = MissionLink(
                        "team_task",
                        task_id,
                        event.event_type,
                        event.occurred_at,
                    )
                awaiting = (
                    self._text(event.data.get("status")) == "awaiting_approval"
                    or event.data.get("approval_required") is True
                )
                if status not in {MissionStatus.FAILED, MissionStatus.APPROVED}:
                    status = (
                        MissionStatus.AWAITING_APPROVAL
                        if awaiting and task_id else MissionStatus.PLANNING
                    )
                    stage = (
                        MissionStage.APPROVAL
                        if status is MissionStatus.AWAITING_APPROVAL
                        else MissionStage.TEAM_PLANNING
                    )
                    progress = max(
                        progress,
                        MISSION_PROGRESS[
                            "awaiting_approval" if awaiting and task_id
                            else "team_plan_created"
                        ],
                    )
                    if awaiting and task_id:
                        action = interface_action(
                            "team.approve",
                            {"team_task_id": task_id},
                        )
                        current_action = action.capability_id
                        next_action = action.cli_command or ""
                        next_reason = "The persisted Team plan requires human approval."
                    else:
                        current_action = "team.show" if task_id else ""
                        next_action = ""
                        next_reason = "No safe actionable Team command can be derived."
            elif event.event_type == EventTypes.TEAM_PLAN_APPROVED:
                task_id = self._text(event.data.get("team_task_id"))
                approval_id = self._text(event.data.get("approval_id"))
                if (
                    task_id
                    and approval_id
                    and status is not MissionStatus.FAILED
                ):
                    links["approval"] = MissionLink(
                        "approval",
                        approval_id,
                        event.event_type,
                        event.occurred_at,
                    )
                    status = MissionStatus.APPROVED
                    stage = MissionStage.IMPLEMENTATION
                    progress = max(progress, MISSION_PROGRESS["approved"])
                    action = interface_action("team.implement", {
                        "team_task_id": task_id,
                        "approval_id": approval_id,
                    })
                    current_action = action.capability_id
                    next_action = action.cli_command or ""
                    next_reason = (
                        "The Team plan is approved; implementation remains a "
                        "separate explicit operation."
                    )
            elif event.event_type == EventTypes.GOAL_PROPOSAL_FAILED:
                proposal_status = "failed"
                status = MissionStatus.FAILED
                stage = MissionStage.FAILED
                progress = max(progress, MISSION_PROGRESS["proposal_accepted"])
                current_action = ""
                next_action = ""
                next_reason = "The accepted proposal dispatch failed; retries are disabled."
                failed_at = event.occurred_at

        refs = tuple(MissionEventRef(
            event.event_id,
            event.event_type,
            event.occurred_at,
            event.source,
            event.subject_id or "",
        ) for event in relevant)
        return MissionProjection(
            proposal_status=proposal_status,
            status=status,
            stage=stage,
            progress=progress,
            current_action=current_action,
            next_action=next_action,
            next_action_reason=next_reason,
            links=tuple(sorted(links.values(), key=lambda item: item.link_type)),
            event_refs=refs,
            last_event_id=refs[-1].event_id if refs else "",
            last_event_at=refs[-1].occurred_at if refs else "",
            failed_at=failed_at,
            warnings=tuple(dict.fromkeys((*mission.warnings, *warnings))),
        )

    def relevant_events(
        self,
        mission: Mission,
        events: tuple[OrionEvent, ...],
    ) -> tuple[OrionEvent, ...]:
        """Return only strictly correlated, deduplicated events in replay order."""
        unique = {event.event_id: event for event in events}
        ordered = tuple(sorted(
            unique.values(),
            key=lambda event: (
                event.occurred_at,
                _EVENT_ORDER.get(event.event_type, 999),
                event.event_id,
            ),
        ))
        accepted_ids = frozenset(
            event.event_id for event in ordered
            if event.event_type == EventTypes.GOAL_PROPOSAL_ACCEPTED
            and self._proposal_event_belongs(mission, event)
        )
        linked_task = mission.link("team_task")
        team_task_ids = {
            linked_task.subject_id
        } if linked_task is not None else set()
        for event in ordered:
            if event.event_type != EventTypes.TEAM_PLAN_CREATED:
                continue
            task_id = str(event.data.get("team_task_id", "")).strip()
            if (
                event.correlation_id == mission.goal_id
                and task_id
                and LINK_ID_PATTERN.fullmatch(task_id)
                and event.subject_id == task_id
                and (
                    task_id in team_task_ids
                    or event.causation_id in accepted_ids
                )
            ):
                team_task_ids.add(task_id)
        return tuple(
            event for event in ordered
            if self._belongs(
                mission,
                event,
                frozenset(team_task_ids),
            )
        )

    @staticmethod
    def signature(mission: Mission) -> tuple[object, ...]:
        """Return projection-only state for stale/current comparisons."""
        return (
            mission.proposal_status,
            mission.status,
            mission.stage,
            mission.progress,
            mission.current_action,
            mission.next_action,
            mission.next_action_reason,
            mission.links,
            mission.event_refs,
            mission.last_event_id,
            mission.last_event_at,
            mission.failed_at,
            mission.warnings,
        )

    @staticmethod
    def projected_signature(projection: MissionProjection) -> tuple[object, ...]:
        return (
            projection.proposal_status,
            projection.status,
            projection.stage,
            projection.progress,
            projection.current_action,
            projection.next_action,
            projection.next_action_reason,
            projection.links,
            projection.event_refs,
            projection.last_event_id,
            projection.last_event_at,
            projection.failed_at,
            projection.warnings,
        )

    @staticmethod
    def _base_state(
        mission: Mission,
        links: dict[str, MissionLink],
    ) -> tuple[MissionStatus, MissionStage, int]:
        if mission.proposal_status == "failed":
            return MissionStatus.FAILED, MissionStage.FAILED, max(mission.progress, 10)
        if "team_task" in links and "approval" in links:
            return (
                MissionStatus.APPROVED,
                MissionStage.IMPLEMENTATION,
                max(mission.progress, MISSION_PROGRESS["approved"]),
            )
        if mission.proposal_status in {"accepted", "consumed"}:
            return (
                MissionStatus.PLANNING,
                MissionStage.TEAM_PLANNING,
                MISSION_PROGRESS[
                    "proposal_consumed"
                    if mission.proposal_status == "consumed"
                    else "proposal_accepted"
                ],
            )
        return MissionStatus.CREATED, MissionStage.PROPOSAL, MISSION_PROGRESS["created"]

    @staticmethod
    def _belongs(
        mission: Mission,
        event: OrionEvent,
        team_task_ids: frozenset[str],
    ) -> bool:
        if event.event_type in {
            EventTypes.GOAL_PROPOSAL_CREATED,
            EventTypes.GOAL_PROPOSAL_VALIDATED,
            EventTypes.GOAL_PROPOSAL_ACCEPTED,
            EventTypes.GOAL_PROPOSAL_CONSUMED,
            EventTypes.GOAL_PROPOSAL_FAILED,
        }:
            return MissionProjectionEngine._proposal_event_belongs(mission, event)
        if event.event_type == EventTypes.TEAM_PLAN_CREATED:
            data = event.data
            task_id = str(data.get("team_task_id", "")).strip()
            return (
                event.correlation_id == mission.goal_id
                and bool(task_id)
                and event.subject_id == task_id
                and task_id in team_task_ids
            )
        if event.event_type == EventTypes.TEAM_PLAN_APPROVED:
            data = event.data
            task_id = str(data.get("team_task_id", "")).strip()
            approval_id = str(data.get("approval_id", "")).strip()
            plan_sha256 = str(data.get("plan_sha256", "")).strip().lower()
            return (
                task_id in team_task_ids
                and event.subject_id == task_id
                and event.correlation_id in {mission.goal_id, task_id}
                and bool(LINK_ID_PATTERN.fullmatch(approval_id))
                and len(plan_sha256) == 64
                and all(character in "0123456789abcdef" for character in plan_sha256)
                and str(data.get("status", "")).strip() == "approved"
                and data.get("approval_required") is True
                and str(data.get("approval_status", "")).strip() == "approved"
            )
        return False

    @staticmethod
    def _proposal_event_belongs(mission: Mission, event: OrionEvent) -> bool:
        data = event.data
        return (
            event.correlation_id == mission.goal_id
            and event.subject_id == mission.proposal_id
            and str(data.get("goal_id", "")) == mission.goal_id
            and str(data.get("proposal_id", "")) == mission.proposal_id
            and data.get("version") == mission.proposal_version
        )

    @staticmethod
    def _text(value: object) -> str:
        return str(value or "").strip()
