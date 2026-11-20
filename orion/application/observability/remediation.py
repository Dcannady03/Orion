"""Approval-based remediation for diagnosed issues.

The engine proposes fixes for problems the health check finds, and
applies them only with explicit approval. Every applied fix is verified
afterwards (a health re-check or a targeted re-read). Nothing is applied
without ``confirm=True``, and apply re-diagnoses first, so a stale plan
can never be applied to a system that has already healed.
"""

from __future__ import annotations

import datetime as _datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from orion.application.observability.backup import BackupService
from orion.application.observability.health import (
    HealthReport,
    HealthState,
)

KEEP_BACKUPS = 2
EVENT_FILE_MAX_AGE_DAYS = 90

PROPOSAL_SWITCH_PROVIDER = "switch-provider-ollama"
PROPOSAL_PRUNE_BACKUPS = "prune-old-backups"
PROPOSAL_PRUNE_EVENTS = "prune-old-events"
PROPOSAL_CREATE_WORKSPACE = "create-workspace"

KNOWN_PROPOSALS = (
    PROPOSAL_SWITCH_PROVIDER,
    PROPOSAL_PRUNE_BACKUPS,
    PROPOSAL_PRUNE_EVENTS,
    PROPOSAL_CREATE_WORKSPACE,
)


@dataclass(frozen=True)
class RemediationProposal:
    """One proposed fix, with its risk and reversibility stated up front."""

    proposal_id: str
    title: str
    problem: str
    fix: str
    risk: str
    reversible: bool
    details: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.proposal_id,
            "title": self.title,
            "problem": self.problem,
            "fix": self.fix,
            "risk": self.risk,
            "reversible": self.reversible,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class RemediationPlan:
    """The full set of proposed fixes for the current diagnosis."""

    generated_at: str
    proposals: tuple[RemediationProposal, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "proposals": [proposal.to_dict() for proposal in self.proposals],
        }


@dataclass(frozen=True)
class RemediationOutcome:
    """What happened when one proposal was applied (or refused)."""

    proposal_id: str
    applied: bool
    verified: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.proposal_id,
            "applied": self.applied,
            "verified": self.verified,
            "detail": self.detail,
        }


def _format_bytes(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} GiB"


