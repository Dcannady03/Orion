"""Explicit allowlisted translation from proposal steps to typed requests."""
from __future__ import annotations

from dataclasses import dataclass

from orion.application.commands.ai_team_commands import TeamPlanRequest
from orion.application.goals.proposals.models import GoalProposalStep
from orion.application.results import ApplicationResult


@dataclass(frozen=True)
class GoalProposalTranslation:
    """Internal typed request envelope; never persisted or accepted from JSON."""

    capability_id: str
    application_request_type: str
    request: object


class GoalProposalTranslator:
    """Translate and dispatch only explicitly supported capability contracts."""

    TEAM_PLAN = "team.plan"
    TEAM_PLAN_REQUEST = "TeamPlanRequest"

    @classmethod
    def supports(cls, capability_id: str) -> bool:
        return str(capability_id).strip() == cls.TEAM_PLAN

    @classmethod
    def request_type(cls, capability_id: str) -> str:
        return cls.TEAM_PLAN_REQUEST if cls.supports(capability_id) else ""

    def translate(self, step: GoalProposalStep) -> GoalProposalTranslation:
        if not isinstance(step, GoalProposalStep):
            raise TypeError("Goal Proposal translation requires a proposal step.")
        if step.capability_id != self.TEAM_PLAN:
            raise GoalProposalTranslationError(
                f"Capability translation is not supported: {step.capability_id}"
            )
        if step.application_request_type != self.TEAM_PLAN_REQUEST:
            raise GoalProposalTranslationError(
                "Proposal application request type does not match team.plan."
            )
        inputs = dict(step.resolved_inputs)
        unknown = set(inputs) - {"goal"}
        if unknown:
            raise GoalProposalTranslationError(
                f"team.plan proposal inputs are not allowed: {sorted(unknown)}"
            )
        goal = inputs.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            raise GoalProposalTranslationError(
                "team.plan proposal requires a resolved goal."
            )
        request = TeamPlanRequest(goal=" ".join(goal.split()))
        return GoalProposalTranslation(
            capability_id=self.TEAM_PLAN,
            application_request_type=self.TEAM_PLAN_REQUEST,
            request=request,
        )

    def dispatch(
        self,
        translation: GoalProposalTranslation,
        *,
        team_application,
    ) -> ApplicationResult:
        """Call the allowlisted application handler directly, never a CLI adapter."""
        if not isinstance(translation, GoalProposalTranslation):
            raise TypeError("Proposal dispatch requires a typed translation.")
        if (
            translation.capability_id != self.TEAM_PLAN
            or translation.application_request_type != self.TEAM_PLAN_REQUEST
            or not isinstance(translation.request, TeamPlanRequest)
        ):
            raise GoalProposalTranslationError(
                "Translated Goal Proposal request is not allowlisted."
            )
        if team_application is None:
            raise GoalProposalTranslationError(
                "AI Team application handler is unavailable."
            )
        result = team_application.plan(translation.request)
        if not isinstance(result, ApplicationResult):
            raise GoalProposalTranslationError(
                "AI Team application handler returned an invalid result."
            )
        return result


class GoalProposalTranslationError(ValueError):
    """Raised when a proposal step cannot become an allowlisted typed request."""
