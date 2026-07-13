"""Unified action registration and execution service."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from orion.actions.approval import ApprovalEngine, PolicyDecision
from orion.actions.history import ActionHistory
from orion.actions.models import Action, ActionResult, ActionStatus, utc_now

ActionHandler = Callable[[Action], Any]


class ActionService:
    def __init__(self, history: ActionHistory, approval: ApprovalEngine | None = None) -> None:
        self.history = history
        self.approval = approval or ApprovalEngine()
        self._handlers: dict[str, ActionHandler] = {}
        self._actions: dict[str, Action] = {}

    def register_handler(self, action_type: str, handler: ActionHandler, *, replace: bool = False) -> None:
        name = action_type.strip().lower()
        if not name:
            raise ValueError("Action type cannot be empty.")
        if not callable(handler):
            raise TypeError("Action handler must be callable.")
        if name in self._handlers and not replace:
            raise KeyError(f"Action handler already registered: {name}")
        self._handlers[name] = handler

    def create(self, action_type: str, parameters: dict[str, Any] | None = None, *, source: str = "user", requires_approval: bool = False) -> Action:
        action = Action(action_type, parameters or {}, source, requires_approval)
        if action.type not in self._handlers:
            raise KeyError(f"No action handler registered for: {action.type}")
        policy = self.approval.evaluate(action)
        if policy.decision is PolicyDecision.DENY:
            action.status = ActionStatus.DENIED
            self._actions[action.id] = action
            self.history.record("denied_by_policy", action, detail=policy.reason)
            raise PermissionError(policy.reason or f"Action denied by policy: {action.type}")
        if policy.decision is PolicyDecision.REQUIRE_APPROVAL:
            action.requires_approval = True
            action.status = ActionStatus.PENDING_APPROVAL
        self._actions[action.id] = action
        self.history.record("created", action, detail=policy.reason)
        return action

    def get(self, action_id: str) -> Action:
        try:
            return self._actions[action_id]
        except KeyError as exc:
            raise KeyError(f"Unknown action: {action_id}") from exc


    def pending(self) -> tuple[Action, ...]:
        return tuple(action for action in self._actions.values() if action.status is ActionStatus.PENDING_APPROVAL)

    def approve(self, action_id: str) -> Action:
        action = self.get(action_id)
        if action.status is not ActionStatus.PENDING_APPROVAL:
            raise RuntimeError(f"Action is not awaiting approval: {action.status.value}")
        action.status = ActionStatus.APPROVED
        self.history.record("approved", action)
        return action

    def deny(self, action_id: str, reason: str = "") -> Action:
        action = self.get(action_id)
        if action.status is not ActionStatus.PENDING_APPROVAL:
            raise RuntimeError(f"Action is not awaiting approval: {action.status.value}")
        action.status = ActionStatus.DENIED
        self.history.record("denied", action, detail=reason.strip())
        return action

    def execute(self, action: Action) -> ActionResult:
        if action.status is ActionStatus.DENIED:
            raise PermissionError("Denied action cannot be executed.")
        if action.status in {ActionStatus.EXECUTING, ActionStatus.SUCCEEDED, ActionStatus.FAILED}:
            raise RuntimeError(f"Action cannot execute from status: {action.status.value}")
        if action.requires_approval and action.status is not ActionStatus.APPROVED:
            raise PermissionError("Action requires approval before execution.")
        handler = self._handlers[action.type]
        action.status = ActionStatus.EXECUTING
        self.history.record("executing", action)
        started = utc_now()
        try:
            value = handler(action)
            action.status = ActionStatus.SUCCEEDED
            result = ActionResult(action.id, True, output="" if value is None else str(value), started_at=started, finished_at=utc_now())
        except Exception as exc:  # handlers define their own failure modes
            action.status = ActionStatus.FAILED
            result = ActionResult(action.id, False, error=str(exc), started_at=started, finished_at=utc_now())
        self.history.record("completed", action, result)
        return result

    def run(self, action_type: str, parameters: dict[str, Any] | None = None, *, source: str = "user") -> ActionResult:
        action = self.create(action_type, parameters, source=source)
        return self.execute(action)

    def handler_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))
