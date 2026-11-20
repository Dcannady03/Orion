"""Explicit retry with end-to-end verification.

Retries are always explicit and bounded: the user runs ``retry <operation>``
and Orion makes a small number of foreground attempts, then verifies the
result end to end. Nothing here retries automatically in the background,
and Mission operations keep their existing no-automatic-retry policy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

DEFAULT_MAX_ATTEMPTS = 3
MAX_ATTEMPTS_LIMIT = 5

AttemptFn = Callable[[], "RetryOutcome"]
VerifyFn = Callable[["RetryOutcome"], "RetryOutcome"]


@dataclass(frozen=True)
class RetryPolicy:
    """How many attempts a retry makes, and how long it waits between them."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > MAX_ATTEMPTS_LIMIT:
            raise ValueError(
                f"max_attempts must be between 1 and {MAX_ATTEMPTS_LIMIT}"
            )
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds must not be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "max_attempts": self.max_attempts,
            "delay_seconds": self.delay_seconds,
        }


@dataclass(frozen=True)
class RetryOutcome:
    """The result of one attempt, plus evidence for the verification step."""

    ok: bool
    detail: str
    evidence: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class RetryAttempt:
    """One recorded attempt: its number, outcome, and duration."""

    attempt: int
    ok: bool
    detail: str
    duration_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "ok": self.ok,
            "detail": self.detail,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True)
class RetryResult:
    """The full record of a retry: every attempt plus verification."""

    operation: str
    target: str
    attempts: tuple[RetryAttempt, ...]
    verified: bool
    verification: str

    @property
    def succeeded(self) -> bool:
        """True when the last attempt succeeded."""
        return bool(self.attempts) and self.attempts[-1].ok

    @property
    def ok(self) -> bool:
        """True only when an attempt succeeded AND verification passed."""
        return self.succeeded and self.verified

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "target": self.target,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "succeeded": self.succeeded,
            "verified": self.verified,
            "verification": self.verification,
        }


class RetryRunner:
    """Run a bounded, explicit retry and verify the result end to end.

    The attempt function is called until it succeeds or the policy's
    attempt budget runs out. A failed attempt never crashes the retry:
    exceptions are recorded as failed attempts. When an attempt succeeds,
    the verify function runs a separate end-to-end check against the
    successful attempt's evidence.
    """

    def __init__(
        self,
        *,
        policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._policy = policy or RetryPolicy()
        self._sleep = sleep or time.sleep

    def run(
        self,
        operation: str,
        target: str,
        *,
        attempt_fn: AttemptFn,
        verify_fn: VerifyFn | None = None,
    ) -> RetryResult:
        attempts: list[RetryAttempt] = []
        last_outcome: RetryOutcome | None = None
        for number in range(1, self._policy.max_attempts + 1):
            started = time.monotonic()
            try:
                outcome = attempt_fn()
            except Exception as exc:  # noqa: BLE001 - record, never crash
                outcome = RetryOutcome(False, f"{type(exc).__name__}: {exc}")
            duration = time.monotonic() - started
            attempts.append(
                RetryAttempt(number, outcome.ok, outcome.detail, round(duration, 3))
            )
            last_outcome = outcome
            if outcome.ok:
                break
            if number < self._policy.max_attempts and self._policy.delay_seconds:
                self._sleep(self._policy.delay_seconds)
        if last_outcome is None or not last_outcome.ok:
            return RetryResult(
                operation,
                target,
                tuple(attempts),
                False,
                "Operation did not succeed; nothing to verify.",
            )
        if verify_fn is None:
            return RetryResult(
                operation,
                target,
                tuple(attempts),
                True,
                "No separate verification step; the successful attempt is the verification.",
            )
        try:
            verification = verify_fn(last_outcome)
        except Exception as exc:  # noqa: BLE001 - a failed check is not verified
            verification = RetryOutcome(False, f"{type(exc).__name__}: {exc}")
        return RetryResult(
            operation,
            target,
            tuple(attempts),
            verification.ok,
            verification.detail,
        )
