"""Immutable human final-review decision for a completed AI Team run."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any


FINAL_REVIEW_SCHEMA_VERSION = 1
FINAL_REVIEW_ID_PATTERN = re.compile(r"final-review-[0-9]{4}")
FINAL_REVIEW_DECISIONS = frozenset({"accepted", "changes_requested", "rejected"})
PLAN_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MAX_FINAL_REVIEW_NOTE_LENGTH = 500


def _exact_mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    missing = sorted(fields - set(value))
    unknown = sorted(set(value) - fields)
    if missing:
        raise ValueError(f"{label} is missing required fields: {missing}")
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {unknown}")
    return value


def _safe_text(value: Any, *, maximum: int = 1_000, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("Final-review text fields must be strings.")
    text = value.strip()
    if required and not text:
        raise ValueError("Final-review text fields cannot be empty.")
    text = " ".join(text.split())
    return text[:maximum]


def _timestamp(value: Any, label: str) -> tuple[str, datetime]:
    text = _safe_text(value, maximum=80, required=True)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset.")
    return text, parsed


def _bounded_strings(
    value: Any,
    label: str,
    *,
    maximum_items: int = 500,
    maximum_length: int = 1_000,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ValueError(f"{label} must be a bounded JSON array.")
    return tuple(_safe_text(item, maximum=maximum_length, required=True) for item in value)


@dataclass(frozen=True)
class FinalReviewDecision:
    """One immutable human decision recorded against a completed run.

    A run accepts at most one final-review decision. The decision is the
    human boundary between a reviewed run and Mission completion: ``accepted``
    completes the Mission, ``changes_requested`` and ``rejected`` block it.
    There is no rework loop in Phase 3; a blocked Mission stays blocked.
    """

    schema_version: int
    decision_id: str
    run_id: str
    team_task_id: str
    approval_id: str
    plan_sha256: str
    decision: str
    note: str
    actor: str
    completed_at: str
    artifact_paths: tuple[str, ...]

    @classmethod
    def from_value(cls, value: Any) -> "FinalReviewDecision":
        fields = {
            "schema_version", "decision_id", "run_id", "team_task_id",
            "approval_id", "plan_sha256", "decision", "note", "actor",
            "completed_at", "artifact_paths",
        }
        value = _exact_mapping(value, fields, "Final-review decision")
        if value["schema_version"] != FINAL_REVIEW_SCHEMA_VERSION:
            raise ValueError("Final-review schema version is not supported.")
        decision_id = _safe_text(value["decision_id"], maximum=20, required=True).lower()
        if not FINAL_REVIEW_ID_PATTERN.fullmatch(decision_id):
            raise ValueError("Final-review decision ID has an invalid format.")
        plan_sha256 = _safe_text(value["plan_sha256"], maximum=64, required=True).lower()
        if not PLAN_SHA256_PATTERN.fullmatch(plan_sha256):
            raise ValueError("Final-review plan hash must be a 64-character hex digest.")
        decision = _safe_text(value["decision"], maximum=20, required=True).lower()
        if decision not in FINAL_REVIEW_DECISIONS:
            raise ValueError(f"Final-review decision is not supported: {decision}")
        completed_at, _ = _timestamp(value["completed_at"], "Final-review completed_at")
        artifact_paths = _bounded_strings(
            value["artifact_paths"], "Final-review artifact paths", maximum_items=1, maximum_length=100
        )
        if artifact_paths != (f"final-review/{decision_id}.json",):
            raise ValueError("Final-review artifact paths do not match the decision identity.")
        return cls(
            FINAL_REVIEW_SCHEMA_VERSION,
            decision_id,
            _safe_text(value["run_id"], maximum=100, required=True),
            _safe_text(value["team_task_id"], maximum=100, required=True),
            _safe_text(value["approval_id"], maximum=110, required=True),
            plan_sha256,
            decision,
            _safe_text(value["note"], maximum=MAX_FINAL_REVIEW_NOTE_LENGTH),
            _safe_text(value["actor"], maximum=100, required=True),
            completed_at,
            artifact_paths,
        )

    @property
    def outcome(self) -> str:
        """Mission outcome this decision produces: completed or blocked."""
        return "completed" if self.decision == "accepted" else "blocked"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "team_task_id": self.team_task_id,
            "approval_id": self.approval_id,
            "plan_sha256": self.plan_sha256,
            "decision": self.decision,
            "note": self.note,
            "actor": self.actor,
            "completed_at": self.completed_at,
            "artifact_paths": list(self.artifact_paths),
        }
