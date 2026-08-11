"""Application boundary for observation-only Mission operations."""
from __future__ import annotations

from dataclasses import dataclass

from orion.application.interface_actions import interface_action
from orion.application.missions.models import Mission
from orion.application.missions.service import MissionService
from orion.application.results import ApplicationResult


@dataclass(frozen=True)
class MissionReferenceRequest:
    mission_id: str


@dataclass(frozen=True)
class MissionListRequest:
    status: str = ""
    goal_id: str = ""
    proposal_id: str = ""
    limit: int = 100


@dataclass(frozen=True)
class MissionHistoryRequest:
    mission_id: str
    limit: int = 100


class MissionApplicationHandler:
    """Return structured Mission results without printing or executing domains."""

    def __init__(self, service: MissionService) -> None:
        self.service = service

    def create(self, proposal_id: str) -> ApplicationResult:
        try:
            creation = self.service.create(self._required(proposal_id, "Goal Proposal ID"))
        except self._expected_errors() as exc:
            return self._failure("Mission creation failed", exc)
        mission = creation.mission
        heading = "Mission Created" if creation.created else "Existing Mission"
        return ApplicationResult.success(
            self._format_mission(mission, heading=heading),
            data={
                "command": "create",
                "created": creation.created,
                "reconciled": creation.reconciled,
                **self._mission_data(mission),
            },
            warnings=mission.warnings,
            next_actions=self._next_actions(mission),
        )

    def show(self, request: MissionReferenceRequest) -> ApplicationResult:
        try:
            mission = self.service.get(self._reference(request))
        except self._expected_errors() as exc:
            return self._failure("Mission could not be shown", exc)
        return ApplicationResult.success(
            self._format_mission(mission),
            data={"command": "show", **self._mission_data(mission)},
            warnings=mission.warnings,
            next_actions=self._next_actions(mission),
        )

    def list(self, request: MissionListRequest) -> ApplicationResult:
        if not isinstance(request, MissionListRequest):
            return ApplicationResult.failure(
                "Mission list requires a structured request.",
                errors=("Mission list request is invalid.",),
            )
        try:
            missions = self.service.list(
                status=request.status or None,
                goal_id=request.goal_id,
                proposal_id=request.proposal_id,
                limit=request.limit,
            )
        except self._expected_errors() as exc:
            return self._failure("Missions could not be listed", exc)
        lines = ["Missions", "-" * 88]
        if missions:
            lines.extend(
                f"{item.mission_id} | {item.status.value:<18} | "
                f"{item.progress:>3}% | {item.title[:42]}"
                for item in missions
            )
        else:
            lines.append("No matching Missions.")
        return ApplicationResult.success(
            "\n".join(lines),
            data={
                "command": "list",
                "missions": [self._mission_data(item) for item in missions],
                "count": len(missions),
                "filters": {
                    "status": request.status,
                    "goal_id": request.goal_id,
                    "proposal_id": request.proposal_id,
                },
            },
        )

    def history(self, request: MissionHistoryRequest) -> ApplicationResult:
        if not isinstance(request, MissionHistoryRequest):
            return ApplicationResult.failure(
                "Mission history requires a structured request.",
                errors=("Mission history request is invalid.",),
            )
        try:
            mission_id = self._required(request.mission_id, "Mission ID")
            events, warnings = self.service.history(mission_id, limit=request.limit)
        except self._expected_errors() as exc:
            return self._failure("Mission history is unavailable", exc)
        lines = [f"Mission History: {mission_id}", "-" * 88]
        if events:
            lines.extend(
                f"{event.occurred_at} | {event.event_type} | {event.event_id}"
                for event in events
            )
        else:
            lines.append("No correlated events are available.")
        return ApplicationResult.success(
            "\n".join(lines),
            data={
                "command": "history",
                "mission_id": mission_id,
                "events": [event.to_dict() for event in events],
                "count": len(events),
                "limit": request.limit,
                "ordering": "newest_first",
                "read_only": True,
            },
            warnings=warnings,
            next_actions=(f"mission show {mission_id}",),
        )

    def validate(self, request: MissionReferenceRequest) -> ApplicationResult:
        try:
            validation = self.service.validate(self._reference(request))
        except self._expected_errors() as exc:
            return self._failure("Mission validation failed", exc)
        lines = [
            "Mission Validation",
            "-" * 72,
            f"Mission    : {validation.mission_id}",
            f"Valid      : {'Yes' if validation.valid else 'No'}",
            f"Schema     : {'valid' if validation.schema_valid else 'INVALID'}",
            f"Proposal   : {'valid' if validation.proposal_identity_valid else 'INVALID'}",
            f"Goal       : {'valid' if validation.goal_identity_valid else 'INVALID'}",
            f"Path       : {'safe' if validation.path_safe else 'UNSAFE'}",
            f"Projection : {'current' if validation.projection_matches else 'STALE'}",
            "No domain state was changed.",
        ]
        data = {"command": "validate", "validation": validation.to_dict()}
        next_actions = (
            f"mission show {validation.mission_id}"
            if validation.valid
            else f"mission reconcile {validation.mission_id}",
        )
        if validation.valid:
            return ApplicationResult.success(
                "\n".join(lines),
                data=data,
                warnings=validation.warnings,
                next_actions=next_actions,
            )
        return ApplicationResult.failure(
            "\n".join(lines),
            data=data,
            warnings=validation.warnings,
            errors=validation.errors,
            next_actions=next_actions,
        )

    def reconcile(self, request: MissionReferenceRequest) -> ApplicationResult:
        try:
            reconciliation = self.service.reconcile(self._reference(request))
        except self._expected_errors() as exc:
            return self._failure("Mission reconciliation failed", exc)
        mission = reconciliation.mission
        heading = "Mission Reconciled" if reconciliation.changed else "Mission Already Current"
        return ApplicationResult.success(
            self._format_mission(mission, heading=heading),
            data={
                "command": "reconcile",
                "changed": reconciliation.changed,
                "observed_event_count": len(reconciliation.events),
                "mission_only_write": reconciliation.changed,
                **self._mission_data(mission),
            },
            warnings=mission.warnings,
            next_actions=self._next_actions(mission),
        )

    @staticmethod
    def _mission_data(mission: Mission) -> dict[str, object]:
        data = mission.to_dict()
        for link_type, field in (
            ("team_task", "team_task_id"),
            ("team_run", "team_run_id"),
            ("command_center_job", "command_center_job_id"),
            ("approval", "approval_id"),
            ("validation", "validation_id"),
            ("documentation_review", "documentation_review_id"),
        ):
            link = mission.link(link_type)
            if link is not None:
                data[field] = link.subject_id
        data["goal"] = mission.goal_text
        data["event_count"] = len(mission.event_refs)
        return data

    @staticmethod
    def _next_actions(mission: Mission) -> tuple[str, ...]:
        commands: list[str] = []
        if mission.next_action:
            commands.append(mission.next_action)
        task = mission.link("team_task")
        if task is not None:
            status = interface_action(
                "team.show",
                {"team_task_id": task.subject_id},
            ).cli_command
            if status:
                commands.append(status)
        commands.append(f"mission reconcile {mission.mission_id}")
        return tuple(dict.fromkeys(commands))

    @classmethod
    def _format_mission(cls, mission: Mission, *, heading: str = "Mission") -> str:
        task = mission.link("team_task")
        lines = [
            heading,
            "-" * 72,
            f"Mission ID : {mission.mission_id}",
            f"Goal       : {mission.goal_text}",
            f"Status     : {mission.status.value.replace('_', ' ').title()}",
            f"Stage      : {mission.stage.value}",
            f"Progress   : {mission.progress}%",
            "",
            "Proposal",
            f"  Status   : {mission.proposal_status}",
            f"  ID       : {mission.proposal_id}",
        ]
        if task is not None:
            lines.extend(["", "AI Team", f"  Task     : {task.subject_id}"])
        if mission.next_action:
            lines.extend(["", "Next:", f"  {mission.next_action}"])
        else:
            lines.extend(["", "Next:", f"  None — {mission.next_action_reason}"])
        lines.append("Observation only; no capability was executed.")
        return "\n".join(lines)

    @staticmethod
    def _reference(request: MissionReferenceRequest) -> str:
        if not isinstance(request, MissionReferenceRequest):
            raise TypeError("Mission reference request is required.")
        return MissionApplicationHandler._required(request.mission_id, "Mission ID")

    @staticmethod
    def _required(value: object, label: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{label} is required.")
        return text

    @staticmethod
    def _failure(prefix: str, exc: BaseException) -> ApplicationResult:
        return ApplicationResult.failure(
            f"{prefix}: {exc}",
            data={"error_type": type(exc).__name__},
            errors=(str(exc),),
        )

    @staticmethod
    def _expected_errors():
        return (
            FileExistsError,
            FileNotFoundError,
            NotADirectoryError,
            OSError,
            PermissionError,
            RuntimeError,
            TimeoutError,
            TypeError,
            ValueError,
        )
