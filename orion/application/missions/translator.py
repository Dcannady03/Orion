"""Explicit allowlisted translation for one Mission coordination operation."""
from __future__ import annotations

from dataclasses import dataclass
import re

from orion.application.commands.ai_team_commands import TeamApprovalRequest
from orion.application.missions.coordination_models import MissionNextOperation
from orion.application.results import ApplicationResult


_PLAN_HASH_PATTERN = re.compile(r"[a-f0-9]{64}")


@dataclass(frozen=True)
class MissionOperationTranslation:
    capability_id: str
    application_request_type: str
    request: object


class MissionOperationTranslator:
    """Translate only team.approve into its existing typed application request."""

    TEAM_APPROVE = "team.approve"
    TEAM_APPROVAL_REQUEST = "TeamApprovalRequest"

    @classmethod
    def supports(cls, capability_id: str) -> bool:
        return str(capability_id).strip() == cls.TEAM_APPROVE

    def translate(
        self,
        operation: MissionNextOperation,
        *,
        actor: str,
        correlation_id: str,
        causation_id: str | None,
    ) -> MissionOperationTranslation:
        if not isinstance(operation, MissionNextOperation):
            raise TypeError("Mission translation requires a typed next operation.")
        if operation.blocked or operation.capability_id != self.TEAM_APPROVE:
            raise MissionOperationTranslationError(
                f"Mission capability translation is not supported: "
                f"{operation.capability_id or 'blocked'}"
            )
        inputs = dict(operation.resolved_inputs)
        if set(inputs) != {"team_task_id", "plan_sha256"}:
            raise MissionOperationTranslationError(
                "team.approve Mission inputs must be team_task_id and plan_sha256."
            )
        task_id = str(inputs["team_task_id"]).strip()
        plan_sha256 = str(inputs["plan_sha256"]).strip().lower()
        if not task_id or task_id != operation.subject_id:
            raise MissionOperationTranslationError(
                "team.approve Mission subject does not match its typed input."
            )
        if not _PLAN_HASH_PATTERN.fullmatch(plan_sha256):
            raise MissionOperationTranslationError(
                "team.approve Mission plan SHA-256 is invalid."
            )
        request = TeamApprovalRequest(
            team_task_id=task_id,
            actor=str(actor).strip() or "user",
            plan_sha256=plan_sha256,
            correlation_id=str(correlation_id).strip() or None,
            causation_id=str(causation_id).strip() if causation_id else None,
        )
        return MissionOperationTranslation(
            capability_id=self.TEAM_APPROVE,
            application_request_type=self.TEAM_APPROVAL_REQUEST,
            request=request,
        )

    def dispatch(
        self,
        translation: MissionOperationTranslation,
        *,
        team_application,
    ) -> ApplicationResult:
        if not isinstance(translation, MissionOperationTranslation):
            raise TypeError("Mission dispatch requires a typed translation.")
        if (
            translation.capability_id != self.TEAM_APPROVE
            or translation.application_request_type != self.TEAM_APPROVAL_REQUEST
            or not isinstance(translation.request, TeamApprovalRequest)
        ):
            raise MissionOperationTranslationError(
                "Translated Mission request is not allowlisted."
            )
        if team_application is None:
            raise MissionOperationTranslationError(
                "AI Team application handler is unavailable."
            )
        result = team_application.approve(translation.request)
        if not isinstance(result, ApplicationResult):
            raise MissionOperationTranslationError(
                "AI Team application handler returned an invalid result."
            )
        return result


class MissionOperationTranslationError(ValueError):
    """Raised when a Mission operation cannot use the explicit allowlist."""
