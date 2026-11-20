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
    "implementation_started": 45,
    "implementation_completed": 60,
    "validation_blocked": 70,
    "validation_completed": 75,
    "documentation_blocked": 85,
    "documentation_completed": 90,
    "final_review": 95,
    "completed": 100,
}

_EVENT_ORDER = {
    EventTypes.GOAL_PROPOSAL_CREATED: 10,
    EventTypes.GOAL_PROPOSAL_VALIDATED: 20,
    EventTypes.GOAL_PROPOSAL_ACCEPTED: 30,
    EventTypes.TEAM_PLAN_CREATED: 40,
    EventTypes.GOAL_PROPOSAL_CONSUMED: 50,
    EventTypes.TEAM_PLAN_APPROVED: 60,
    EventTypes.TEAM_IMPLEMENTATION_STARTED: 70,
    EventTypes.TEAM_IMPLEMENTATION_COMPLETED: 80,
    EventTypes.TEAM_IMPLEMENTATION_FAILED: 85,
    EventTypes.TEAM_VALIDATION_COMPLETED: 90,
    EventTypes.TEAM_DOCUMENTATION_REVIEW_COMPLETED: 100,
    EventTypes.TEAM_FINAL_REVIEW_BLOCKED: 110,
    EventTypes.TEAM_FINAL_REVIEW_COMPLETED: 120,
    EventTypes.GOAL_PROPOSAL_FAILED: 130,
}

_ADVANCED_STATUSES = frozenset({
    MissionStatus.AWAITING_APPROVAL,
    MissionStatus.APPROVED,
    MissionStatus.IMPLEMENTING,
    MissionStatus.AWAITING_VALIDATION,
    MissionStatus.AWAITING_DOCUMENTATION,
    MissionStatus.AWAITING_REVIEW,
    MissionStatus.BLOCKED,
    MissionStatus.COMPLETED,
    MissionStatus.FAILED,
})
_TERMINAL_STATUSES = frozenset({
    MissionStatus.BLOCKED,
    MissionStatus.COMPLETED,
    MissionStatus.FAILED,
})
_VALIDATION_STATUSES = frozenset({
    "passed", "warnings", "failed", "unavailable", "error",
})
_DOCUMENTATION_STATUSES = frozenset({
    "passed", "warnings", "failed", "not_required", "unavailable", "error",
})


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
    completed_at: str = ""
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
            "completed_at": self.completed_at,
            "failed_at": self.failed_at,
            "warnings": list(self.warnings),
        }


