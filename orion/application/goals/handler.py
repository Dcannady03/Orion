"""Application-layer boundary for deterministic Goal Engine operations."""
from __future__ import annotations

from orion.application.goals.engine import GoalEngine, GoalPlanningError
from orion.application.goals.models import GoalRequest
from orion.application.results import ApplicationResult


class GoalApplicationHandler:
    """Return portable application results without executing proposed work."""

    def __init__(self, engine: GoalEngine) -> None:
        if not isinstance(engine, GoalEngine):
            raise TypeError("Goal application handler requires a GoalEngine.")
        self.engine = engine

    def plan(self, request: GoalRequest) -> ApplicationResult:
        return self._planned(request, view="plan")

    def explain(self, request: GoalRequest) -> ApplicationResult:
        return self._planned(request, view="explain")

    def preview(self, request: GoalRequest) -> ApplicationResult:
        return self._planned(request, view="preview")

    def capabilities(self, request: GoalRequest) -> ApplicationResult:
        return self._planned(request, view="capabilities")

    def validate(self, request: GoalRequest) -> ApplicationResult:
        return self._planned(request, view="validate")

    def classify(self, request: GoalRequest) -> ApplicationResult:
        try:
            classification = self.engine.classify(request)
        except (
            GoalPlanningError,
            OSError,
            PermissionError,
            TypeError,
            ValueError,
        ) as exc:
            return self._failure(request, "Goal classification failed", exc)
        return ApplicationResult.success(
            "\n".join((
                "Goal Classification",
                "-" * 60,
                f"Goal       : {request.goal_text}",
                f"Category   : {classification.category}",
                f"Confidence : {classification.confidence:.0%}",
                f"Reason     : {classification.reason}",
                "",
                "No capability was executed.",
            )),
            data={
                "command": "classify",
                "classification": classification.to_dict(),
                "planning_only": True,
            },
        )

    def _planned(self, request: GoalRequest, *, view: str) -> ApplicationResult:
        try:
            plan = self.engine.plan(request)
        except (
            GoalPlanningError,
            OSError,
            PermissionError,
            TypeError,
            ValueError,
        ) as exc:
            return self._failure(request, "Goal planning failed", exc)
        data: dict[str, object] = {
            "command": view,
            "goal_plan": plan.to_dict(),
            "planning_only": True,
        }
        if view == "preview":
            data["execution_preview"] = plan.execution_preview.to_dict()
            message = self._preview_message(plan)
        elif view == "explain":
            data["explanation"] = plan.explanation.to_dict()
            message = self._explanation_message(plan)
        elif view == "capabilities":
            data["capabilities"] = [
                item.to_dict() for item in plan.capability_steps
            ]
            message = self._capability_message(plan)
        elif view == "validate":
            data["validation"] = {
                "valid": True,
                "registered_capability_count": len(plan.capability_steps),
                "approval_boundaries": sum(
                    1 for item in plan.capability_steps
                    if item.requires_approval
                ),
                "informational_only": True,
            }
            message = "\n".join((
                "Goal Validation",
                "-" * 60,
                f"Goal ID      : {plan.goal_id}",
                "Valid         : YES",
                f"Capabilities  : {len(plan.capability_steps)} registered",
                f"Approval      : {'REQUIRED' if plan.approval_required else 'NOT REQUIRED'}",
                "Safety        : planning only; nothing executed",
            ))
        else:
            message = self._plan_message(plan)
        return ApplicationResult.success(
            message,
            data=data,
            warnings=plan.warnings,
            next_actions=plan.next_actions,
        )

    @staticmethod
    def _plan_message(plan) -> str:
        lines = [
            "Orion Goal Plan",
            "-" * 60,
            f"Goal          : {plan.goal}",
            f"Classification: {plan.classification} ({plan.confidence:.0%})",
            f"Workspace     : {plan.context.workspace_name}",
            f"Department    : {plan.context.department_name or 'Unassigned'}",
            "",
            "Capabilities",
        ]
        lines.extend(
            f"  {item.step_number}. {item.capability_id} "
            f"[{item.estimated_stage}]"
            for item in plan.capability_steps
        )
        lines.extend((
            "",
            f"Approval      : {'REQUIRED' if plan.approval_required else 'NOT REQUIRED'}",
            f"Est. stages   : {len(plan.estimated_stages)}",
            "Safety        : informational plan only; nothing executed",
        ))
        return "\n".join(lines)

    @staticmethod
    def _preview_message(plan) -> str:
        preview = plan.execution_preview
        lines = [
            "Goal Execution Preview",
            "-" * 60,
            f"Goal       : {preview.goal}",
            f"Workspace  : {plan.context.workspace_name}",
            f"Department : {preview.department}",
            "",
            "Execution Plan",
        ]
        lines.extend(f"  - {item}" for item in preview.execution_plan)
        lines.extend((
            "",
            f"Approval   : {'REQUIRED' if preview.approval_required else 'NOT REQUIRED'}",
            f"Stages     : {len(preview.estimated_stages)}",
            "Preview only. No capability was executed.",
        ))
        return "\n".join(lines)

    @staticmethod
    def _explanation_message(plan) -> str:
        explanation = plan.explanation
        lines = [
            "Goal Plan Explanation",
            "-" * 60,
            explanation.summary,
            "",
            f"Classification: {explanation.classification_reason}",
            f"Workspace     : {explanation.workspace_reason}",
            f"Department    : {explanation.department_reason}",
            f"Approval      : {explanation.approval_reason}",
            "",
            "Capability decisions",
        ]
        lines.extend(f"  - {item}" for item in explanation.capability_reasons)
        lines.extend(("", explanation.safety_boundary))
        return "\n".join(lines)

    @staticmethod
    def _capability_message(plan) -> str:
        lines = [
            "Goal Capabilities",
            "-" * 60,
        ]
        lines.extend(
            (
                f"  {item.step_number}. {item.capability_id}\n"
                f"     Stage: {item.estimated_stage}\n"
                f"     Approval: {'yes' if item.requires_approval else 'no'}\n"
                f"     Reason: {item.reason}"
            )
            for item in plan.capability_steps
        )
        lines.extend(("", "Registry-backed proposal only; nothing executed."))
        return "\n".join(lines)

    @staticmethod
    def _failure(
        request: GoalRequest,
        prefix: str,
        exc: Exception,
    ) -> ApplicationResult:
        detail = str(exc).strip() or type(exc).__name__
        goal = getattr(request, "goal_text", "")
        return ApplicationResult.failure(
            f"{prefix}: {detail}",
            data={
                "goal": str(goal),
                "planning_only": True,
            },
            errors=(detail,),
            next_actions=('goal plan "<clear outcome>"',),
        )
