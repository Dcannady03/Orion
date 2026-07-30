"""Immutable, interface-neutral models for Orion Goal Engine planning."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math


GOAL_CATEGORIES = (
    "Engineering",
    "Marketing",
    "Documentation",
    "Research",
    "Automation",
    "Security",
    "Operations",
    "Planning",
    "Release",
    "Personal Productivity",
)
GOAL_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
GOAL_EXECUTION_MODES = frozenset({"plan", "preview"})


def _text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    normalized = str(value).strip()
    if required and not normalized:
        raise ValueError(f"{label} must be a non-empty string.")
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be {maximum:,} characters or fewer.")
    return normalized


def _strings(
    values: tuple[str, ...] | list[str],
    label: str,
    *,
    maximum_items: int = 100,
) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    normalized = tuple(
        _text(value, label, 1_000, required=True)
        for value in values
    )
    if len(normalized) > maximum_items:
        raise ValueError(f"{label} cannot contain more than {maximum_items} items.")
    return normalized


@dataclass(frozen=True)
class GoalRequest:
    """One high-level user outcome submitted for planning only."""

    goal_text: str
    workspace: str = ""
    department: str = ""
    priority: str = "normal"
    requested_outcome: str = ""
    attachments: tuple[str, ...] = ()
    provider_preferences: tuple[str, ...] = ()
    execution_mode: str = "plan"
    allow_ai_planning: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "goal_text",
            _text(self.goal_text, "Goal text", 4_000, required=True),
        )
        object.__setattr__(
            self,
            "workspace",
            _text(self.workspace, "Workspace", 1_000),
        )
        object.__setattr__(
            self,
            "department",
            _text(self.department, "Department", 120),
        )
        priority = str(self.priority).strip().lower()
        if priority not in GOAL_PRIORITIES:
            choices = ", ".join(sorted(GOAL_PRIORITIES))
            raise ValueError(f"Goal priority must be one of: {choices}.")
        object.__setattr__(self, "priority", priority)
        object.__setattr__(
            self,
            "requested_outcome",
            _text(self.requested_outcome, "Requested outcome", 2_000),
        )
        object.__setattr__(
            self,
            "attachments",
            _strings(self.attachments, "Goal attachments"),
        )
        object.__setattr__(
            self,
            "provider_preferences",
            _strings(
                self.provider_preferences,
                "Provider preferences",
                maximum_items=20,
            ),
        )
        execution_mode = str(self.execution_mode).strip().lower()
        if execution_mode not in GOAL_EXECUTION_MODES:
            raise ValueError(
                "Goal execution mode must be plan or preview; "
                "the Goal Engine cannot execute work."
            )
        object.__setattr__(self, "execution_mode", execution_mode)
        if not isinstance(self.allow_ai_planning, bool):
            raise ValueError("allow_ai_planning must be true or false.")

    def to_dict(self) -> dict[str, object]:
        return {
            "goal_text": self.goal_text,
            "workspace": self.workspace,
            "department": self.department,
            "priority": self.priority,
            "requested_outcome": self.requested_outcome,
            "attachments": list(self.attachments),
            "provider_preferences": list(self.provider_preferences),
            "execution_mode": self.execution_mode,
            "allow_ai_planning": self.allow_ai_planning,
        }


@dataclass(frozen=True)
class GoalClassification:
    """Deterministic intent classification and its evidence."""

    category: str
    confidence: float
    reason: str
    matched_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.category not in GOAL_CATEGORIES:
            raise ValueError(f"Unknown Goal Engine category: {self.category}")
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("Goal confidence must be between 0 and 1.")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(
            self,
            "reason",
            _text(self.reason, "Classification reason", 1_000, required=True),
        )
        object.__setattr__(
            self,
            "matched_terms",
            _strings(self.matched_terms, "Matched classification terms"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "confidence": self.confidence,
            "reason": self.reason,
            "matched_terms": list(self.matched_terms),
        }


@dataclass(frozen=True)
class GoalContext:
    """Resolved planning context without service or implementation objects."""

    workspace: str
    workspace_name: str
    workspace_source: str
    workspace_mode: str
    project_name: str
    department_id: str
    department_name: str
    priority: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workspace",
            _text(self.workspace, "Resolved workspace", 1_000, required=True),
        )
        for field_name, label, maximum in (
            ("workspace_name", "Workspace name", 255),
            ("workspace_source", "Workspace source", 80),
            ("workspace_mode", "Workspace mode", 80),
            ("project_name", "Project name", 255),
            ("department_id", "Department ID", 120),
            ("department_name", "Department name", 120),
        ):
            object.__setattr__(
                self,
                field_name,
                _text(getattr(self, field_name), label, maximum),
            )
        priority = str(self.priority).strip().lower()
        if priority not in GOAL_PRIORITIES:
            raise ValueError(f"Unknown Goal Engine priority: {priority}")
        object.__setattr__(self, "priority", priority)

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace": self.workspace,
            "workspace_name": self.workspace_name,
            "workspace_source": self.workspace_source,
            "workspace_mode": self.workspace_mode,
            "project_name": self.project_name,
            "department_id": self.department_id,
            "department_name": self.department_name,
            "priority": self.priority,
        }


@dataclass(frozen=True)
class CapabilityStep:
    """One registry-backed capability proposed for future execution."""

    step_number: int
    capability_id: str
    reason: str
    requires_approval: bool
    mutates_state: bool
    estimated_stage: str
    required_inputs: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.step_number, bool)
            or not isinstance(self.step_number, int)
            or self.step_number < 1
        ):
            raise ValueError("Capability step number must be a positive integer.")
        for field_name, label, maximum in (
            ("capability_id", "Capability ID", 200),
            ("reason", "Capability reason", 1_000),
            ("estimated_stage", "Capability stage", 120),
        ):
            object.__setattr__(
                self,
                field_name,
                _text(
                    getattr(self, field_name),
                    label,
                    maximum,
                    required=True,
                ),
            )
        if not isinstance(self.requires_approval, bool):
            raise ValueError("Capability approval metadata must be true or false.")
        if not isinstance(self.mutates_state, bool):
            raise ValueError("Capability mutation metadata must be true or false.")
        object.__setattr__(
            self,
            "required_inputs",
            _strings(self.required_inputs, "Required inputs"),
        )
        object.__setattr__(
            self,
            "expected_outputs",
            _strings(self.expected_outputs, "Expected outputs"),
        )
        object.__setattr__(
            self,
            "required_permissions",
            _strings(self.required_permissions, "Required permissions"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "step_number": self.step_number,
            "capability_id": self.capability_id,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "mutates_state": self.mutates_state,
            "estimated_stage": self.estimated_stage,
            "required_inputs": list(self.required_inputs),
            "expected_outputs": list(self.expected_outputs),
            "required_permissions": list(self.required_permissions),
        }


@dataclass(frozen=True)
class GoalExplanation:
    """Human-readable evidence behind a deterministic goal plan."""

    summary: str
    classification_reason: str
    workspace_reason: str
    department_reason: str
    capability_reasons: tuple[str, ...]
    approval_reason: str
    safety_boundary: str

    def __post_init__(self) -> None:
        for field_name in (
            "summary",
            "classification_reason",
            "workspace_reason",
            "department_reason",
            "approval_reason",
            "safety_boundary",
        ):
            object.__setattr__(
                self,
                field_name,
                _text(
                    getattr(self, field_name),
                    field_name.replace("_", " ").title(),
                    2_000,
                    required=True,
                ),
            )
        object.__setattr__(
            self,
            "capability_reasons",
            _strings(self.capability_reasons, "Capability explanations"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "classification_reason": self.classification_reason,
            "workspace_reason": self.workspace_reason,
            "department_reason": self.department_reason,
            "capability_reasons": list(self.capability_reasons),
            "approval_reason": self.approval_reason,
            "safety_boundary": self.safety_boundary,
        }


@dataclass(frozen=True)
class GoalPreview:
    """A portable, informational-only rendering contract for a plan."""

    goal_id: str
    goal: str
    classification: str
    workspace: str
    department: str
    execution_plan: tuple[str, ...]
    approval_boundaries: tuple[str, ...]
    approval_required: bool
    estimated_stages: tuple[str, ...]
    informational_only: bool = True

    def __post_init__(self) -> None:
        for field_name, label, maximum in (
            ("goal_id", "Goal ID", 120),
            ("goal", "Goal", 4_000),
            ("classification", "Classification", 120),
            ("workspace", "Workspace", 1_000),
            ("department", "Department", 120),
        ):
            object.__setattr__(
                self,
                field_name,
                _text(
                    getattr(self, field_name),
                    label,
                    maximum,
                    required=True,
                ),
            )
        object.__setattr__(
            self,
            "execution_plan",
            _strings(self.execution_plan, "Execution preview steps"),
        )
        object.__setattr__(
            self,
            "approval_boundaries",
            _strings(self.approval_boundaries, "Approval boundaries"),
        )
        object.__setattr__(
            self,
            "estimated_stages",
            _strings(self.estimated_stages, "Estimated stages"),
        )
        if not isinstance(self.approval_required, bool):
            raise ValueError("Goal preview approval state must be true or false.")
        if self.informational_only is not True:
            raise ValueError("Goal previews must remain informational only.")

    def to_dict(self) -> dict[str, object]:
        return {
            "goal_id": self.goal_id,
            "goal": self.goal,
            "classification": self.classification,
            "workspace": self.workspace,
            "department": self.department,
            "execution_plan": list(self.execution_plan),
            "approval_boundaries": list(self.approval_boundaries),
            "approval_required": self.approval_required,
            "estimated_stages": list(self.estimated_stages),
            "estimated_stage_count": len(self.estimated_stages),
            "informational_only": self.informational_only,
        }


@dataclass(frozen=True)
class GoalPlan:
    """Complete deterministic plan produced without executing any capability."""

    goal_id: str
    goal: str
    classification: str
    confidence: float
    context: GoalContext
    capability_steps: tuple[CapabilityStep, ...]
    estimated_stages: tuple[str, ...]
    approval_required: bool
    warnings: tuple[str, ...]
    risks: tuple[str, ...]
    explanation: GoalExplanation
    execution_preview: GoalPreview
    next_actions: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "goal_id",
            _text(self.goal_id, "Goal ID", 120, required=True),
        )
        object.__setattr__(
            self,
            "goal",
            _text(self.goal, "Goal", 4_000, required=True),
        )
        if self.classification not in GOAL_CATEGORIES:
            raise ValueError(f"Unknown Goal Engine category: {self.classification}")
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("Goal confidence must be between 0 and 1.")
        object.__setattr__(self, "confidence", confidence)
        if not isinstance(self.context, GoalContext):
            raise TypeError("Goal context must be a GoalContext.")
        steps = tuple(self.capability_steps)
        if not steps or any(not isinstance(item, CapabilityStep) for item in steps):
            raise ValueError("Goal plan must contain capability steps.")
        if tuple(item.step_number for item in steps) != tuple(
            range(1, len(steps) + 1)
        ):
            raise ValueError("Goal capability steps must be consecutively numbered.")
        object.__setattr__(self, "capability_steps", steps)
        predicted_approval = any(item.requires_approval for item in steps)
        if self.approval_required is not predicted_approval:
            raise ValueError(
                "Goal approval prediction must match its capability metadata."
            )
        object.__setattr__(
            self,
            "estimated_stages",
            _strings(self.estimated_stages, "Estimated stages"),
        )
        object.__setattr__(self, "warnings", _strings(self.warnings, "Goal warnings"))
        object.__setattr__(self, "risks", _strings(self.risks, "Goal risks"))
        if not isinstance(self.explanation, GoalExplanation):
            raise TypeError("Goal explanation must be a GoalExplanation.")
        if not isinstance(self.execution_preview, GoalPreview):
            raise TypeError("Execution preview must be a GoalPreview.")
        if self.execution_preview.approval_required is not self.approval_required:
            raise ValueError(
                "Goal preview approval prediction must match the goal plan."
            )
        object.__setattr__(
            self,
            "next_actions",
            _strings(self.next_actions, "Goal next actions"),
        )

    @property
    def estimated_capabilities(self) -> int:
        return len(self.capability_steps)

    @property
    def workspace(self) -> str:
        return self.context.workspace

    @property
    def department(self) -> str:
        return self.context.department_name

    def to_dict(self) -> dict[str, object]:
        return {
            "goal_id": self.goal_id,
            "goal": self.goal,
            "classification": self.classification,
            "confidence": self.confidence,
            "workspace": self.context.workspace,
            "department": self.context.department_name,
            "context": self.context.to_dict(),
            "estimated_stages": list(self.estimated_stages),
            "estimated_stage_count": len(self.estimated_stages),
            "estimated_capabilities": self.estimated_capabilities,
            "capability_steps": [
                item.to_dict() for item in self.capability_steps
            ],
            "approval_required": self.approval_required,
            "execution_preview": self.execution_preview.to_dict(),
            "warnings": list(self.warnings),
            "risks": list(self.risks),
            "explanation": self.explanation.to_dict(),
            "next_actions": list(self.next_actions),
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
        )