class RemediationEngine:
    """Propose and, with explicit approval, apply fixes for health problems."""

    def __init__(
        self,
        *,
        paths,
        checker,
        config_manager: Any = None,
        provider_manager: Any = None,
        clock: Callable[[], _datetime.datetime] | None = None,
    ) -> None:
        self._paths = paths
        self._checker = checker
        self._config_manager = config_manager
        self._provider_manager = provider_manager
        self._clock = clock or _datetime.datetime.now

    def propose(self, report: HealthReport | None = None) -> RemediationPlan:
        """Build a remediation plan from a fresh (or supplied) health report."""
        report = report if report is not None else self._checker.check()
        checks = {check.name: check for check in report.checks}
        proposals: list[RemediationProposal] = []

        def _problem(name: str) -> bool:
            check = checks.get(name)
            return check is not None and check.state in {
                HealthState.WARNING,
                HealthState.ERROR,
            }

        if _problem("providers"):
            proposal = self._propose_switch_provider(checks["providers"].detail)
            if proposal is not None:
                proposals.append(proposal)
        if _problem("disk"):
            proposal = self._propose_prune_backups(checks["disk"].detail)
            if proposal is not None:
                proposals.append(proposal)
            proposal = self._propose_prune_events(checks["disk"].detail)
            if proposal is not None:
                proposals.append(proposal)
        if _problem("workspace"):
            proposal = self._propose_create_workspace(checks["workspace"].detail)
            if proposal is not None:
                proposals.append(proposal)
        return RemediationPlan(
            generated_at=self._clock().isoformat(),
            proposals=tuple(proposals),
        )

    def apply(self, proposal_id: str, *, confirm: bool) -> RemediationOutcome:
        """Apply one proposal, but only with explicit approval.

        Re-diagnoses first: a proposal whose problem is gone reports
        "no longer needed" instead of acting on stale information.
        """
        if not confirm:
            return RemediationOutcome(
                proposal_id,
                False,
                False,
                "Approval required: this fix was not applied.",
            )
        if proposal_id not in KNOWN_PROPOSALS:
            return RemediationOutcome(
                proposal_id,
                False,
                False,
                f"Unknown remediation proposal: {proposal_id}",
            )
        plan = self.propose()
        proposal = next(
            (item for item in plan.proposals if item.proposal_id == proposal_id),
            None,
        )
        if proposal is None:
            return RemediationOutcome(
                proposal_id,
                False,
                False,
                "No longer needed: the problem is gone.",
            )
        try:
            if proposal_id == PROPOSAL_SWITCH_PROVIDER:
                return self._apply_switch_provider()
            if proposal_id == PROPOSAL_PRUNE_BACKUPS:
                return self._apply_prune_backups()
            if proposal_id == PROPOSAL_PRUNE_EVENTS:
                return self._apply_prune_events()
            if proposal_id == PROPOSAL_CREATE_WORKSPACE:
                return self._apply_create_workspace()
        except Exception as exc:  # noqa: BLE001 - a failed fix must report, not crash
            return RemediationOutcome(
                proposal_id, False, False, f"{type(exc).__name__}: {exc}"
            )
        return RemediationOutcome(
            proposal_id, False, False, "No action is defined for this proposal."
        )

    def _propose_switch_provider(self, detail: str) -> RemediationProposal | None:
        if self._provider_manager is None:
            return None
        try:
            statuses = list(self._provider_manager.statuses())
        except Exception:  # noqa: BLE001 - cannot propose without provider state
            return None
        active = next(
            (item for item in statuses if bool(getattr(item, "active", False))),
            None,
        )
        active_key = str(getattr(active, "key", "")) if active is not None else ""
        if active_key == "ollama":
            return None
        return RemediationProposal(
            proposal_id=PROPOSAL_SWITCH_PROVIDER,
            title="Switch the active provider to Ollama",
            problem=detail,
            fix="Activate Ollama as the live provider. Reversible with "
            "`ai provider use <name>`.",
            risk="low",
            reversible=True,
            details={"current_provider": active_key or "none"},
        )

    def _propose_prune_backups(self, detail: str) -> RemediationProposal | None:
        service = BackupService(self._paths)
        backups = service.list_backups()
        if len(backups) <= KEEP_BACKUPS:
            return None
        doomed = backups[KEEP_BACKUPS:]
        total = sum(item.size_bytes for item in doomed)
        return RemediationProposal(
            proposal_id=PROPOSAL_PRUNE_BACKUPS,
            title="Delete old backup archives",
            problem=detail,
            fix=f"Delete {len(doomed)} old backup archive(s) "
            f"({_format_bytes(total)}), keeping the newest {KEEP_BACKUPS}. "
            "This cannot be undone.",
            risk="medium",
            reversible=False,
            details={
                "files": [item.name for item in doomed],
                "total_bytes": total,
                "keep": KEEP_BACKUPS,
            },
        )

    def _old_event_files(self) -> list[Path]:
        events_root = self._paths.events
        if not events_root.is_dir():
            return []
        cutoff = self._clock() - _datetime.timedelta(days=EVENT_FILE_MAX_AGE_DAYS)
        cutoff_ts = cutoff.timestamp()
        old: list[Path] = []
        for path in events_root.iterdir():
            if path.suffix != ".jsonl" or not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff_ts:
                    old.append(path)
            except OSError:
                continue
        return sorted(old)

    def _propose_prune_events(self, detail: str) -> RemediationProposal | None:
        old = self._old_event_files()
        if not old:
            return None
        total = 0
        for path in old:
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return RemediationProposal(
            proposal_id=PROPOSAL_PRUNE_EVENTS,
            title="Delete old event history files",
            problem=detail,
            fix=f"Delete {len(old)} event file(s) older than "
            f"{EVENT_FILE_MAX_AGE_DAYS} days ({_format_bytes(total)}). "
            "This cannot be undone.",
            risk="medium",
            reversible=False,
            details={
                "files": [path.name for path in old],
                "total_bytes": total,
                "max_age_days": EVENT_FILE_MAX_AGE_DAYS,
            },
        )

    def _workspace_path(self) -> Path | None:
        if self._config_manager is None:
            return None
        raw = str(self._config_manager.get("workspace.default_path", ".") or ".")
        return Path(raw).expanduser()

    def _propose_create_workspace(self, detail: str) -> RemediationProposal | None:
        path = self._workspace_path()
        if path is None or path.exists():
            return None
        return RemediationProposal(
            proposal_id=PROPOSAL_CREATE_WORKSPACE,
            title="Create the missing workspace directory",
            problem=detail,
            fix=f"Create the directory {path}. Reversible by deleting it.",
            risk="low",
            reversible=True,
            details={"path": str(path)},
        )

    def _apply_switch_provider(self) -> RemediationOutcome:
        active = self._provider_manager.activate("ollama")
        report = self._checker.check("providers")
        if report.overall is HealthState.OK:
            return RemediationOutcome(
                PROPOSAL_SWITCH_PROVIDER,
                True,
                True,
                f"Active provider is now {active.name()}; providers check is OK.",
            )
        return RemediationOutcome(
            PROPOSAL_SWITCH_PROVIDER,
            True,
            False,
            f"Activated {active.name()}, but the providers check is "
            f"still {report.overall.value}.",
        )

    def _apply_prune_backups(self) -> RemediationOutcome:
        service = BackupService(self._paths)
        backups = service.list_backups()
        doomed = backups[KEEP_BACKUPS:]
        deleted = 0
        freed = 0
        for item in doomed:
            try:
                freed += item.path.stat().st_size
                item.path.unlink()
                deleted += 1
            except OSError:
                continue
        remaining = service.list_backups()
        if len(remaining) <= KEEP_BACKUPS and deleted == len(doomed):
            return RemediationOutcome(
                PROPOSAL_PRUNE_BACKUPS,
                True,
                True,
                f"Deleted {deleted} backup(s), freed {_format_bytes(freed)}; "
                f"{len(remaining)} newest remain.",
            )
        return RemediationOutcome(
            PROPOSAL_PRUNE_BACKUPS,
            deleted > 0,
            False,
            f"Deleted {deleted} of {len(doomed)} backup(s); "
            f"{len(remaining)} remain.",
        )

    def _apply_prune_events(self) -> RemediationOutcome:
        old = self._old_event_files()
        deleted = 0
        freed = 0
        for path in old:
            try:
                freed += path.stat().st_size
                path.unlink()
                deleted += 1
            except OSError:
                continue
        remaining = self._old_event_files()
        if not remaining and deleted == len(old):
            return RemediationOutcome(
                PROPOSAL_PRUNE_EVENTS,
                True,
                True,
                f"Deleted {deleted} event file(s), freed {_format_bytes(freed)}.",
            )
        return RemediationOutcome(
            PROPOSAL_PRUNE_EVENTS,
            deleted > 0,
            False,
            f"Deleted {deleted} of {len(old)} event file(s); "
            f"{len(remaining)} old file(s) remain.",
        )

    def _apply_create_workspace(self) -> RemediationOutcome:
        path = self._workspace_path()
        if path is None:  # pragma: no cover - propose() already filtered this
            return RemediationOutcome(
                PROPOSAL_CREATE_WORKSPACE,
                False,
                False,
                "No workspace path is configured.",
            )
        path.mkdir(parents=True, exist_ok=True)
        report = self._checker.check("workspace")
        if report.overall is HealthState.OK:
            return RemediationOutcome(
                PROPOSAL_CREATE_WORKSPACE,
                True,
                True,
                f"Created workspace directory {path}; workspace check is OK.",
            )
        return RemediationOutcome(
            PROPOSAL_CREATE_WORKSPACE,
            True,
            False,
            f"Created {path}, but the workspace check is still "
            f"{report.overall.value}.",
        )
