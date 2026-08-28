"""Human-confirmed, exactly-one-operation Mission coordination."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from threading import RLock
from typing import Mapping
from uuid import uuid4

from orion.application.commands.ai_team_commands import TeamTaskRequest
from orion.application.interface_actions import interface_action
from orion.application.missions.coordination_models import (
    MissionAdvanceAudit,
    MissionAdvancePreview,
    MissionAdvanceRequest,
    MissionAdvanceResult,
    MissionNextOperation,
)
from orion.application.missions.coordination_repository import (
    MissionCoordinationRepository,
)
from orion.application.missions.models import Mission, MissionStage, MissionStatus
from orion.application.missions.service import MissionService
from orion.application.missions.translator import MissionOperationTranslator
from orion.application.results import ApplicationResult


_PLAN_HASH_PATTERN = re.compile(r"[a-f0-9]{64}")


class MissionCoordinator:
    """Preview and dispatch one allowlisted operation, then always stop."""

    def __init__(
        self,
        mission_service: MissionService,
        team_application,
        audit_repository: MissionCoordinationRepository,
        *,
        translator: MissionOperationTranslator | None = None,
        clock=None,
        attempt_id_factory=None,
    ) -> None:
        if not isinstance(mission_service, MissionService):
            raise TypeError("Mission Coordinator requires MissionService.")
        if not isinstance(audit_repository, MissionCoordinationRepository):
            raise TypeError(
                "Mission Coordinator requires MissionCoordinationRepository."
            )
        self.mission_service = mission_service
        self.team_application = team_application
        self.audit_repository = audit_repository
        self.translator = translator or MissionOperationTranslator()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._attempt_id_factory = attempt_id_factory or (
            lambda: f"mission-attempt-{uuid4().hex}"
        )
        self._lock = RLock()

    def preview(self, mission_id: str) -> MissionAdvancePreview:
        """Reconcile and preview without dispatching any domain operation."""
        reconciliation = self.mission_service.reconcile(mission_id)
        mission = reconciliation.mission
        validation = self.mission_service.validate(mission.mission_id)
        if not validation.valid:
            reason = validation.errors[0] if validation.errors else (
                "Mission validation did not establish a safe current projection."
            )
            return self._blocked_preview(mission, reason, validation.warnings)
        return self._preview_current(mission, check_audit=True)

    def advance(self, request: MissionAdvanceRequest) -> MissionAdvanceResult:
        if not isinstance(request, MissionAdvanceRequest):
            raise TypeError("Mission advance requires a structured request.")
        if not request.confirmed:
            raise MissionCoordinationError(
                "Mission advancement requires explicit confirmation."
            )
        if not request.advance_token:
            raise MissionCoordinationError(
                "Mission advancement requires the exact preview token."
            )

        with self._lock:
            with self.audit_repository.advance_lock(request.mission_id):
                reconciliation = self.mission_service.reconcile(request.mission_id)
                mission = reconciliation.mission
                validation = self.mission_service.validate(mission.mission_id)
                if not validation.valid:
                    raise MissionCoordinationError(
                        validation.errors[0] if validation.errors else
                        "Mission validation failed before advancement."
                    )
                preview = self._preview_current(mission, check_audit=False)
                if preview.operation.blocked:
                    raise MissionCoordinationError(preview.operation.blocked_reason)
                if not hmac.compare_digest(
                    preview.advance_token,
                    request.advance_token,
                ):
                    raise MissionStalePreviewError(
                        "Mission changed after preview. Re-run mission next."
                    )

                prior = self.audit_repository.get(mission.mission_id)
                audit_block = self._audit_block_reason(prior, preview.advance_token)
                if audit_block:
                    raise MissionCoordinationError(audit_block)
                reserved = MissionAdvanceAudit(
                    schema_version=1,
                    mission_id=mission.mission_id,
                    attempt_id=str(self._attempt_id_factory()).strip().lower(),
                    advance_token=preview.advance_token,
                    capability_id=preview.operation.capability_id,
                    subject_id=preview.operation.subject_id,
                    actor=request.actor,
                    state="reserved",
                    started_at=self._now(),
                )
                self.audit_repository.write(
                    reserved,
                    expected_attempt_id=prior.attempt_id if prior else None,
                )

                try:
                    translation = self.translator.translate(
                        preview.operation,
                        actor=request.actor,
                        correlation_id=mission.goal_id,
                        causation_id=mission.last_event_id or None,
                    )
                    downstream = self.translator.dispatch(
                        translation,
                        team_application=self.team_application,
                    )
                except Exception as exc:
                    try:
                        self.mission_service.reconcile(mission.mission_id)
                    except Exception:
                        pass
                    self._record_uncertain(reserved, exc)
                    raise MissionDispatchUncertainError(
                        "Mission dispatch outcome is uncertain; automatic replay is "
                        "blocked. Inspect downstream state and reconcile explicitly."
                    ) from exc

                try:
                    after = self.mission_service.reconcile(mission.mission_id)
                except Exception as exc:
                    self._record_uncertain(reserved, exc)
                    raise MissionDispatchUncertainError(
                        "The downstream operation returned, but Mission reconciliation "
                        "failed. Automatic replay is blocked."
                    ) from exc

                updated = after.mission
                audit_state = "succeeded" if downstream.ok else "failed"
                downstream_reference = self._downstream_reference(downstream)
                completed = replace(
                    reserved,
                    state=audit_state,
                    finished_at=self._now(),
                    result_status=downstream.status,
                    downstream_reference=downstream_reference,
                    last_advance_event_id=updated.last_event_id,
                    safe_message=self._safe_message(downstream.message),
                )
                self.audit_repository.write(
                    completed,
                    expected_attempt_id=reserved.attempt_id,
                )

                warnings = tuple(dict.fromkeys((
                    *downstream.warnings,
                    *updated.warnings,
                )))
                if downstream.ok and updated.status == mission.status:
                    warnings = tuple(dict.fromkeys((
                        *warnings,
                        "The operation succeeded, but no authoritative Mission state "
                        "change is observable yet; duplicate replay is blocked.",
                    )))
                return MissionAdvanceResult(
                    mission_id=mission.mission_id,
                    operation_dispatched=True,
                    capability_id=preview.operation.capability_id,
                    downstream_result=downstream.to_dict(),
                    previous_status=mission.status.value,
                    new_status=updated.status.value,
                    new_stage=updated.stage.value,
                    new_progress=updated.progress,
                    reconciled=after.changed,
                    next_action=updated.next_action,
                    audit_state=audit_state,
                    warnings=warnings,
                )

    def _preview_current(
        self,
        mission: Mission,
        *,
        check_audit: bool,
    ) -> MissionAdvancePreview:
        operation = self.determine_operation(mission)
        if operation.blocked:
            return self._blocked_preview(mission, operation.blocked_reason)
        token = self._advance_token(mission, operation)
        if check_audit:
            if self.audit_repository.lock_exists(mission.mission_id):
                return self._blocked_preview(
                    mission,
                    "Another Mission advance may be active or its outcome may be "
                    "uncertain.",
                )
            prior = self.audit_repository.get(mission.mission_id)
            audit_block = self._audit_block_reason(prior, token)
            if audit_block:
                return self._blocked_preview(mission, audit_block)
        return MissionAdvancePreview(
            mission_id=mission.mission_id,
            status=mission.status.value,
            stage=mission.stage.value,
            progress=mission.progress,
            mission_updated_at=mission.updated_at,
            last_event_id=mission.last_event_id,
            operation=operation,
            advance_token=token,
            warnings=mission.warnings,
        )

    def determine_operation(self, mission: Mission) -> MissionNextOperation:
        """Determine one operation from authoritative state without dispatching it."""
        if mission.status is MissionStatus.FAILED:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "Failed Missions cannot be advanced or retried automatically.",
            )
        if mission.status is MissionStatus.BLOCKED:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The authoritative Team lifecycle is blocked; automatic retry is "
                "not supported.",
            )
        if mission.status is MissionStatus.COMPLETED:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The Mission is already complete and has no next mutation.",
            )
        if mission.status is MissionStatus.IMPLEMENTING:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "A Team implementation is already recorded as executing. Inspect "
                "the run and reconcile before another operation.",
            )
        if mission.status is MissionStatus.AWAITING_REVIEW:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The Mission is awaiting explicit final review; the AI Team "
                "application exposes no typed completion operation.",
            )
        if (
            mission.status is MissionStatus.AWAITING_APPROVAL
            and mission.stage is MissionStage.APPROVAL
        ):
            return self._approval_operation(mission)
        if (
            mission.status is MissionStatus.APPROVED
            and mission.stage is MissionStage.IMPLEMENTATION
        ):
            return self._implementation_operation(mission)
        if (
            mission.status is MissionStatus.AWAITING_VALIDATION
            and mission.stage is MissionStage.VALIDATION
        ):
            return self._validation_operation(mission)
        if (
            mission.status is MissionStatus.AWAITING_DOCUMENTATION
            and mission.stage is MissionStage.DOCUMENTATION_REVIEW
        ):
            return self._documentation_operation(mission)
        return MissionNextOperation.blocked_operation(
            mission.mission_id,
            "No supported operation is eligible from the current Mission state.",
        )

    def _approval_operation(self, mission: Mission) -> MissionNextOperation:
        task = mission.link("team_task")
        if task is None:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The awaiting-approval Mission has no authoritative Team task ID.",
            )
        try:
            details = self.team_application.approval_details(
                TeamTaskRequest(task.subject_id)
            )
        except Exception as exc:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "AI Team approval state could not be inspected safely "
                f"({type(exc).__name__}).",
            )
        if not isinstance(details, ApplicationResult) or not details.ok:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "AI Team approval details are unavailable for this Mission.",
            )
        if str(details.data.get("team_task_id", "")).strip() != task.subject_id:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "AI Team approval details do not match the Mission task.",
            )
        if str(details.data.get("status", "")).strip() != "awaiting_approval":
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The authoritative Team task is no longer awaiting approval.",
            )
        if details.data.get("approval_inspection_available") is not True:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "Existing Team approvals cannot be inspected; advancement fails closed.",
            )
        existing_count = details.data.get("existing_approval_count", 0)
        if (
            isinstance(existing_count, bool)
            or not isinstance(existing_count, int)
            or existing_count < 0
        ):
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "Existing Team approval metadata is invalid.",
            )
        if existing_count:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "A Team approval already exists, but Mission projection has not "
                "observed it. Inspect and reconcile before continuing.",
            )
        plan_sha256 = str(details.data.get("plan_sha256", "")).strip().lower()
        if not _PLAN_HASH_PATTERN.fullmatch(plan_sha256):
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The authoritative Team plan SHA-256 is unavailable or invalid.",
            )
        action = interface_action(
            "team.approve",
            {"team_task_id": task.subject_id},
        )
        if not action.cli_command:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The team.approve interface mapping is unavailable.",
            )
        return MissionNextOperation(
            mission_id=mission.mission_id,
            capability_id="team.approve",
            operation_type="team_approval",
            subject_id=task.subject_id,
            reason="The authoritative Team plan is awaiting explicit approval.",
            requires_confirmation=True,
            requires_downstream_approval=True,
            mutates_state=True,
            required_inputs=("team_task_id", "plan_sha256"),
            resolved_inputs={
                "team_task_id": task.subject_id,
                "plan_sha256": plan_sha256,
            },
            cli_representation=action.cli_command,
        )

    def _implementation_operation(self, mission: Mission) -> MissionNextOperation:
        task = mission.link("team_task")
        approval = mission.link("approval")
        if task is None or approval is None:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The approved Mission is missing its Team task or approval link.",
            )
        details, reason = self._coordination_details(mission, task.subject_id)
        if details is None:
            return MissionNextOperation.blocked_operation(mission.mission_id, reason)
        if str(details.get("task_status", "")).strip() != "awaiting_approval":
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The authoritative Team task is not in its approved-plan source state.",
            )
        plan_sha256 = str(details.get("plan_sha256", "")).strip().lower()
        approvals = self._records(details, "approvals")
        matching_approvals = [
            item for item in approvals
            if str(item.get("team_task_id", "")).strip() == task.subject_id
            and str(item.get("approval_id", "")).strip() == approval.subject_id
            and str(item.get("plan_sha256", "")).strip().lower() == plan_sha256
        ]
        if (
            not _PLAN_HASH_PATTERN.fullmatch(plan_sha256)
            or len(approvals) != 1
            or len(matching_approvals) != 1
        ):
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The linked Team approval cannot be verified against the current plan.",
            )
        if self._records(details, "unresolved_runs"):
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "An unresolved Team run record exists; implementation fails closed.",
            )
        if self._records(details, "runs"):
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "A Team implementation run already exists, but Mission projection "
                "has not observed it. Inspect and reconcile before continuing.",
            )
        action = interface_action("team.implement", {
            "team_task_id": task.subject_id,
            "approval_id": approval.subject_id,
        })
        if not action.cli_command:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The team.implement interface mapping is unavailable.",
            )
        return MissionNextOperation(
            mission_id=mission.mission_id,
            capability_id="team.implement",
            operation_type="team_implementation",
            subject_id=task.subject_id,
            reason="The linked immutable approval is eligible for one implementation.",
            requires_confirmation=True,
            requires_downstream_approval=True,
            mutates_state=True,
            required_inputs=("team_task_id", "approval_id", "plan_sha256"),
            resolved_inputs={
                "team_task_id": task.subject_id,
                "approval_id": approval.subject_id,
                "plan_sha256": plan_sha256,
            },
            cli_representation=action.cli_command,
        )

    def _validation_operation(self, mission: Mission) -> MissionNextOperation:
        run, reason = self._verified_run(mission)
        if run is None:
            return MissionNextOperation.blocked_operation(mission.mission_id, reason)
        if str(run.get("status", "")).strip() != "awaiting_review" or str(
            run.get("implementation_status", "")
        ).strip() != "complete":
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The authoritative Team run is not a completed implementation.",
            )
        if str(run.get("validation_status", "")).strip():
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "Validation already exists, but Mission projection has not observed "
                "it. Inspect and reconcile before continuing.",
            )
        action = interface_action("team.validate", {"run_id": run["run_id"]})
        if not action.cli_command:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The team.validate interface mapping is unavailable.",
            )
        inputs = {
            "run_id": run["run_id"],
            "team_task_id": run["team_task_id"],
            "approval_id": run["approval_id"],
            "plan_sha256": run["plan_sha256"],
            "implementation_completed_at": run["completed_at"],
        }
        if (
            any(not str(value).strip() for value in inputs.values())
            or not self._valid_timestamp(inputs["implementation_completed_at"])
        ):
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The completed Team run is missing token-bound lifecycle facts.",
            )
        return MissionNextOperation(
            mission_id=mission.mission_id,
            capability_id="team.validate",
            operation_type="team_validation",
            subject_id=str(run["run_id"]),
            reason="The implementation completed and has no validation attempt.",
            requires_confirmation=True,
            requires_downstream_approval=False,
            mutates_state=True,
            required_inputs=tuple(inputs),
            resolved_inputs=inputs,
            cli_representation=action.cli_command,
        )

    def _documentation_operation(self, mission: Mission) -> MissionNextOperation:
        run, reason = self._verified_run(mission)
        if run is None:
            return MissionNextOperation.blocked_operation(mission.mission_id, reason)
        validation_status = str(run.get("validation_status", "")).strip().lower()
        if validation_status not in {"passed", "warnings"}:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "Documentation Review requires authoritative passed or warning "
                "validation state.",
            )
        if str(run.get("documentation_review_status", "")).strip():
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "Documentation Review already exists, but Mission projection has "
                "not observed it. Inspect and reconcile before continuing.",
            )
        action = interface_action(
            "team.documentation_review",
            {"run_id": run["run_id"]},
        )
        if not action.cli_command:
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The team.documentation_review interface mapping is unavailable.",
            )
        inputs = {
            "run_id": run["run_id"],
            "team_task_id": run["team_task_id"],
            "approval_id": run["approval_id"],
            "plan_sha256": run["plan_sha256"],
            "implementation_completed_at": run["completed_at"],
            "validation_id": run["validation_id"],
            "validation_status": validation_status,
            "validation_completed_at": run["validation_completed_at"],
        }
        if (
            any(not str(value).strip() for value in inputs.values())
            or not self._valid_timestamp(inputs["implementation_completed_at"])
            or not self._valid_timestamp(inputs["validation_completed_at"])
        ):
            return MissionNextOperation.blocked_operation(
                mission.mission_id,
                "The validated Team run is missing token-bound lifecycle facts.",
            )
        return MissionNextOperation(
            mission_id=mission.mission_id,
            capability_id="team.documentation_review",
            operation_type="team_documentation_review",
            subject_id=str(run["run_id"]),
            reason="Validation completed and documentation has not been reviewed.",
            requires_confirmation=True,
            requires_downstream_approval=False,
            mutates_state=True,
            required_inputs=tuple(inputs),
            resolved_inputs=inputs,
            cli_representation=action.cli_command,
        )

    def _verified_run(
        self,
        mission: Mission,
    ) -> tuple[dict[str, object] | None, str]:
        task = mission.link("team_task")
        approval = mission.link("approval")
        linked_run = mission.link("team_run")
        if task is None or approval is None or linked_run is None:
            return None, "The Mission is missing authoritative Team lifecycle links."
        details, reason = self._coordination_details(mission, task.subject_id)
        if details is None:
            return None, reason
        if self._records(details, "unresolved_runs"):
            return None, "An unresolved Team run record exists; advancement fails closed."
        runs = self._records(details, "runs")
        matching = [
            item for item in runs
            if str(item.get("run_id", "")).strip() == linked_run.subject_id
            and str(item.get("team_task_id", "")).strip() == task.subject_id
            and str(item.get("approval_id", "")).strip() == approval.subject_id
        ]
        if len(matching) != 1 or len(runs) != 1:
            return None, (
                "The authoritative Team run set does not match the Mission links; "
                "advancement fails closed."
            )
        run = matching[0]
        plan_sha256 = str(run.get("plan_sha256", "")).strip().lower()
        approvals = self._records(details, "approvals")
        matching_approvals = [
            item for item in approvals
            if str(item.get("team_task_id", "")).strip() == task.subject_id
            and str(item.get("approval_id", "")).strip() == approval.subject_id
            and str(item.get("plan_sha256", "")).strip().lower() == plan_sha256
        ]
        if (
            not _PLAN_HASH_PATTERN.fullmatch(plan_sha256)
            or plan_sha256 != str(details.get("plan_sha256", "")).strip().lower()
            or len(approvals) != 1
            or len(matching_approvals) != 1
        ):
            return None, "The Team run plan hash does not match the current Team plan."
        return run, ""

    def _coordination_details(
        self,
        mission: Mission,
        task_id: str,
    ) -> tuple[dict[str, object] | None, str]:
        inspector = getattr(self.team_application, "coordination_details", None)
        if not callable(inspector):
            return None, "AI Team coordination inspection is unavailable."
        try:
            details = inspector(TeamTaskRequest(task_id))
        except Exception as exc:
            return None, (
                "AI Team lifecycle state could not be inspected safely "
                f"({type(exc).__name__})."
            )
        if not isinstance(details, ApplicationResult) or not details.ok:
            return None, "AI Team lifecycle details are unavailable for this Mission."
        data = dict(details.data)
        if (
            str(data.get("team_task_id", "")).strip() != task_id
            or data.get("read_only") is not True
        ):
            return None, "AI Team lifecycle details do not match the Mission task."
        if data.get("approval_inspection_available") is not True:
            return None, "Team approval inspection is unavailable; advancement fails closed."
        if data.get("run_inspection_available") is not True:
            return None, "Team run inspection is unavailable; advancement fails closed."
        for name in ("approvals", "runs", "unresolved_runs"):
            records = data.get(name)
            if (
                not isinstance(records, (list, tuple))
                or len(records) > 100
                or any(not isinstance(item, Mapping) for item in records)
            ):
                return None, (
                    f"Team {name.replace('_', ' ')} metadata is malformed; "
                    "advancement fails closed."
                )
        return data, ""

    @staticmethod
    def _records(data: dict[str, object], name: str) -> list[dict[str, object]]:
        value = data.get(name, ())
        if not isinstance(value, (list, tuple)):
            return []
        return [dict(item) for item in value]

    @staticmethod
    def _valid_timestamp(value: object) -> bool:
        text = str(value or "").strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return False
        return (
            parsed.tzinfo is not None
            and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
        )

    def _blocked_preview(
        self,
        mission: Mission,
        reason: str,
        warnings: tuple[str, ...] = (),
    ) -> MissionAdvancePreview:
        return MissionAdvancePreview(
            mission_id=mission.mission_id,
            status=mission.status.value,
            stage=mission.stage.value,
            progress=mission.progress,
            mission_updated_at=mission.updated_at,
            last_event_id=mission.last_event_id,
            operation=MissionNextOperation.blocked_operation(
                mission.mission_id,
                reason,
            ),
            warnings=tuple(dict.fromkeys((*mission.warnings, *warnings))),
        )

    @staticmethod
    def _advance_token(
        mission: Mission,
        operation: MissionNextOperation,
    ) -> str:
        payload = {
            "coordination_schema": 2,
            "mission_id": mission.mission_id,
            "goal_id": mission.goal_id,
            "proposal_id": mission.proposal_id,
            "proposal_version": mission.proposal_version,
            "mission_updated_at": mission.updated_at,
            "status": mission.status.value,
            "stage": mission.stage.value,
            "progress": mission.progress,
            "last_event_id": mission.last_event_id,
            "last_event_at": mission.last_event_at,
            "event_cursor": mission.event_cursor,
            "links": [item.to_dict() for item in mission.links],
            "capability_id": operation.capability_id,
            "operation_type": operation.operation_type,
            "subject_id": operation.subject_id,
            "resolved_inputs": dict(operation.resolved_inputs),
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _audit_block_reason(
        audit: MissionAdvanceAudit | None,
        advance_token: str,
    ) -> str:
        if audit is None:
            return ""
        if audit.state in {"reserved", "uncertain"}:
            return (
                "A previous Mission dispatch outcome is uncertain; automatic replay "
                "is blocked pending operator inspection."
            )
        if audit.advance_token != advance_token:
            return ""
        if audit.state == "succeeded":
            return (
                "This exact Mission operation was already dispatched; duplicate "
                "replay is blocked."
            )
        if audit.state == "failed":
            return (
                "This exact Mission operation previously failed; automatic retry is "
                "not supported."
            )
        return ""

    def _record_uncertain(
        self,
        reserved: MissionAdvanceAudit,
        exc: BaseException,
    ) -> None:
        uncertain = replace(
            reserved,
            state="uncertain",
            finished_at=self._now(),
            result_status="uncertain",
            safe_message=(
                "Dispatch outcome uncertain after "
                f"{type(exc).__name__}. Operator inspection is required."
            ),
        )
        self.audit_repository.write(
            uncertain,
            expected_attempt_id=reserved.attempt_id,
        )

    @staticmethod
    def _downstream_reference(result: ApplicationResult) -> str:
        data = result.data
        for name in (
            "documentation_review_id",
            "validation_id",
            "run_id",
            "approval_id",
        ):
            value = str(data.get(name, "")).strip()
            if value:
                return value
        return ""

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime):
            raise TypeError("Mission Coordinator clock must return a datetime.")
        if value.tzinfo is None:
            raise ValueError("Mission Coordinator clock must be timezone-aware.")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _safe_message(value: object) -> str:
        return " ".join(str(value).split())[:300]


class MissionCoordinationError(ValueError):
    """Raised when a Mission cannot safely dispatch its next operation."""


class MissionStalePreviewError(MissionCoordinationError):
    """Raised when confirmation no longer matches authoritative Mission state."""


class MissionDispatchUncertainError(MissionCoordinationError):
    """Raised after a dispatch boundary with an uncertain observable outcome."""
