"""Stable Orion event type contracts."""
from __future__ import annotations


class EventTypes:
    """Names emitted by stable Orion application boundaries."""

    GOAL_PLAN_CREATED = "goal.plan.created"
    GOAL_PROPOSAL_CREATED = "goal.proposal.created"
    GOAL_PROPOSAL_VALIDATED = "goal.proposal.validated"
    GOAL_PROPOSAL_ACCEPTED = "goal.proposal.accepted"
    GOAL_PROPOSAL_REJECTED = "goal.proposal.rejected"
    GOAL_PROPOSAL_EXPIRED = "goal.proposal.expired"
    GOAL_PROPOSAL_CONSUMED = "goal.proposal.consumed"
    GOAL_PROPOSAL_FAILED = "goal.proposal.failed"
    TEAM_PLAN_CREATED = "team.plan.created"
    TEAM_PLAN_APPROVED = "team.plan.approved"
    TEAM_IMPLEMENTATION_STARTED = "team.implementation.started"
    TEAM_IMPLEMENTATION_COMPLETED = "team.implementation.completed"
    TEAM_IMPLEMENTATION_FAILED = "team.implementation.failed"
    TEAM_VALIDATION_COMPLETED = "team.validation.completed"
    TEAM_DOCUMENTATION_REVIEW_COMPLETED = "team.documentation_review.completed"
    TEAM_FINAL_REVIEW_COMPLETED = "team.final_review.completed"
    TEAM_FINAL_REVIEW_BLOCKED = "team.final_review.blocked"


KNOWN_EVENT_TYPES = (
    EventTypes.GOAL_PLAN_CREATED,
    EventTypes.GOAL_PROPOSAL_CREATED,
    EventTypes.GOAL_PROPOSAL_VALIDATED,
    EventTypes.GOAL_PROPOSAL_ACCEPTED,
    EventTypes.GOAL_PROPOSAL_REJECTED,
    EventTypes.GOAL_PROPOSAL_EXPIRED,
    EventTypes.GOAL_PROPOSAL_CONSUMED,
    EventTypes.GOAL_PROPOSAL_FAILED,
    EventTypes.TEAM_PLAN_CREATED,
    EventTypes.TEAM_PLAN_APPROVED,
    EventTypes.TEAM_IMPLEMENTATION_STARTED,
    EventTypes.TEAM_IMPLEMENTATION_COMPLETED,
    EventTypes.TEAM_IMPLEMENTATION_FAILED,
    EventTypes.TEAM_VALIDATION_COMPLETED,
    EventTypes.TEAM_DOCUMENTATION_REVIEW_COMPLETED,
    EventTypes.TEAM_FINAL_REVIEW_COMPLETED,
    EventTypes.TEAM_FINAL_REVIEW_BLOCKED,
)
