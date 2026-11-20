"""Health graphs: service health over time and dependency trees.

Snapshots are health reports recorded over time (see ``health --record``)
and stored as JSONL under the observability directory. The graphs built
from them are read-only views: per-section timelines rendered as ASCII
sparklines, and a dependency tree showing what Orion's health depends on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from orion.application.observability.health import (
    HEALTH_SECTIONS,
    HealthCheck,
    HealthReport,
    HealthState,
)

SNAPSHOT_FILE_NAME = "health-snapshots.jsonl"
MAX_SNAPSHOTS = 1_000
DEFAULT_SNAPSHOT_LIMIT = 100
MAX_SPARKLINE_LENGTH = 80

# One ASCII character per health state for the timeline sparkline.
_STATE_CHARS = {
    HealthState.OK: "=",
    HealthState.WARNING: "~",
    HealthState.ERROR: "X",
    HealthState.UNKNOWN: "?",
}


@dataclass(frozen=True)
class HealthSnapshotHistory:
    """Health snapshots in chronological order, plus load warnings."""

    snapshots: tuple[HealthReport, ...] = ()
    skipped: int = 0
    total: int = 0


class HealthSnapshotStore:
    """Append-only JSONL storage for health-check snapshots."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._path = self._root / SNAPSHOT_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def append(self, report: HealthReport) -> Path:
        """Record one snapshot, keeping at most MAX_SNAPSHOTS lines."""
        self._root.mkdir(parents=True, exist_ok=True)
        line = json.dumps(report.to_dict(), sort_keys=True) + "\n"
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(line)
        self._prune()
        return self._path

    def load(self, limit: int = DEFAULT_SNAPSHOT_LIMIT) -> HealthSnapshotHistory:
        """Load snapshots in chronological order (oldest first)."""
        if not self._path.is_file():
            return HealthSnapshotHistory()
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return HealthSnapshotHistory()
        snapshots: list[HealthReport] = []
        skipped = 0
        for line in lines:
            report = _parse_snapshot(line)
            if report is None:
                skipped += 1
                continue
            snapshots.append(report)
        total = len(snapshots)
        if limit > 0:
            snapshots = snapshots[-limit:]
        return HealthSnapshotHistory(tuple(snapshots), skipped, total)

    def _prune(self) -> None:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) <= MAX_SNAPSHOTS:
            return
        kept = lines[-MAX_SNAPSHOTS:]
        self._path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _parse_snapshot(line: str) -> HealthReport | None:
    """Parse one snapshot line, returning None when it is malformed."""
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    generated_at = data.get("generated_at")
    raw_checks = data.get("checks")
    if not isinstance(generated_at, str) or not isinstance(raw_checks, list):
        return None
    checks: list[HealthCheck] = []
    for raw in raw_checks:
        if not isinstance(raw, dict):
            return None
        try:
            state = HealthState(str(raw.get("state", "")))
        except ValueError:
            return None
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            return None
        checks.append(
            HealthCheck(
                name,
                state,
                str(raw.get("detail", "")),
                dict(raw.get("data", {})) if isinstance(raw.get("data"), dict) else {},
            )
        )
    return HealthReport(generated_at=generated_at, checks=tuple(checks))


@dataclass(frozen=True)
class SectionTimeline:
    """One section's health over time, as a sparkline plus counts."""

    section: str
    sparkline: str
    counts: Mapping[str, int]
    transitions: int
    first_at: str
    last_at: str
    snapshots: int

    def to_dict(self) -> dict[str, object]:
        return {
            "section": self.section,
            "sparkline": self.sparkline,
            "counts": dict(self.counts),
            "transitions": self.transitions,
            "first_at": self.first_at,
            "last_at": self.last_at,
            "snapshots": self.snapshots,
        }


@dataclass(frozen=True)
class HealthGraphs:
    """Timelines for the overall result and every recorded section."""

    overall: SectionTimeline
    sections: tuple[SectionTimeline, ...] = ()
    skipped: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "overall": self.overall.to_dict(),
            "sections": [section.to_dict() for section in self.sections],
            "skipped": self.skipped,
        }


def build_timelines(history: HealthSnapshotHistory) -> HealthGraphs:
    """Build overall and per-section timelines from snapshots."""
    snapshots = history.snapshots
    overall_states = [report.overall for report in snapshots]
    overall = _timeline("overall", overall_states, snapshots)
    sections: list[SectionTimeline] = []
    for name in HEALTH_SECTIONS:
        states: list[HealthState] = []
        present: list[HealthReport] = []
        for report in snapshots:
            check = next(
                (item for item in report.checks if item.name == name), None
            )
            if check is not None:
                states.append(check.state)
                present.append(report)
        if states:
            sections.append(_timeline(name, states, present))
    return HealthGraphs(overall, tuple(sections), history.skipped)


