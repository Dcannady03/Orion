"""Application handler for Goal Proposal lifecycle operations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from orion.application.events import (
    EventPublication,
    EventPublisher,
    EventTypes,
)
from orion.application.goals.engine import GoalEngine, GoalPlanningError
from orion.application.goals.models import GoalRequest
from orion.application.goals.proposals.models import (
    GoalProposal,
    GoalProposalAcceptance,
    GoalProposalRejection,
    GoalProposalStatus,
)
from orion.application.goals.proposals.service import (
    GoalProposalError,
    GoalProposalService,
)
from orion.application.results import ApplicationResult


@dataclass(frozen=True)
class CreateGoalProposalRequest:
    goal_request: GoalRequest
    expiry_hours: int | None = None
    supersedes: str = ""
    source: str = "goal_engine"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.goal_request, GoalRequest):
            raise TypeError("Proposal creation requires a GoalRequest.")


@dataclass(frozen=True)
class GoalProposalReferenceRequest:
    proposal_id: str


@dataclass(frozen=True)
class ListGoalProposalsRequest:
    status: str = ""
    goal_id: str = ""
    limit: int = 100


class GoalProposalApplicationHandler:
    """Coordinate Goal planning and proposal services without UI dependencies."""

    def __init__(
        self,
        goal_engine: GoalEngine,
        proposal_service: GoalProposalService,
        *,
        event_publisher: EventPublisher | None = None,
    ) -> None:
        if not isinstance(goal_engine, GoalEngine):
            raise TypeError("Goal Proposal handler requires a GoalEngine.")
        if not isinstance(proposal_service, GoalProposalService):
            raise TypeError("Goal Proposal handler requires a proposal service.")
        if event_publisher is not None and not isinstance(
            event_publisher,
            EventPublisher,
        ):
            raise TypeError("Goal Proposal event publisher must be EventPublisher.")
        self.goal_engine = goal_engine
        self.service = proposal_service
        self.event_publisher = event_publisher

    def create(self, request: CreateGoalProposalRequest) -> ApplicationResult:
        if not isinstance(request, CreateGoalProposalRequest):
            return self._failure(
                "request_invalid",
                "Goal Proposal creation requires a structured request.",
            )
        prior = self._safe_get(request.supersedes) if request.supersedes else None
        try:
            plan = self.goal_engine.plan(request.goal_request)
            proposal = self.service.create(
                plan,
                expiry_hours=request.expiry_hours,
                supersedes=request.supersedes,
                source=request.source,
                metadata=request.metadata,
            )
        except (
            GoalPlanningError,
            GoalProposalError,
            FileExistsError,
            FileNotFoundError,
            OSError,
            PermissionError,
            TypeError,
            ValueError,
        ) as exc:
            result = self._exception_failure("Goal Proposal creation failed", exc)
            return self._attach(
                result,
                self._expired_correction(prior, request.supersedes),
            )
        result = ApplicationResult.success(
            self._format_proposal(proposal, heading="Goal Proposal Created"),
            data={
                "command": "create",
                "proposal": proposal.to_dict(),
                **self._proposal_fields(proposal),
                "planning_only": True,
                "executed_capabilities": 0,
            },
            warnings=plan.warnings,
            next_actions=self.service.next_actions(proposal),
        )
        publication = self._publish(
            EventTypes.GOAL_PROPOSAL_CREATED,
            proposal,
            severity="notice",
            data={
                "proposal_id": proposal.proposal_id,
                "goal_id": proposal.goal_id,
                "version": proposal.version,
                "status": proposal.status.value,
                "classification": proposal.classification,
                "workspace": proposal.workspace,
                "department_id": proposal.department_id,
                "expires_at": proposal.expires_at,
                "capability_id": proposal.current.capability_id,
            },
        )
        return self._attach(result, publication)

    def show(self, request: GoalProposalReferenceRequest) -> ApplicationResult:
        if not isinstance(request, GoalProposalReferenceRequest):
            return self._failure(
                "request_invalid",
                "Goal Proposal show requires a structured reference.",
            )
        try:
            proposal = self.service.get(request.proposal_id)
        except (
            FileNotFoundError,
            OSError,
            PermissionError,
            TypeError,
            ValueError,
        ) as exc:
            return self._exception_failure("Goal Proposal could not be shown", exc)
        return ApplicationResult.success(
            self._format_proposal(proposal),
            data={
                "command": "show",
                "proposal": proposal.to_dict(),
                **self._proposal_fields(proposal),
            },
            next_actions=self.service.next_actions(proposal),
        )

    def list(self, request: ListGoalProposalsRequest) -> ApplicationResult:
        if not isinstance(request, ListGoalProposalsRequest):
            return self._failure(
                "request_invalid",
                "Goal Proposal list requires a structured request.",
            )
        try:
            proposals = self.service.list(
                status=request.status or None,
                goal_id=request.goal_id,
                limit=request.limit,
            )
        except (
            OSError,
            PermissionError,
            TypeError,
            ValueError,
        ) as exc:
            return self._exception_failure("Goal Proposals could not be listed", exc)
        lines = ["Goal Proposals", "-" * 72]
        if not proposals:
            lines.append("No matching proposals.")
        for item in proposals:
            lines.append(
                f"{item.proposal_id}  v{item.version:<3} "
                f"{item.status.value:<10} {item.classification:<22} "
                f"{item.goal_text[:48]}"
            )
        return ApplicationResult.success(
            "\n".join(lines),
            data={
                "command": "list",
                "proposals": [item.to_dict() for item in proposals],
                "count": len(proposals),
            },
            next_actions=('goal proposal create "<goal>"',),
        )

    def validate(self, request: GoalProposalReferenceRequest) -> ApplicationResult:
        if not isinstance(request, GoalProposalReferenceRequest):
            return self._failure(
                "request_invalid",
                "Goal Proposal validation requires a structured reference.",
            )
        try:
            prior = self.service.get(request.proposal_id)
            validation = self.service.validate(request.proposal_id)
            proposal = self.service.get(request.proposal_id)
        except (
            FileNotFoundError,
            OSError,
            PermissionError,
            TypeError,
            ValueError,
        ) as exc:
            return self._exception_failure("Goal Proposal validation failed", exc)
        data = {
            "command": "validate",
            "proposal": proposal.to_dict(),
            **self._proposal_fields(proposal),
            "validation": validation.to_dict(),
            "validation_status": validation.validation_status,
            "planning_only": True,
            "executed_capabilities": 0,
        }
        lines = [
            "Goal Proposal Validation",
            "-" * 72,
            f"Proposal    : {proposal.proposal_id}",
            f"Status      : {proposal.status.value}",
            f"Validation  : {validation.validation_status}",
            f"Plan hash   : {'valid' if validation.plan_hash_valid else 'INVALID'}",
            (
                "Capabilities: "
                + (
                    "valid"
                    if validation.capability_fingerprint_valid
                    else "CHANGED"
                )
            ),
            f"Workspace   : {'valid' if validation.workspace_valid else 'unavailable'}",
            f"Department  : {'valid' if validation.department_valid else 'unavailable'}",
            f"Translation : {'supported' if validation.translation_supported else 'blocked'}",
            "No capability was executed.",
        ]
        if validation.valid:
            result = ApplicationResult.success(
                "\n".join(lines),
                data=data,
                warnings=validation.warnings,
                next_actions=self.service.next_actions(proposal),
            )
        else:
            result = ApplicationResult.failure(
                "\n".join(lines),
                data=data,
                errors=validation.errors,
                warnings=validation.warnings,
                next_actions=self.service.next_actions(proposal),
            )
        publications = [
            self._publish(
                EventTypes.GOAL_PROPOSAL_VALIDATED,
                proposal,
                severity="info" if validation.valid else "warning",
                data={
                    "proposal_id": proposal.proposal_id,
                    "goal_id": proposal.goal_id,
                    "version": proposal.version,
                    "status": proposal.status.value,
                    "validation_status": validation.validation_status,
                    "valid": validation.valid,
                    "plan_hash_valid": validation.plan_hash_valid,
                    "capability_fingerprint_valid": (
                        validation.capability_fingerprint_valid
                    ),
                },
            ),
        ]
        if (
            prior.status is GoalProposalStatus.PENDING
            and proposal.status is GoalProposalStatus.EXPIRED
        ):
            publications.append(self._publish(
                EventTypes.GOAL_PROPOSAL_EXPIRED,
                proposal,
                severity="warning",
                data={
                    "proposal_id": proposal.proposal_id,
                    "goal_id": proposal.goal_id,
                    "version": proposal.version,
                    "status": proposal.status.value,
                    "expires_at": proposal.expires_at,
                },
            ))
        return self._attach(result, *publications)

    def accept(self, acceptance: GoalProposalAcceptance) -> ApplicationResult:
        prior = (
            self._safe_get(acceptance.proposal_id)
            if isinstance(acceptance, GoalProposalAcceptance)
            else None
        )
        try:
            dispatch = self.service.accept(acceptance)
        except (
            GoalProposalError,
            FileNotFoundError,
            OSError,
            PermissionError,
            TypeError,
            ValueError,
        ) as exc:
            result = self._exception_failure("Goal Proposal acceptance failed", exc)
            proposal_id = (
                acceptance.proposal_id
                if isinstance(acceptance, GoalProposalAcceptance)
                else ""
            )
            return self._attach(
                result,
                self._expired_correction(prior, proposal_id),
            )
        proposal = dispatch.proposal
        downstream = dispatch.application_result
        data = {
            "command": "accept",
            "proposal": proposal.to_dict(),
            **self._proposal_fields(proposal),
            "application_result": downstream.to_dict(),
            "dispatched_capability_count": 1,
        }
        message = "\n".join((
            (
                "Goal Proposal Consumed"
                if proposal.status is GoalProposalStatus.CONSUMED
                else "Goal Proposal Dispatch Failed"
            ),
            "-" * 72,
            f"Proposal   : {proposal.proposal_id}",
            f"Capability : {proposal.attempted_capability_id}",
            f"Status     : {proposal.status.value}",
            (
                "The accepted operation was dispatched once through its existing "
                "application handler."
            ),
            (
                "Goal Proposal acceptance is not AI Team implementation approval; "
                "all downstream approval boundaries remain authoritative."
            ),
            "",
            downstream.message,
        ))
        next_actions = tuple(dict.fromkeys(
            (*downstream.next_actions, *self.service.next_actions(proposal))
        ))
        if downstream.ok:
            result = ApplicationResult.success(
                message,
                data=data,
                warnings=downstream.warnings,
                next_actions=next_actions,
            )
        else:
            result = ApplicationResult.failure(
                message,
                data=data,
                errors=downstream.errors or (
                    "Downstream application operation failed.",
                ),
                warnings=downstream.warnings,
                next_actions=next_actions,
            )
        return self._attach(result, *dispatch.event_publications)

    def reject(self, rejection: GoalProposalRejection) -> ApplicationResult:
        prior = (
            self._safe_get(rejection.proposal_id)
            if isinstance(rejection, GoalProposalRejection)
            else None
        )
        try:
            proposal = self.service.reject(rejection)
        except (
            GoalProposalError,
            FileNotFoundError,
            OSError,
            PermissionError,
            TypeError,
            ValueError,
        ) as exc:
            result = self._exception_failure("Goal Proposal rejection failed", exc)
            proposal_id = (
                rejection.proposal_id
                if isinstance(rejection, GoalProposalRejection)
                else ""
            )
            return self._attach(
                result,
                self._expired_correction(prior, proposal_id),
            )
        result = ApplicationResult.success(
            self._format_proposal(proposal, heading="Goal Proposal Rejected"),
            data={
                "command": "reject",
                "proposal": proposal.to_dict(),
                **self._proposal_fields(proposal),
                "planning_only": True,
                "executed_capabilities": 0,
            },
            next_actions=self.service.next_actions(proposal),
        )
        publication = self._publish(
            EventTypes.GOAL_PROPOSAL_REJECTED,
            proposal,
            severity="notice",
            data={
                "proposal_id": proposal.proposal_id,
                "goal_id": proposal.goal_id,
                "version": proposal.version,
                "status": proposal.status.value,
                "rejected_by": proposal.rejected_by,
                "reason_recorded": bool(proposal.rejection_reason),
            },
        )
        return self._attach(result, publication)

    @staticmethod
    def _format_proposal(
        proposal: GoalProposal,
        *,
        heading: str = "Goal Proposal",
    ) -> str:
        current = proposal.current
        return "\n".join((
            heading,
            "-" * 72,
            f"Proposal ID : {proposal.proposal_id}",
            f"Goal ID     : {proposal.goal_id}",
            f"Version     : {proposal.version}",
            f"Status      : {proposal.status.value}",
            f"Goal        : {proposal.goal_text}",
            f"Workspace   : {proposal.workspace}",
            f"Department  : {proposal.department_name or 'Unassigned'}",
            f"Next step   : {current.capability_id}",
            f"Mutates     : {'YES' if current.mutates_state else 'NO'}",
            (
                "Cap approval: "
                f"{'REQUIRED' if current.requires_approval else 'NOT REQUIRED'}"
            ),
            f"Expires     : {proposal.expires_at}",
            f"Plan SHA-256: {proposal.plan_hash}",
            (
                "Review only until explicit acceptance; acceptance can dispatch "
                "at most this one operation."
            ),
        ))

    @staticmethod
    def _proposal_fields(proposal: GoalProposal) -> dict[str, object]:
        current = proposal.current
        return {
            "proposal_id": proposal.proposal_id,
            "goal_id": proposal.goal_id,
            "version": proposal.version,
            "status": proposal.status.value,
            "goal": proposal.goal_text,
            "classification": proposal.classification,
            "workspace": proposal.workspace,
            "department": proposal.department_name,
            "created_at": proposal.created_at,
            "expires_at": proposal.expires_at,
            "plan_hash": proposal.plan_hash,
            "registry_fingerprint": proposal.registry_fingerprint,
            "capability_fingerprint": proposal.capability_fingerprint,
            "current_step": proposal.current_step,
            "capability_id": current.capability_id,
            "requires_approval": current.requires_approval,
            "mutates_state": current.mutates_state,
            "warnings": proposal.metadata.get("goal_warnings", ()),
            "risks": proposal.metadata.get("goal_risks", ()),
            "accepted_at": proposal.accepted_at,
            "rejected_at": proposal.rejected_at,
            "consumed_at": proposal.consumed_at,
        }

    def _publish(
        self,
        event_type: str,
        proposal: GoalProposal,
        *,
        severity: str,
        data: Mapping[str, object],
    ) -> EventPublication:
        if self.event_publisher is None:
            return EventPublication()
        return self.event_publisher.publish_lifecycle_event(
            event_type=event_type,
            source="goal_proposals",
            severity=severity,
            correlation_id=proposal.goal_id,
            subject_id=proposal.proposal_id,
            data=data,
        )

    def _attach(
        self,
        result: ApplicationResult,
        *publications: EventPublication,
    ) -> ApplicationResult:
        if self.event_publisher is None:
            return result
        return self.event_publisher.attach(result, *publications)

    def _safe_get(self, proposal_id: str) -> GoalProposal | None:
        if not proposal_id:
            return None
        try:
            return self.service.get(proposal_id)
        except (
            FileNotFoundError,
            OSError,
            PermissionError,
            TypeError,
            ValueError,
        ):
            return None

    def _expired_correction(
        self,
        prior: GoalProposal | None,
        proposal_id: str,
    ) -> EventPublication:
        if prior is None or prior.status is not GoalProposalStatus.PENDING:
            return EventPublication()
        current = self._safe_get(proposal_id)
        if current is None or current.status is not GoalProposalStatus.EXPIRED:
            return EventPublication()
        return self._publish(
            EventTypes.GOAL_PROPOSAL_EXPIRED,
            current,
            severity="warning",
            data={
                "proposal_id": current.proposal_id,
                "goal_id": current.goal_id,
                "version": current.version,
                "status": current.status.value,
                "expires_at": current.expires_at,
            },
        )

    @classmethod
    def _exception_failure(
        cls,
        prefix: str,
        exc: BaseException,
    ) -> ApplicationResult:
        code = getattr(exc, "code", type(exc).__name__)
        message = cls._safe_message(exc)
        return ApplicationResult.failure(
            f"{prefix}: {message}",
            data={"error_code": str(code)},
            errors=(message,),
        )

    @staticmethod
    def _failure(code: str, message: str) -> ApplicationResult:
        return ApplicationResult.failure(
            message,
            data={"error_code": code},
            errors=(message,),
        )

    @staticmethod
    def _safe_message(exc: BaseException) -> str:
        return " ".join(
            (str(exc).strip() or type(exc).__name__).split()
        )[:500]
