"""Goal Proposal lifecycle, validation, acceptance, and single-step dispatch."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from orion.application.capabilities import CapabilityRegistry
from orion.application.goals.models import GoalPlan
from orion.application.goals.proposals.integrity import (
    proposal_plan_hash,
    registry_fingerprint,
    scoped_capability_fingerprint,
)
from orion.application.goals.proposals.models import (
    GOAL_PROPOSAL_SCHEMA_VERSION,
    GoalProposal,
    GoalProposalAcceptance,
    GoalProposalRejection,
    GoalProposalSnapshot,
    GoalProposalStatus,
    GoalProposalStep,
    GoalProposalStepStatus,
    GoalProposalValidation,
)
from orion.application.goals.proposals.repository import GoalProposalRepository
from orion.application.goals.proposals.translator import (
    GoalProposalTranslationError,
    GoalProposalTranslator,
)
from orion.application.results import ApplicationResult


@dataclass(frozen=True)
class GoalProposalDispatch:
    """Outcome of one accepted, allowlisted application operation."""

    proposal: GoalProposal
    application_result: ApplicationResult


class GoalProposalError(ValueError):
    """Stable proposal lifecycle error suitable for application mapping."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


class GoalProposalService:
    """Persist and advance proposals without implementing domain operations."""

    def __init__(
        self,
        repository: GoalProposalRepository,
        capability_registry: CapabilityRegistry,
        *,
        translator: GoalProposalTranslator,
        team_application=None,
        workspace_manager=None,
        project_context=None,
        command_center=None,
        default_expiry_hours: int = 24,
        max_expiry_hours: int = 168,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(repository, GoalProposalRepository):
            raise TypeError("Goal Proposal service requires a repository.")
        if not isinstance(capability_registry, CapabilityRegistry):
            raise TypeError("Goal Proposal service requires a CapabilityRegistry.")
        if not isinstance(translator, GoalProposalTranslator):
            raise TypeError("Goal Proposal service requires an allowlisted translator.")
        self.repository = repository
        self.capability_registry = capability_registry
        self.translator = translator
        self.team_application = team_application
        self.workspace_manager = workspace_manager
        self.project_context = project_context
        self.command_center = command_center
        self.default_expiry_hours = self._expiry_hours(
            default_expiry_hours,
            maximum=max_expiry_hours,
        )
        if (
            isinstance(max_expiry_hours, bool)
            or not isinstance(max_expiry_hours, int)
            or max_expiry_hours < self.default_expiry_hours
        ):
            raise ValueError(
                "Maximum Goal Proposal expiry must be an integer no shorter "
                "than the default."
            )
        self.max_expiry_hours = max_expiry_hours
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (
            lambda: f"proposal-{uuid4().hex}"
        )

    def create(
        self,
        plan: GoalPlan,
        *,
        expiry_hours: int | None = None,
        supersedes: str = "",
        source: str = "goal_engine",
        metadata: Mapping[str, object] | None = None,
    ) -> GoalProposal:
        """Create and persist a proposal without executing a capability."""
        if not isinstance(plan, GoalPlan):
            raise TypeError("Goal Proposal creation requires a GoalPlan.")
        hours = self._expiry_hours(
            self.default_expiry_hours if expiry_hours is None else expiry_hours,
            maximum=self.max_expiry_hours,
        )
        definitions = {
            item.capability_id: item
            for item in self.capability_registry.list()
        }
        for step in plan.capability_steps:
            definition = definitions.get(step.capability_id)
            if definition is None:
                raise GoalProposalError(
                    "capability_unavailable",
                    f"Goal capability is no longer registered: {step.capability_id}",
                )
            self._verify_plan_step(step, definition)

        now = self._now_utc()
        proposal_id = str(self._id_factory()).strip().lower()
        existing = self.repository.list(goal_id=plan.goal_id, limit=500)
        version = max((item.version for item in existing), default=0) + 1
        prior = None
        supersedes_id = str(supersedes).strip()
        if supersedes_id:
            prior = self.repository.get(supersedes_id)
            if prior.goal_id != plan.goal_id:
                raise GoalProposalError(
                    "supersession_goal_mismatch",
                    "A Goal Proposal may only supersede the same goal ID.",
                )
            if prior.status is not GoalProposalStatus.PENDING:
                raise GoalProposalError(
                    "supersession_not_pending",
                    "Only a pending Goal Proposal may be superseded.",
                )
            if now >= self._parse_time(prior.expires_at):
                self._correct_status(
                    prior,
                    GoalProposalStatus.EXPIRED,
                    now,
                    "proposal_expired",
                    "Goal Proposal expired before supersession.",
                )
                raise GoalProposalError(
                    "proposal_expired",
                    "An expired Goal Proposal cannot be superseded.",
                )
            if proposal_plan_hash(prior.snapshot()) != prior.plan_hash:
                raise GoalProposalError(
                    "proposal_hash_mismatch",
                    "The proposal selected for supersession failed integrity checks.",
                )
            version = max(version, prior.version + 1)

        expires_at = now + timedelta(hours=hours)
        capabilities = tuple(
            item.capability_id for item in plan.capability_steps
        )
        full_fingerprint = registry_fingerprint(self.capability_registry)
        capability_fingerprint = scoped_capability_fingerprint(
            self.capability_registry,
            capabilities,
        )
        steps = tuple(
            self._proposal_step(proposal_id, plan, item)
            for item in plan.capability_steps
        )
        eligible = next(
            (
                item.step_number
                for item in steps
                if self.translator.supports(item.capability_id)
            ),
            1,
        )
        snapshot = GoalProposalSnapshot(
            proposal_id=proposal_id,
            goal_id=plan.goal_id,
            version=version,
            goal_text=plan.goal,
            classification=plan.classification,
            workspace=plan.context.workspace,
            department_id=plan.context.department_id,
            department_name=plan.context.department_name,
            priority=plan.context.priority,
            created_at=self._format_time(now),
            expires_at=self._format_time(expires_at),
            registry_fingerprint=full_fingerprint,
            capability_fingerprint=capability_fingerprint,
            steps=steps,
            source=source,
            supersedes=supersedes_id,
            metadata={
                **dict(metadata or {}),
                "goal_warnings": list(plan.warnings),
                "goal_risks": list(plan.risks),
            },
        )
        proposal = GoalProposal(
            schema_version=GOAL_PROPOSAL_SCHEMA_VERSION,
            proposal_id=proposal_id,
            goal_id=snapshot.goal_id,
            version=version,
            status=GoalProposalStatus.PENDING,
            goal_text=snapshot.goal_text,
            classification=snapshot.classification,
            workspace=snapshot.workspace,
            department_id=snapshot.department_id,
            department_name=snapshot.department_name,
            priority=snapshot.priority,
            created_at=self._format_time(now),
            updated_at=self._format_time(now),
            expires_at=snapshot.expires_at,
            plan_hash=proposal_plan_hash(snapshot),
            registry_fingerprint=full_fingerprint,
            capability_fingerprint=capability_fingerprint,
            steps=steps,
            current_step=eligible,
            source=snapshot.source,
            supersedes=supersedes_id,
            metadata=snapshot.metadata,
        )
        if prior is None:
            self.repository.save(proposal)
            return proposal
        old = replace(
            prior,
            status=GoalProposalStatus.SUPERSEDED,
            updated_at=self._format_time(now),
            superseded_by=proposal.proposal_id,
        )
        self.repository.save_supersession(old, proposal)
        return proposal

    def get(self, proposal_id: str) -> GoalProposal:
        return self.repository.get(proposal_id)

    def list(
        self,
        *,
        status: GoalProposalStatus | str | None = None,
        goal_id: str = "",
        limit: int = 100,
    ) -> tuple[GoalProposal, ...]:
        return self.repository.list(
            status=status,
            goal_id=goal_id,
            limit=limit,
        )

    def validate(
        self,
        proposal_id: str,
        *,
        correct_status: bool = True,
    ) -> GoalProposalValidation:
        """Validate without execution; only expired/invalid state may be corrected."""
        proposal = self.repository.get(proposal_id)
        now = self._now_utc()
        warnings: list[str] = []
        errors: list[str] = []

        status_error = self._status_validation_error(proposal.status)
        if status_error:
            errors.append(status_error)

        expired = (
            proposal.status is GoalProposalStatus.PENDING
            and now >= self._parse_time(proposal.expires_at)
        )
        if expired:
            errors.append("Goal Proposal has expired.")

        calculated_hash = proposal_plan_hash(proposal.snapshot())
        plan_hash_valid = calculated_hash == proposal.plan_hash
        if not plan_hash_valid:
            errors.append("Goal Proposal immutable plan hash does not match.")

        full_current = registry_fingerprint(self.capability_registry)
        registry_changed = full_current != proposal.registry_fingerprint
        try:
            scoped_current = scoped_capability_fingerprint(
                self.capability_registry,
                tuple(item.capability_id for item in proposal.steps),
            )
            scoped_valid = (
                scoped_current == proposal.capability_fingerprint
            )
        except KeyError:
            scoped_valid = False
        if not scoped_valid:
            errors.append(
                "A selected capability is missing or its safety metadata changed."
            )
        elif registry_changed:
            warnings.append(
                "The full Capability Registry changed, but all selected "
                "capabilities retain their exact safety metadata."
            )

        workspace_valid = self._workspace_valid(proposal.workspace)
        if not workspace_valid:
            errors.append(
                "Proposal workspace is missing or is not the active workspace."
            )
        department_valid = self._department_valid(
            proposal.department_id,
            proposal.department_name,
        )
        if not department_valid:
            errors.append("Proposal department is unavailable or disabled.")

        current = proposal.current
        inputs_valid = all(
            name in current.resolved_inputs
            and current.resolved_inputs[name] not in {None, ""}
            for name in current.required_inputs
        )
        if not inputs_valid:
            errors.append(
                f"Current capability {current.capability_id} has unresolved inputs."
            )
        translation_supported = (
            self.translator.supports(current.capability_id)
            and current.application_request_type
            == self.translator.request_type(current.capability_id)
        )
        if not translation_supported:
            errors.append(
                f"Capability translation is not supported: {current.capability_id}"
            )
        if any(
            item.application_request_type
            not in {"", self.translator.request_type(item.capability_id)}
            for item in proposal.steps
        ):
            errors.append("Proposal contains an unknown application request type.")

        demonstrably_invalid = (
            not plan_hash_valid
            or not scoped_valid
            or not inputs_valid
            or any("unknown application request type" in item for item in errors)
        )
        if correct_status and proposal.status is GoalProposalStatus.PENDING:
            if expired:
                self._correct_status(
                    proposal,
                    GoalProposalStatus.EXPIRED,
                    now,
                    "proposal_expired",
                    "Goal Proposal expired before acceptance.",
                )
            elif demonstrably_invalid:
                self._correct_status(
                    proposal,
                    GoalProposalStatus.INVALID,
                    now,
                    "proposal_invalid",
                    errors[0] if errors else "Goal Proposal validation failed.",
                )

        valid = not errors
        if valid:
            validation_status = "warning" if warnings else "valid"
        elif expired:
            validation_status = "expired"
        elif demonstrably_invalid:
            validation_status = "invalid"
        elif proposal.status is not GoalProposalStatus.PENDING:
            validation_status = proposal.status.value
        else:
            validation_status = "blocked"
        return GoalProposalValidation(
            proposal_id=proposal.proposal_id,
            valid=valid,
            validation_status=validation_status,
            checked_at=self._format_time(now),
            plan_hash_valid=plan_hash_valid,
            capability_fingerprint_valid=scoped_valid,
            registry_fingerprint_changed=registry_changed,
            workspace_valid=workspace_valid,
            department_valid=department_valid,
            inputs_valid=inputs_valid,
            translation_supported=translation_supported,
            warnings=tuple(dict.fromkeys(warnings)),
            errors=tuple(dict.fromkeys(errors)),
        )

    def accept(
        self,
        acceptance: GoalProposalAcceptance,
    ) -> GoalProposalDispatch:
        """Atomically accept, then dispatch at most one allowlisted operation."""
        if not isinstance(acceptance, GoalProposalAcceptance):
            raise TypeError("Goal Proposal acceptance request is required.")
        if not acceptance.confirmed:
            raise GoalProposalError(
                "confirmation_required",
                "Goal Proposal acceptance requires explicit confirmation.",
            )
        proposal = self.repository.get(acceptance.proposal_id)
        if proposal.plan_hash != acceptance.proposal_hash:
            raise GoalProposalError(
                "acceptance_hash_mismatch",
                "Acceptance hash does not match the persisted Goal Proposal.",
            )
        validation = self.validate(proposal.proposal_id, correct_status=True)
        if not validation.valid:
            raise GoalProposalError(
                validation.validation_status,
                validation.errors[0] if validation.errors else (
                    "Goal Proposal cannot be accepted."
                ),
            )
        proposal = self.repository.get(proposal.proposal_id)
        current = proposal.current
        try:
            translation = self.translator.translate(current)
        except GoalProposalTranslationError as exc:
            raise GoalProposalError("translation_unsupported", str(exc)) from exc

        now = self._now_utc()
        accepted = replace(
            proposal,
            status=GoalProposalStatus.ACCEPTED,
            updated_at=self._format_time(now),
            accepted_at=self._format_time(now),
            accepted_by=acceptance.accepted_by,
            attempted_capability_id=current.capability_id,
            steps=self._steps_with_status(
                proposal,
                GoalProposalStepStatus.ACCEPTED,
            ),
        )
        try:
            self.repository.replace(
                accepted,
                expected_status=GoalProposalStatus.PENDING,
            )
        except PermissionError as exc:
            raise GoalProposalError(
                "proposal_already_used",
                "Goal Proposal acceptance is single-use and its state changed.",
            ) from exc

        try:
            result = self.translator.dispatch(
                translation,
                team_application=self.team_application,
            )
        except (
            GoalProposalTranslationError,
            OSError,
            PermissionError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            result = ApplicationResult.failure(
                f"Accepted proposal operation failed: {self._safe_message(exc)}",
                data={
                    "capability_id": current.capability_id,
                    "proposal_id": proposal.proposal_id,
                },
                errors=(self._safe_message(exc),),
            )

        finished_at = self._now_utc()
        if result.ok:
            terminal = replace(
                accepted,
                status=GoalProposalStatus.CONSUMED,
                updated_at=self._format_time(finished_at),
                consumed_at=self._format_time(finished_at),
                dispatch_summary=self._dispatch_summary(result),
                steps=self._steps_with_status(
                    accepted,
                    GoalProposalStepStatus.CONSUMED,
                ),
            )
        else:
            terminal = replace(
                accepted,
                status=GoalProposalStatus.FAILED,
                updated_at=self._format_time(finished_at),
                failed_at=self._format_time(finished_at),
                failure_code="dispatch_failed",
                failure_message=self._safe_message_text(
                    result.message or "Downstream application operation failed."
                ),
                dispatch_summary=self._dispatch_summary(result),
                steps=self._steps_with_status(
                    accepted,
                    GoalProposalStepStatus.FAILED,
                ),
            )
        try:
            self.repository.replace(
                terminal,
                expected_status=GoalProposalStatus.ACCEPTED,
            )
        except (OSError, PermissionError, ValueError) as exc:
            raise GoalProposalError(
                "post_dispatch_persistence_failed",
                (
                    "The operation returned, but its terminal proposal state "
                    "could not be persisted. The accepted proposal remains "
                    "non-replayable and requires inspection."
                ),
            ) from exc
        return GoalProposalDispatch(terminal, result)

    def reject(self, rejection: GoalProposalRejection) -> GoalProposal:
        if not isinstance(rejection, GoalProposalRejection):
            raise TypeError("Goal Proposal rejection request is required.")
        proposal = self.repository.get(rejection.proposal_id)
        if proposal.status is not GoalProposalStatus.PENDING:
            raise GoalProposalError(
                f"proposal_{proposal.status.value}",
                f"Only a pending proposal may be rejected; "
                f"current status is {proposal.status.value}.",
            )
        now = self._now_utc()
        if now >= self._parse_time(proposal.expires_at):
            self._correct_status(
                proposal,
                GoalProposalStatus.EXPIRED,
                now,
                "proposal_expired",
                "Goal Proposal expired before rejection.",
            )
            raise GoalProposalError(
                "proposal_expired",
                "Expired Goal Proposals cannot be rejected.",
            )
        if proposal_plan_hash(proposal.snapshot()) != proposal.plan_hash:
            self._correct_status(
                proposal,
                GoalProposalStatus.INVALID,
                now,
                "proposal_invalid",
                "Goal Proposal immutable plan hash does not match.",
            )
            raise GoalProposalError(
                "proposal_hash_mismatch",
                "Invalid Goal Proposals cannot be rejected.",
            )
        rejected = replace(
            proposal,
            status=GoalProposalStatus.REJECTED,
            updated_at=self._format_time(now),
            rejected_at=self._format_time(now),
            rejected_by=rejection.rejected_by,
            rejection_reason=rejection.reason,
        )
        self.repository.replace(
            rejected,
            expected_status=GoalProposalStatus.PENDING,
        )
        return rejected

    def next_actions(self, proposal: GoalProposal) -> tuple[str, ...]:
        identity = proposal.proposal_id
        if proposal.status is GoalProposalStatus.PENDING:
            return (
                f"goal proposal show {identity}",
                f"goal proposal validate {identity}",
                f"goal proposal accept {identity}",
                f"goal proposal reject {identity}",
            )
        if proposal.status is GoalProposalStatus.CONSUMED:
            reference = str(
                proposal.dispatch_summary.get("team_task_id", "")
            ).strip()
            return (
                (f"team status {reference}" if reference else "team"),
                f"goal proposal show {identity}",
            )
        if proposal.status is GoalProposalStatus.SUPERSEDED:
            return (
                f"goal proposal show {proposal.superseded_by}",
                f"goal proposal show {identity}",
            )
        if proposal.status in {
            GoalProposalStatus.EXPIRED,
            GoalProposalStatus.INVALID,
        }:
            return (
                f'goal proposal create "{proposal.goal_text}"',
                f"goal proposal show {identity}",
            )
        return (
            f"goal proposal show {identity}",
            f'goal plan "{proposal.goal_text}"',
        )

    def _proposal_step(self, proposal_id: str, plan: GoalPlan, step) -> GoalProposalStep:
        base_inputs: dict[str, object] = {
            "goal": plan.goal,
            "workspace": plan.context.workspace,
            "department": plan.context.department_id,
            "priority": plan.context.priority,
        }
        resolved = {
            name: base_inputs[name]
            for name in step.required_inputs
            if name in base_inputs and base_inputs[name] not in {None, ""}
        }
        return GoalProposalStep(
            step_id=f"{proposal_id}-step-{step.step_number:03d}",
            step_number=step.step_number,
            capability_id=step.capability_id,
            reason=step.reason,
            requires_approval=step.requires_approval,
            mutates_state=step.mutates_state,
            required_inputs=step.required_inputs,
            resolved_inputs=resolved,
            expected_outputs=step.expected_outputs,
            required_permissions=step.required_permissions,
            application_request_type=self.translator.request_type(
                step.capability_id
            ),
        )

    @staticmethod
    def _verify_plan_step(step, definition) -> None:
        schema = definition.input_schema
        required = tuple(str(item) for item in schema.get("required", ()))
        output_required = tuple(
            str(item) for item in definition.output_schema.get("required", ())
        )
        output_properties = definition.output_schema.get("properties", {})
        outputs = (
            output_required
            if output_required
            else tuple(sorted(str(key) for key in output_properties))
            if isinstance(output_properties, Mapping)
            else ()
        )
        if (
            step.requires_approval is not definition.requires_approval
            or step.mutates_state is not definition.mutates_state
            or step.required_inputs != required
            or step.expected_outputs != outputs
            or step.required_permissions != definition.required_permissions
        ):
            raise GoalProposalError(
                "capability_metadata_mismatch",
                f"Goal Plan capability metadata is stale: {step.capability_id}",
            )

    def _workspace_valid(self, workspace: str) -> bool:
        try:
            root = Path(workspace).expanduser().resolve()
            if not root.is_dir():
                return False
            active = getattr(self.workspace_manager, "root", None)
            if active is None:
                active = getattr(self.project_context, "workspace_root", None)
            if active is None:
                return False
            return Path(active).expanduser().resolve() == root
        except (OSError, RuntimeError, TypeError, ValueError):
            return False

    def _department_valid(self, department_id: str, department_name: str) -> bool:
        if not department_id and not department_name:
            return True
        if self.command_center is None:
            return False
        try:
            expected = {department_id.casefold(), department_name.casefold()} - {""}
            return any(
                bool(getattr(item, "enabled", False))
                and bool(expected & {
                    str(getattr(item, "department_id", "")).casefold(),
                    str(getattr(item, "name", "")).casefold(),
                })
                for item in self.command_center.departments()
            )
        except (OSError, PermissionError, RuntimeError, ValueError):
            return False

    @staticmethod
    def _status_validation_error(status: GoalProposalStatus) -> str:
        messages = {
            GoalProposalStatus.ACCEPTED: (
                "Goal Proposal was accepted and dispatch outcome is uncertain."
            ),
            GoalProposalStatus.REJECTED: "Goal Proposal was rejected.",
            GoalProposalStatus.EXPIRED: "Goal Proposal has expired.",
            GoalProposalStatus.INVALID: "Goal Proposal was marked invalid.",
            GoalProposalStatus.SUPERSEDED: "Goal Proposal was superseded.",
            GoalProposalStatus.CONSUMED: "Goal Proposal was already consumed.",
            GoalProposalStatus.FAILED: "Goal Proposal dispatch previously failed.",
        }
        return messages.get(status, "")

    def _correct_status(
        self,
        proposal: GoalProposal,
        status: GoalProposalStatus,
        now: datetime,
        code: str,
        message: str,
    ) -> GoalProposal:
        corrected = replace(
            proposal,
            status=status,
            updated_at=self._format_time(now),
            failure_code=code,
            failure_message=self._safe_message_text(message),
        )
        self.repository.replace(
            corrected,
            expected_status=GoalProposalStatus.PENDING,
        )
        return corrected

    @staticmethod
    def _steps_with_status(
        proposal: GoalProposal,
        status: GoalProposalStepStatus,
    ) -> tuple[GoalProposalStep, ...]:
        return tuple(
            replace(item, status=status)
            if item.step_number == proposal.current_step
            else item
            for item in proposal.steps
        )

    @staticmethod
    def _dispatch_summary(result: ApplicationResult) -> dict[str, object]:
        allowed = (
            "team_task_id",
            "status",
            "stage",
            "approval_required",
            "approval_status",
            "plan_sha256",
        )
        summary = {
            key: result.data[key]
            for key in allowed
            if key in result.data
            and isinstance(result.data[key], (str, int, float, bool, type(None)))
        }
        summary["application_status"] = result.status
        summary["message"] = GoalProposalService._safe_message_text(result.message)
        return summary

    @staticmethod
    def _safe_message(exc: BaseException) -> str:
        return GoalProposalService._safe_message_text(
            str(exc).strip() or type(exc).__name__
        )

    @staticmethod
    def _safe_message_text(value: str) -> str:
        return " ".join(str(value).split())[:500]

    @staticmethod
    def _expiry_hours(value: object, *, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Goal Proposal expiry hours must be an integer.")
        if not 1 <= value <= maximum:
            raise ValueError(
                f"Goal Proposal expiry must be between 1 and {maximum} hours."
            )
        return value

    def _now_utc(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime):
            raise TypeError("Goal Proposal clock must return a datetime.")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Goal Proposal clock must be timezone-aware.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