def _timeline(
    name: str, states: list[HealthState], snapshots: list[HealthReport]
) -> SectionTimeline:
    counts: dict[str, int] = {state.value: 0 for state in HealthState}
    for state in states:
        counts[state.value] += 1
    transitions = sum(
        1
        for previous, current in zip(states, states[1:])
        if previous is not current
    )
    sparkline = _sparkline(states)
    return SectionTimeline(
        section=name,
        sparkline=sparkline,
        counts=counts,
        transitions=transitions,
        first_at=snapshots[0].generated_at if snapshots else "",
        last_at=snapshots[-1].generated_at if snapshots else "",
        snapshots=len(states),
    )


def _sparkline(states: list[HealthState]) -> str:
    """Render states as one character each, downsampled past the max width."""
    if not states:
        return ""
    if len(states) <= MAX_SPARKLINE_LENGTH:
        return "".join(_STATE_CHARS[state] for state in states)
    step = (len(states) + MAX_SPARKLINE_LENGTH - 1) // MAX_SPARKLINE_LENGTH
    sampled = states[::step]
    return "".join(_STATE_CHARS[state] for state in sampled)


@dataclass(frozen=True)
class DependencyNode:
    """One node in the dependency tree: a subsystem, provider, or service."""

    name: str
    kind: str
    state: HealthState
    detail: str = ""
    children: tuple["DependencyNode", ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "state": self.state.value,
            "detail": self.detail,
            "children": [child.to_dict() for child in self.children],
        }

    def render(self, prefix: str = "", *, last: bool = True) -> list[str]:
        """Render the subtree as ASCII tree lines."""
        lines = [f"{prefix}+-- {self.name} [{self.state.value.upper()}]"]
        if self.detail:
            lines[-1] += f" - {self.detail}"
        child_prefix = prefix + ("    " if last else "|   ")
        for index, child in enumerate(self.children):
            lines.extend(
                child.render(child_prefix, last=index == len(self.children) - 1)
            )
        return lines


def build_dependency_graph(report: HealthReport) -> DependencyNode:
    """Build Orion's dependency tree from a health report."""
    checks = {check.name: check for check in report.checks}
    children: list[DependencyNode] = []
    for name in HEALTH_SECTIONS:
        check = checks.get(name)
        if check is None:
            continue
        children.append(_dependency_child(check))
    return DependencyNode("orion", "root", report.overall, "", tuple(children))


def _dependency_child(check: HealthCheck) -> DependencyNode:
    data = check.data if isinstance(check.data, Mapping) else {}
    if check.name == "providers":
        providers = data.get("providers") if isinstance(data, Mapping) else None
        children = tuple(
            _provider_node(row)
            for row in providers
            if isinstance(row, Mapping)
        ) if isinstance(providers, list) else ()
        return DependencyNode(
            "providers", "subsystem", check.state, check.detail, children
        )
    if check.name == "services":
        services = data.get("services") if isinstance(data, Mapping) else None
        children = tuple(
            _service_node(name, entry)
            for name, entry in sorted(services.items())
            if isinstance(entry, Mapping)
        ) if isinstance(services, dict) else ()
        return DependencyNode(
            "services", "subsystem", check.state, check.detail, children
        )
    return DependencyNode(check.name, "subsystem", check.state, check.detail)


def _provider_node(row: Mapping[str, Any]) -> DependencyNode:
    key = str(row.get("key", ""))
    configured = bool(row.get("configured", False))
    active = bool(row.get("active", False))
    model = str(row.get("model", "") or "")
    if configured:
        state = HealthState.OK
    elif active:
        state = HealthState.ERROR
    else:
        state = HealthState.WARNING
    detail = "configured" if configured else "not configured"
    if active:
        detail += ", active"
    if model:
        detail += f", model {model}"
    return DependencyNode(key, "provider", state, detail)


def _service_node(name: str, entry: Mapping[str, Any]) -> DependencyNode:
    try:
        state = HealthState(str(entry.get("state", "")))
    except ValueError:
        state = HealthState.UNKNOWN
    return DependencyNode(
        name, "service", state, str(entry.get("message", "") or "")
    )
