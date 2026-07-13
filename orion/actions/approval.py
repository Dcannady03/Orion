"""Central approval and policy engine for Orion actions."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from orion.actions.models import Action


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class ActionPolicy:
    action_type: str
    decision: PolicyDecision
    reason: str = ""


class ApprovalEngine:
    """Evaluates action policies and owns approval transitions."""

    def __init__(self) -> None:
        self._policies: dict[str, ActionPolicy] = {}

    def set_policy(self, action_type: str, decision: PolicyDecision, reason: str = "") -> None:
        name = action_type.strip().lower()
        if not name:
            raise ValueError("Action type cannot be empty.")
        self._policies[name] = ActionPolicy(name, decision, reason.strip())

    def evaluate(self, action: Action) -> ActionPolicy:
        policy = self._policies.get(action.type)
        if policy is not None:
            return policy
        decision = PolicyDecision.REQUIRE_APPROVAL if action.requires_approval else PolicyDecision.ALLOW
        return ActionPolicy(action.type, decision)

    def policies(self) -> tuple[ActionPolicy, ...]:
        return tuple(self._policies[name] for name in sorted(self._policies))