class MissionProjectionEngine:
    """Project strictly correlated lifecycle facts without invoking operations."""

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
        current_action, next_action, next_reason = self._state_action(
            status, stage, links
        )
        completed_at = mission.completed_at
        failed_at = mission.failed_at

        for event in relevant:
            if event.event_type == EventTypes.GOAL_PROPOSAL_ACCEPTED:
                if proposal_status not in {"consumed", "failed"}:
                    proposal_status = "accepted"
                if status not in _ADVANCED_STATUSES:
                    status = MissionStatus.PLANNING
                    stage = MissionStage.TEAM_PLANNING
                    progress = max(progress, MISSION_PROGRESS["proposal_accepted"])
                    current_action = "team.plan"
                    next_action = ""
                    next_reason = "Waiting for an authoritative Team plan event."
                continue

            if event.event_type == EventTypes.GOAL_PROPOSAL_CONSUMED:
                if proposal_status != "failed":
                    proposal_status = "consumed"
                if status not in _ADVANCED_STATUSES:
                    status = MissionStatus.PLANNING
                    stage = MissionStage.TEAM_PLANNING
                    progress = max(progress, MISSION_PROGRESS["proposal_consumed"])
                    current_action = "team.plan"
                    next_action = ""
                    next_reason = (
                        "The proposal was consumed, but no later actionable event "
                        "is currently observable."
                    )
                continue

            if event.event_type == EventTypes.TEAM_PLAN_CREATED:
                task_id = self._text(event.data.get("team_task_id"))
                if task_id:
                    links["team_task"] = MissionLink(
                        "team_task", task_id, event.event_type, event.occurred_at
                    )
                awaiting = (
                    self._text(event.data.get("status")) == "awaiting_approval"
                    or event.data.get("approval_required") is True
                )
                if status not in _TERMINAL_STATUSES and status not in {
                    MissionStatus.APPROVED,
                    MissionStatus.IMPLEMENTING,
                    MissionStatus.AWAITING_VALIDATION,
                    MissionStatus.AWAITING_DOCUMENTATION,
                    MissionStatus.AWAITING_REVIEW,
                }:
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
                            "awaiting_approval"
                            if awaiting and task_id else "team_plan_created"
                        ],
                    )
                    current_action, next_action, next_reason = self._state_action(
                        status, stage, links
                    )
                continue

            if event.event_type == EventTypes.TEAM_PLAN_APPROVED:
                if status in _TERMINAL_STATUSES:
                    continue
                task_id = self._text(event.data.get("team_task_id"))
                approval_id = self._text(event.data.get("approval_id"))
                links["approval"] = MissionLink(
                    "approval", approval_id, event.event_type, event.occurred_at
                )
                status = MissionStatus.APPROVED
                stage = MissionStage.IMPLEMENTATION
                progress = max(progress, MISSION_PROGRESS["approved"])
                current_action, next_action, next_reason = self._state_action(
                    status, stage, links
                )
                continue

            if event.event_type in {
                EventTypes.TEAM_IMPLEMENTATION_STARTED,
                EventTypes.TEAM_IMPLEMENTATION_COMPLETED,
                EventTypes.TEAM_IMPLEMENTATION_FAILED,
            }:
                if status in _TERMINAL_STATUSES:
                    continue
                if (
                    event.event_type == EventTypes.TEAM_IMPLEMENTATION_STARTED
                    and status is not MissionStatus.APPROVED
                ):
                    continue
                if (
                    event.event_type
                    in {
                        EventTypes.TEAM_IMPLEMENTATION_COMPLETED,
                        EventTypes.TEAM_IMPLEMENTATION_FAILED,
                    }
                    and status not in {
                        MissionStatus.APPROVED,
                        MissionStatus.IMPLEMENTING,
                    }
                ):
                    continue
                run_id = self._text(event.data.get("run_id"))
                links["team_run"] = MissionLink(
                    "team_run", run_id, event.event_type, event.occurred_at
                )
                if event.event_type == EventTypes.TEAM_IMPLEMENTATION_STARTED:
                    status = MissionStatus.IMPLEMENTING
                    stage = MissionStage.IMPLEMENTATION
                    progress = max(
                        progress, MISSION_PROGRESS["implementation_started"]
                    )
                elif event.event_type == EventTypes.TEAM_IMPLEMENTATION_COMPLETED:
                    status = MissionStatus.AWAITING_VALIDATION
                    stage = MissionStage.VALIDATION
                    progress = max(
                        progress, MISSION_PROGRESS["implementation_completed"]
                    )
                else:
                    status = MissionStatus.FAILED
                    stage = MissionStage.FAILED
                    progress = max(
                        progress, MISSION_PROGRESS["implementation_started"]
                    )
                    failed_at = event.occurred_at
                current_action, next_action, next_reason = self._state_action(
                    status, stage, links
                )
                continue

            if event.event_type == EventTypes.TEAM_VALIDATION_COMPLETED:
                if status is not MissionStatus.AWAITING_VALIDATION:
                    continue
                validation_id = self._text(event.data.get("validation_id"))
                validation_status = self._text(
                    event.data.get("validation_status")
                ).lower()
                links["validation"] = MissionLink(
                    "validation",
                    validation_id,
                    event.event_type,
                    event.occurred_at,
                )
                if validation_status in {"passed", "warnings"}:
                    status = MissionStatus.AWAITING_DOCUMENTATION
                    stage = MissionStage.DOCUMENTATION_REVIEW
                    progress = max(
                        progress, MISSION_PROGRESS["validation_completed"]
                    )
                else:
                    status = MissionStatus.BLOCKED
                    stage = MissionStage.VALIDATION
                    progress = max(progress, MISSION_PROGRESS["validation_blocked"])
                current_action, next_action, next_reason = self._state_action(
                    status, stage, links
                )
                continue

            if event.event_type == EventTypes.TEAM_DOCUMENTATION_REVIEW_COMPLETED:
                if status is not MissionStatus.AWAITING_DOCUMENTATION:
                    continue
                documentation_id = self._text(
                    event.data.get("documentation_review_id")
                )
                documentation_status = self._text(
                    event.data.get("documentation_review_status")
                ).lower()
                links["documentation_review"] = MissionLink(
                    "documentation_review",
                    documentation_id,
                    event.event_type,
                    event.occurred_at,
                )
                if documentation_status in {"passed", "warnings", "not_required"}:
                    status = MissionStatus.AWAITING_REVIEW
                    stage = MissionStage.FINAL_REVIEW
                    progress = max(
                        progress, MISSION_PROGRESS["documentation_completed"]
                    )
                else:
                    status = MissionStatus.BLOCKED
                    stage = MissionStage.DOCUMENTATION_REVIEW
                    progress = max(
                        progress, MISSION_PROGRESS["documentation_blocked"]
                    )
                current_action, next_action, next_reason = self._state_action(
                    status, stage, links
                )
                continue

            if event.event_type == EventTypes.TEAM_FINAL_REVIEW_BLOCKED:
                if status is not MissionStatus.AWAITING_REVIEW:
                    continue
                status = MissionStatus.BLOCKED
                stage = MissionStage.FINAL_REVIEW
                progress = max(progress, MISSION_PROGRESS["final_review"])
                current_action, next_action, next_reason = self._state_action(
                    status, stage, links
                )
                continue

            if event.event_type == EventTypes.TEAM_FINAL_REVIEW_COMPLETED:
                if status is not MissionStatus.AWAITING_REVIEW:
                    continue
                status = MissionStatus.COMPLETED
                stage = MissionStage.COMPLETED
                progress = MISSION_PROGRESS["completed"]
                completed_at = event.occurred_at
                current_action, next_action, next_reason = self._state_action(
                    status, stage, links
                )
                continue

            if event.event_type == EventTypes.GOAL_PROPOSAL_FAILED:
                proposal_status = "failed"
                status = MissionStatus.FAILED
                stage = MissionStage.FAILED
                progress = max(progress, MISSION_PROGRESS["proposal_accepted"])
                current_action = ""
                next_action = ""
                next_reason = (
                    "The accepted proposal dispatch failed; retries are disabled."
                )
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
            completed_at=completed_at,
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
        task_ids = {linked_task.subject_id} if linked_task is not None else set()
        for event in ordered:
            if event.event_type != EventTypes.TEAM_PLAN_CREATED:
                continue
            task_id = self._text(event.data.get("team_task_id"))
            if (
                event.source == "ai_team"
                and event.correlation_id == mission.goal_id
                and task_id
                and LINK_ID_PATTERN.fullmatch(task_id)
                and event.subject_id == task_id
                and (task_id in task_ids or event.causation_id in accepted_ids)
            ):
                task_ids.add(task_id)

        linked_approval = mission.link("approval")
        approval_ids = (
            {linked_approval.subject_id} if linked_approval is not None else set()
        )
        for event in ordered:
            if self._approval_event_belongs(mission, event, frozenset(task_ids)):
                approval_ids.add(self._text(event.data.get("approval_id")))

        linked_run = mission.link("team_run")
        run_ids = {linked_run.subject_id} if linked_run is not None else set()
        for event in ordered:
            if self._implementation_event_belongs(
                mission,
                event,
                frozenset(task_ids),
                frozenset(approval_ids),
            ):
                run_ids.add(self._text(event.data.get("run_id")))

        linked_validation = mission.link("validation")
        validation_ids = (
            {linked_validation.subject_id} if linked_validation is not None else set()
        )
        for event in ordered:
            if self._validation_event_belongs(
                mission,
                event,
                frozenset(task_ids),
                frozenset(approval_ids),
                frozenset(run_ids),
            ):
                validation_ids.add(self._text(event.data.get("validation_id")))

        linked_documentation = mission.link("documentation_review")
        documentation_ids = (
            {linked_documentation.subject_id}
            if linked_documentation is not None else set()
        )
        for event in ordered:
            if self._documentation_event_belongs(
                mission,
                event,
                frozenset(task_ids),
                frozenset(approval_ids),
                frozenset(run_ids),
            ):
                documentation_ids.add(
                    self._text(event.data.get("documentation_review_id"))
                )

        return tuple(
            event for event in ordered
            if self._belongs(
                mission,
                event,
                frozenset(task_ids),
                frozenset(approval_ids),
                frozenset(run_ids),
                frozenset(validation_ids),
                frozenset(documentation_ids),
            )
        )

    @staticmethod
    def signature(mission: Mission) -> tuple[object, ...]:
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
            mission.completed_at,
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
            projection.completed_at,
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
                max(MISSION_PROGRESS["approved"], min(mission.progress, 35)),
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
    def _state_action(
        status: MissionStatus,
        stage: MissionStage,
        links: dict[str, MissionLink],
    ) -> tuple[str, str, str]:
        if status is MissionStatus.AWAITING_APPROVAL and "team_task" in links:
            task_id = links["team_task"].subject_id
            action = interface_action("team.approve", {"team_task_id": task_id})
            return (
                action.capability_id,
                action.cli_command or "",
                "The persisted Team plan requires human approval.",
            )
        if status is MissionStatus.APPROVED and {
            "team_task", "approval",
        }.issubset(links):
            action = interface_action("team.implement", {
                "team_task_id": links["team_task"].subject_id,
                "approval_id": links["approval"].subject_id,
            })
            return (
                action.capability_id,
                action.cli_command or "",
                "The Team plan is approved; implementation is a separate operation.",
            )
        if status is MissionStatus.IMPLEMENTING and "team_run" in links:
            action = interface_action(
                "team.show", {"run_id": links["team_run"].subject_id}
            )
            return (
                action.capability_id,
                action.cli_command or "",
                "The Team implementation is recorded as executing.",
            )
        if status is MissionStatus.AWAITING_VALIDATION and "team_run" in links:
            action = interface_action(
                "team.validate", {"run_id": links["team_run"].subject_id}
            )
            return (
                action.capability_id,
                action.cli_command or "",
                "Implementation completed; validation is the next explicit operation.",
            )
        if status is MissionStatus.AWAITING_DOCUMENTATION and "team_run" in links:
            action = interface_action(
                "team.documentation_review",
                {"run_id": links["team_run"].subject_id},
            )
            return (
                action.capability_id,
                action.cli_command or "",
                "Validation completed; Documentation Review is the next operation.",
            )
        if status is MissionStatus.AWAITING_REVIEW:
            return (
                "",
                "",
                "Final review requires a human decision, but no typed Team completion "
                "operation exists yet.",
            )
        if status is MissionStatus.BLOCKED:
            return (
                "",
                "",
                f"The Team lifecycle is blocked during {stage.value}; automatic retry "
                "is disabled.",
            )
        if status is MissionStatus.COMPLETED:
            return "", "", "The authoritative final review completed the Mission."
        if status is MissionStatus.FAILED:
            return "", "", "The lifecycle failed; automatic retry is disabled."
        if status is MissionStatus.PLANNING:
            if "team_task" in links:
                return (
                    "team.show",
                    "",
                    "A Team task is linked, but no actionable state is observable.",
                )
            return "team.plan", "", "Waiting for an authoritative Team plan event."
        return "", "", "No safe next action is currently observable."

    def _belongs(
        self,
        mission: Mission,
        event: OrionEvent,
        task_ids: frozenset[str],
        approval_ids: frozenset[str],
        run_ids: frozenset[str],
        validation_ids: frozenset[str],
        documentation_ids: frozenset[str],
    ) -> bool:
        if event.event_type in {
            EventTypes.GOAL_PROPOSAL_CREATED,
            EventTypes.GOAL_PROPOSAL_VALIDATED,
            EventTypes.GOAL_PROPOSAL_ACCEPTED,
            EventTypes.GOAL_PROPOSAL_CONSUMED,
            EventTypes.GOAL_PROPOSAL_FAILED,
        }:
            return self._proposal_event_belongs(mission, event)
        if event.event_type == EventTypes.TEAM_PLAN_CREATED:
            task_id = self._text(event.data.get("team_task_id"))
            return (
                event.source == "ai_team"
                and event.correlation_id == mission.goal_id
                and task_id in task_ids
                and event.subject_id == task_id
            )
        if event.event_type == EventTypes.TEAM_PLAN_APPROVED:
            return self._approval_event_belongs(mission, event, task_ids)
        if event.event_type in {
            EventTypes.TEAM_IMPLEMENTATION_STARTED,
            EventTypes.TEAM_IMPLEMENTATION_COMPLETED,
            EventTypes.TEAM_IMPLEMENTATION_FAILED,
        }:
            return self._implementation_event_belongs(
                mission, event, task_ids, approval_ids
            )
        if event.event_type == EventTypes.TEAM_VALIDATION_COMPLETED:
            return self._validation_event_belongs(
                mission, event, task_ids, approval_ids, run_ids
            )
        if event.event_type == EventTypes.TEAM_DOCUMENTATION_REVIEW_COMPLETED:
            return self._documentation_event_belongs(
                mission, event, task_ids, approval_ids, run_ids
            )
        if event.event_type in {
            EventTypes.TEAM_FINAL_REVIEW_COMPLETED,
            EventTypes.TEAM_FINAL_REVIEW_BLOCKED,
        }:
            data = event.data
            run_id = self._text(data.get("run_id"))
            task_id = self._text(data.get("team_task_id"))
            approval_id = self._text(data.get("approval_id"))
            validation_id = self._text(data.get("validation_id"))
            documentation_id = self._text(data.get("documentation_review_id"))
            status = self._text(data.get("status"))
            decision = self._text(data.get("decision"))
            terminal_valid = (
                status == "completed" and decision == "accepted"
                if event.event_type == EventTypes.TEAM_FINAL_REVIEW_COMPLETED
                else status == "blocked"
                and decision in {"changes_requested", "rejected"}
            )
            return (
                self._run_identity_belongs(
                    mission, event, task_ids, approval_ids, run_ids
                )
                and run_id == event.subject_id
                and task_id in task_ids
                and approval_id in approval_ids
                and validation_id in validation_ids
                and documentation_id in documentation_ids
                and terminal_valid
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

    def _approval_event_belongs(
        self,
        mission: Mission,
        event: OrionEvent,
        task_ids: frozenset[str],
    ) -> bool:
        if event.event_type != EventTypes.TEAM_PLAN_APPROVED:
            return False
        data = event.data
        task_id = self._text(data.get("team_task_id"))
        approval_id = self._text(data.get("approval_id"))
        return (
            event.source == "ai_team"
            and task_id in task_ids
            and event.subject_id == task_id
            and event.correlation_id in {mission.goal_id, task_id}
            and bool(LINK_ID_PATTERN.fullmatch(approval_id))
            and self._valid_plan_hash(data.get("plan_sha256"))
            and self._text(data.get("status")) == "approved"
            and data.get("approval_required") is True
            and self._text(data.get("approval_status")) == "approved"
        )

    def _implementation_event_belongs(
        self,
        mission: Mission,
        event: OrionEvent,
        task_ids: frozenset[str],
        approval_ids: frozenset[str],
    ) -> bool:
        if event.event_type not in {
            EventTypes.TEAM_IMPLEMENTATION_STARTED,
            EventTypes.TEAM_IMPLEMENTATION_COMPLETED,
            EventTypes.TEAM_IMPLEMENTATION_FAILED,
        }:
            return False
        if not self._run_identity_belongs(
            mission, event, task_ids, approval_ids, frozenset()
        ):
            return False
        data = event.data
        status = self._text(data.get("status"))
        stage = self._text(data.get("stage"))
        implementation_status = self._text(data.get("implementation_status"))
        if event.event_type == EventTypes.TEAM_IMPLEMENTATION_STARTED:
            return (
                status == "executing"
                and stage == "implementation"
                and implementation_status == "executing"
                and bool(self._text(data.get("started_at")))
            )
        if event.event_type == EventTypes.TEAM_IMPLEMENTATION_COMPLETED:
            return (
                status == "awaiting_review"
                and stage == "validation"
                and implementation_status == "complete"
                and bool(self._text(data.get("started_at")))
                and bool(self._text(data.get("completed_at")))
            )
        return (
            status == "failed"
            and stage == "implementation"
            and implementation_status == "failed"
            and bool(self._text(data.get("error_category")))
            and bool(self._text(data.get("completed_at")))
        )

    def _validation_event_belongs(
        self,
        mission: Mission,
        event: OrionEvent,
        task_ids: frozenset[str],
        approval_ids: frozenset[str],
        run_ids: frozenset[str],
    ) -> bool:
        if event.event_type != EventTypes.TEAM_VALIDATION_COMPLETED:
            return False
        data = event.data
        validation_id = self._text(data.get("validation_id"))
        return (
            self._run_identity_belongs(
                mission, event, task_ids, approval_ids, run_ids
            )
            and bool(LINK_ID_PATTERN.fullmatch(validation_id))
            and self._text(data.get("validation_status")) in _VALIDATION_STATUSES
            and self._text(data.get("status")) == "awaiting_review"
            and self._text(data.get("stage")) == "documentation_review"
            and bool(self._text(data.get("completed_at")))
        )

    def _documentation_event_belongs(
        self,
        mission: Mission,
        event: OrionEvent,
        task_ids: frozenset[str],
        approval_ids: frozenset[str],
        run_ids: frozenset[str],
    ) -> bool:
        if event.event_type != EventTypes.TEAM_DOCUMENTATION_REVIEW_COMPLETED:
            return False
        data = event.data
        documentation_id = self._text(data.get("documentation_review_id"))
        return (
            self._run_identity_belongs(
                mission, event, task_ids, approval_ids, run_ids
            )
            and bool(LINK_ID_PATTERN.fullmatch(documentation_id))
            and self._text(data.get("documentation_review_status"))
            in _DOCUMENTATION_STATUSES
            and self._text(data.get("status")) == "awaiting_review"
            and self._text(data.get("stage")) == "final_review"
            and bool(self._text(data.get("completed_at")))
        )

    def _run_identity_belongs(
        self,
        mission: Mission,
        event: OrionEvent,
        task_ids: frozenset[str],
        approval_ids: frozenset[str],
        run_ids: frozenset[str],
    ) -> bool:
        data = event.data
        task_id = self._text(data.get("team_task_id"))
        approval_id = self._text(data.get("approval_id"))
        run_id = self._text(data.get("run_id"))
        return (
            event.source == "ai_team"
            and task_id in task_ids
            and approval_id in approval_ids
            and bool(LINK_ID_PATTERN.fullmatch(run_id))
            and (not run_ids or run_id in run_ids)
            and event.subject_id == run_id
            and event.correlation_id in {mission.goal_id, task_id, run_id}
            and self._valid_plan_hash(data.get("plan_sha256"))
        )

    @staticmethod
    def _valid_plan_hash(value: object) -> bool:
        text = str(value or "").strip().lower()
        return len(text) == 64 and all(
            character in "0123456789abcdef" for character in text
        )

    @staticmethod
    def _text(value: object) -> str:
        return str(value or "").strip()
