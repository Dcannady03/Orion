"""Explicit allowlisted translation for one Mission coordination operation."""
from __future__ import annotations

from dataclasses import dataclass
import re

from orion.application.commands.ai_team_commands import (
    TeamApprovalRequest,
    TeamImplementationRequest,
    TeamRunRequest,
)
from orion.application.missions.coordination_models import MissionNextOperation
from orion.application.results import ApplicationResult


_PLAN_HASH_PATTERN = re.compile(r"[a-f0-9]{64}")


@dataclass(frozen=True)
class MissionOperationTranslation:
    capability_id: str
    application_request_type: str
    request: object


class MissionOperationTranslator:
    """Translate only the reviewed Team lifecycle operation allowlist."""

    TEAM_APPROVE = "team.approve"
    TEAM_IMPLEMENT = "team.implement"
    TEAM_VALIDATE = "team.validate"
    TEAM_DOCUMENTATION_REVIEW = "team.documentation_review"
    TEAM_APPROVAL_REQUEST = "TeamApprovalRequest"
    TEAM_IMPLEMENTATION_REQUEST = "TeamImplementationRequest"
    TEAM_RUN_REQUEST = "TeamRunRequest"
    SUPPORTED = frozenset({
        TEAM_APPROVE,
        TEAM_IMPLEMENT,
        TEAM_VALIDATE,
        TEAM_DOCUMENTATION_REVIEW,
    })

    @classmethod
    def supports(cls, capability_id: str) -> bool:
        return str(capability_id).strip() in cls.SUPPORTED

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
        capability = operation.capability_id
        if operation.blocked or capability not in self.SUPPORTED:
            raise MissionOperationTranslationError(
                "Mission capability translation is not supported: "
                f"{capability or 'blocked'}"
            )
        correlation = str(correlation_id).strip() or None
        causation = str(causation_id).strip() if causation_id else None
        inputs = dict(operation.resolved_inputs)

        if capability == self.TEAM_APPROVE:
            if set(inputs) != {"team_task_id", "plan_sha256"}:
                raise MissionOperationTranslationError(
                    "team.approve Mission inputs must be team_task_id and plan_sha256."
                )
            task_id = self._subject_input(
                operation, inputs, "team_task_id", capability
            )
            plan_sha256 = self._plan_hash(inputs)
            request = TeamApprovalRequest(
                team_task_id=task_id,
                actor=str(actor).strip() or "user",
                plan_sha256=plan_sha256,
                correlation_id=correlation,
                causation_id=causation,
            )
            return MissionOperationTranslation(
                capability, self.TEAM_APPROVAL_REQUEST, request
            )

        if capability == self.TEAM_IMPLEMENT:
            if set(inputs) != {"team_task_id", "approval_id", "plan_sha256"}:
                raise MissionOperationTranslationError(
                    "team.implement Mission inputs must be team_task_id, "
                    "approval_id, and plan_sha256."
                )
            task_id = self._subject_input(
                operation, inputs, "team_task_id", capability
            )
            approval_id = self._required(inputs, "approval_id", capability)
            self._plan_hash(inputs)
            request = TeamImplementationRequest(
                team_task_id=task_id,
                approval_id=approval_id,
                run_followups=False,
                correlation_id=correlation,
                causation_id=causation,
            )
            return MissionOperationTranslation(
                capability, self.TEAM_IMPLEMENTATION_REQUEST, request
            )

        if capability == self.TEAM_VALIDATE:
            expected = {
                "run_id", "team_task_id", "approval_id", "plan_sha256",
                "implementation_completed_at",
            }
            if set(inputs) != expected:
                raise MissionOperationTranslationError(
                    "team.validate Mission inputs do not match the reviewed schema."
                )
            run_id = self._subject_input(operation, inputs, "run_id", capability)
            self._required(inputs, "team_task_id", capability)
            self._required(inputs, "approval_id", capability)
            self._plan_hash(inputs)
            self._required(inputs, "implementation_completed_at", capability)
            request = TeamRunRequest(
                run_id=run_id,
                run_followups=False,
                correlation_id=correlation,
                causation_id=causation,
            )
            return MissionOperationTranslation(
                capability, self.TEAM_RUN_REQUEST, request
            )

        expected = {
            "run_id", "team_task_id", "approval_id", "plan_sha256",
            "implementation_completed_at", "validation_id",
            "validation_status", "validation_completed_at",
        }
        if set(inputs) != expected:
            raise MissionOperationTranslationError(
                "team.documentation_review Mission inputs do not match the reviewed schema."
            )
        run_id = self._subject_input(operation, inputs, "run_id", capability)
        self._required(inputs, "team_task_id", capability)
        self._required(inputs, "approval_id", capability)
        self._plan_hash(inputs)
        self._required(inputs, "implementation_completed_at", capability)
        self._required(inputs, "validation_id", capability)
        validation_status = self._required(
            inputs, "validation_status", capability
        )
        if validation_status not in {"passed", "warnings"}:
            raise MissionOperationTranslationError(
                "Documentation Review requires a passed or warning validation fact."
            )
        self._required(inputs, "validation_completed_at", capability)
        request = TeamRunRequest(
            run_id=run_id,
            run_followups=False,
            correlation_id=correlation,
            causation_id=causation,
        )
        return MissionOperationTranslation(
            capability, self.TEAM_RUN_REQUEST, request
        )

    def dispatch(
        self,
        translation: MissionOperationTranslation,
        *,
        team_application,
    ) -> ApplicationResult:
        if not isinstance(translation, MissionOperationTranslation):
            raise TypeError("Mission dispatch requires a typed translation.")
        if team_application is None:
            raise MissionOperationTranslationError(
                "AI Team application handler is unavailable."
            )
        capability = translation.capability_id
        request_type = translation.application_request_type
        request = translation.request
        if (
            capability == self.TEAM_APPROVE
            and request_type == self.TEAM_APPROVAL_REQUEST
            and isinstance(request, TeamApprovalRequest)
        ):
            result = team_application.approve(request)
        elif (
            capability == self.TEAM_IMPLEMENT
            and request_type == self.TEAM_IMPLEMENTATION_REQUEST
            and isinstance(request, TeamImplementationRequest)
        ):
            result = team_application.implement(request)
        elif (
            capability == self.TEAM_VALIDATE
            and request_type == self.TEAM_RUN_REQUEST
            and isinstance(request, TeamRunRequest)
        ):
            result = team_application.validate(request)
        elif (
            capability == self.TEAM_DOCUMENTATION_REVIEW
            and request_type == self.TEAM_RUN_REQUEST
            and isinstance(request, TeamRunRequest)
        ):
            result = team_application.documentation_review(request)
        else:
            raise MissionOperationTranslationError(
                "Translated Mission request is not allowlisted."
            )
        if not isinstance(result, ApplicationResult):
            raise MissionOperationTranslationError(
                "AI Team application handler returned an invalid result."
            )
        return result

    @staticmethod
    def _required(
        inputs: dict[str, object], name: str, capability: str
    ) -> str:
        value = str(inputs.get(name, "")).strip()
        if not value:
            raise MissionOperationTranslationError(
                f"{capability} Mission input is required: {name}."
            )
        return value

    @classmethod
    def _subject_input(
        cls,
        operation: MissionNextOperation,
        inputs: dict[str, object],
        name: str,
        capability: str,
    ) -> str:
        value = cls._required(inputs, name, capability)
        if value != operation.subject_id:
            raise MissionOperationTranslationError(
                f"{capability} Mission subject does not match its typed input."
            )
        return value

    @staticmethod
    def _plan_hash(inputs: dict[str, object]) -> str:
        plan_sha256 = str(inputs.get("plan_sha256", "")).strip().lower()
        if not _PLAN_HASH_PATTERN.fullmatch(plan_sha256):
            raise MissionOperationTranslationError(
                "Mission Team plan SHA-256 is invalid."
            )
        return plan_sha256


class MissionOperationTranslationError(ValueError):
    """Raised when a Mission operation cannot use the explicit allowlist."""
