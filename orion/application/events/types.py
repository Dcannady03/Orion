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
)
