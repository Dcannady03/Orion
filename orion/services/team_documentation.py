"""Bounded, read-only documentation review for completed AI Team runs."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from orion.services.team_roles import ROLE_SPEC_BY_NAME, ResolvedTeamRole, TeamRoleSnapshot
from orion.services.team_validation import AutomaticValidationService, ValidationAttempt
from orion.services.workspace import WorkspaceCapabilities
from orion.services.workspace_snapshot import (
    WorkspaceBaseline,
    WorkspaceChangeSet,
    WorkspaceSnapshotService,
)


DOCUMENTATION_SCHEMA_VERSION = 1
DOCUMENTATION_ID_PATTERN = re.compile(r"documentation-[0-9]{4}")
RUN_ID_PATTERN = re.compile(r"run-[a-z0-9-]{6,95}")
TEAM_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,80}")
APPROVAL_ID_PATTERN = re.compile(r"approval-[a-z0-9-]{6,95}")
DOCUMENTATION_STATUSES = frozenset({
    "passed", "warnings", "failed", "not_required", "unavailable", "error",
})
DOCUMENTATION_CHECK_STATUSES = frozenset({"passed", "warning", "failed", "skipped"})
CLASSIFICATION_DECISIONS = frozenset({
    "confirm_required", "challenge_not_required", "challenge_required",
    "deterministic_not_required", "provider_unavailable", "review_error",
})
FINDING_SEVERITIES = frozenset({"info", "warning", "error"})
FINDING_CATEGORIES = frozenset({
    "missing", "inaccurate", "stale", "inconsistent", "broken-link",
    "undocumented-command", "help-mismatch", "configuration", "safety",
    "architecture", "changelog", "versioning", "example", "coverage",
})
CLASSIFICATION_CATEGORIES = frozenset({
    "command", "configuration", "provider", "service", "plugin", "center",
    "role", "agent", "execution-engine", "setup", "safety", "public-api",
    "artifact-format", "troubleshooting", "release", "architecture",
    "user-output", "platform", "feature", "documentation-only",
})
DENIED_PARTS = frozenset({".git", ".codex", ".agents", ".orion", "vault", "tokens"})
DENIED_NAMES = frozenset({
    ".env", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml",
    "vault.yaml", "vault.yml", "google-gmail-token.json", "google-calendar-token.json",
    "microsoft-mail-token.json", "microsoft-calendar-token.json",
})
DENIED_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".jks"})
METADATA_IGNORED_PARTS = frozenset({
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    ".venv", "venv", "env", "node_modules", "bower_components", "build", "dist",
    "target", "coverage", "htmlcov",
})
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:ghp|github_pat|xox[abprs])_[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization)\b"
        r"\s*[:=]\s*\S+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}"),
)
ROOT_DOCUMENTS = (
    "docs/USER_GUIDE.md",
    "README.md",
    "CHANGELOG.md",
)
CATEGORY_DOCUMENTS = {
    "command": ("docs/AI_TEAM.md",),
    "configuration": ("docs/CONFIGURATION.md",),
    "provider": ("docs/CONFIGURATION.md", "docs/SERVICES.md"),
    "service": ("docs/SERVICES.md", "docs/ARCHITECTURE.md"),
    "plugin": ("docs/SERVICES.md",),
    "center": ("docs/SERVICES.md",),
    "role": ("docs/AI_TEAM.md", "docs/ARCHITECTURE.md"),
    "agent": ("docs/AI_TEAM.md",),
    "execution-engine": ("docs/EXECUTION_ENGINES.md", "docs/CODEX_BRIDGE.md"),
    "setup": ("docs/USER_GUIDE.md", "README.md"),
    "safety": ("docs/USER_GUIDE.md", "docs/AI_TEAM.md", "docs/CODEX_BRIDGE.md", "docs/ARCHITECTURE.md"),
    "public-api": ("docs/ARCHITECTURE.md", "docs/SERVICES.md"),
    "artifact-format": ("docs/CODEX_BRIDGE.md", "docs/ARCHITECTURE.md"),
    "troubleshooting": ("docs/USER_GUIDE.md",),
    "release": ("CHANGELOG.md",),
    "architecture": ("docs/ARCHITECTURE.md", "docs/SERVICES.md"),
    "user-output": ("docs/USER_GUIDE.md",),
    "platform": ("docs/USER_GUIDE.md", "README.md"),
    "feature": ("docs/USER_GUIDE.md", "docs/ROADMAP.md"),
    "documentation-only": ("docs/USER_GUIDE.md",),
}


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
        raise ValueError("Documentation review text fields must be strings.")
    text = value.strip()
    if required and not text:
        raise ValueError("Documentation review text fields cannot be empty.")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    text = " ".join(text.split())
    return text[:maximum]


def _bounded_strings(
    value: Any,
    label: str,
    *,
    maximum_items: int = 200,
    maximum_length: int = 1_000,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ValueError(f"{label} must be a bounded JSON array.")
    return tuple(
        _safe_text(item, maximum=maximum_length, required=True)
        for item in value
    )


def _timestamp(value: Any, label: str) -> tuple[str, datetime]:
    text = _safe_text(value, maximum=80, required=True)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset.")
    return text, parsed


def _duration(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Documentation review duration must be numeric.")
    result = float(value)
    if not math.isfinite(result) or result < 0 or result > 86_400:
        raise ValueError("Documentation review duration is outside its safe range.")
    return result


def _same_path(first: str | Path, second: str | Path) -> bool:
    return os.path.normcase(str(Path(first).expanduser().resolve())) == os.path.normcase(
        str(Path(second).expanduser().resolve())
    )


def _safe_relative_path(workspace: Path, value: str | Path) -> str:
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("Documentation paths must remain inside the approved workspace.")
    if (
        any(part.casefold() in DENIED_PARTS for part in relative.parts)
        or relative.name.casefold() in DENIED_NAMES
        or relative.suffix.casefold() in DENIED_SUFFIXES
    ):
        raise ValueError("Documentation Review cannot access Vault, OAuth, or credential data.")
    candidate = (workspace / relative).resolve()
    try:
        normalized = candidate.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("Documentation path escapes the approved workspace.") from exc
    return normalized.as_posix()


@dataclass(frozen=True)
class DocumentationClassification:
    required: bool
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]
    categories: tuple[str, ...]

    @classmethod
    def from_value(cls, value: Any) -> "DocumentationClassification":
        value = _exact_mapping(
            value,
            {"required", "reasons", "evidence", "categories"},
            "Documentation requirement classification",
        )
        if not isinstance(value["required"], bool):
            raise ValueError("Documentation requirement must be true or false.")
        categories = _bounded_strings(
            value["categories"], "Documentation classification categories", maximum_items=30,
            maximum_length=50,
        )
        if any(item not in CLASSIFICATION_CATEGORIES for item in categories):
            raise ValueError("Documentation classification contains an unsupported category.")
        reasons = _bounded_strings(
            value["reasons"], "Documentation classification reasons", maximum_items=30,
        )
        evidence = _bounded_strings(
            value["evidence"], "Documentation classification evidence", maximum_items=60,
        )
        if value["required"] and not reasons:
            raise ValueError("Required documentation classification must include a reason.")
        return cls(value["required"], reasons, evidence, categories)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "reasons": list(self.reasons),
            "evidence": list(self.evidence),
            "categories": list(self.categories),
        }


@dataclass(frozen=True)
class DocumentationCheck:
    check_id: str
    name: str
    status: str
    summary: str
    documents: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Any) -> "DocumentationCheck":
        value = _exact_mapping(
            value, {"check_id", "name", "status", "summary", "documents"},
            "Documentation check",
        )
        check_id = _safe_text(value["check_id"], maximum=80, required=True).lower()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,79}", check_id):
            raise ValueError("Documentation check ID has an invalid format.")
        status = _safe_text(value["status"], maximum=20, required=True).lower()
        if status not in DOCUMENTATION_CHECK_STATUSES:
            raise ValueError(f"Documentation check status is not supported: {status}")
        return cls(
            check_id,
            _safe_text(value["name"], maximum=150, required=True),
            status,
            _safe_text(value["summary"], maximum=1_000, required=True),
            _bounded_strings(
                value["documents"], "Documentation check documents", maximum_items=50,
                maximum_length=500,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "documents": list(self.documents),
        }


@dataclass(frozen=True)
class DocumentationFinding:
    severity: str
    category: str
    document: str
    section: str
    finding: str
    implementation_evidence: str
    recommended_correction: str
    confidence: float
    blocks_passed: bool

    @classmethod
    def from_value(cls, value: Any) -> "DocumentationFinding":
        value = _exact_mapping(
            value,
            {
                "severity", "category", "document", "section", "finding",
                "implementation_evidence", "recommended_correction", "confidence",
                "blocks_passed",
            },
            "Documentation finding",
        )
        severity = _safe_text(value["severity"], maximum=20, required=True).lower()
        category = _safe_text(value["category"], maximum=40, required=True).lower()
        if severity not in FINDING_SEVERITIES:
            raise ValueError(f"Documentation finding severity is unsupported: {severity}")
        if category not in FINDING_CATEGORIES:
            raise ValueError(f"Documentation finding category is unsupported: {category}")
        confidence = value["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("Documentation finding confidence must be numeric.")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Documentation finding confidence must be between zero and one.")
        if not isinstance(value["blocks_passed"], bool):
            raise ValueError("Documentation finding blocks_passed must be true or false.")
        if severity == "info" and value["blocks_passed"]:
            raise ValueError("Informational documentation findings cannot block Passed.")
        document = _safe_text(value["document"], maximum=500, required=True)
        document_path = Path(document)
        if (
            document_path.is_absolute()
            or ".." in document_path.parts
            or any(part.casefold() in DENIED_PARTS for part in document_path.parts)
            or document_path.name.casefold() in DENIED_NAMES
            or document_path.suffix.casefold() in DENIED_SUFFIXES
        ):
            raise ValueError("Documentation finding documents must be workspace-relative.")
        return cls(
            severity,
            category,
            document,
            _safe_text(value["section"], maximum=200),
            _safe_text(value["finding"], maximum=1_000, required=True),
            _safe_text(value["implementation_evidence"], maximum=1_000, required=True),
            _safe_text(value["recommended_correction"], maximum=1_000, required=True),
            round(confidence, 4),
            value["blocks_passed"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "document": self.document,
            "section": self.section,
            "finding": self.finding,
            "implementation_evidence": self.implementation_evidence,
            "recommended_correction": self.recommended_correction,
            "confidence": self.confidence,
            "blocks_passed": self.blocks_passed,
        }


@dataclass(frozen=True)
class DocumentationUsage:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None

    @classmethod
    def from_value(cls, value: Any) -> "DocumentationUsage":
        value = _exact_mapping(
            value,
            {"provider", "model", "input_tokens", "output_tokens", "estimated_cost_usd"},
            "Documentation provider usage",
        )
        counts = (value["input_tokens"], value["output_tokens"])
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in counts):
            raise ValueError("Documentation token counts must be non-negative integers.")
        cost = value["estimated_cost_usd"]
        if cost is not None:
            if isinstance(cost, bool) or not isinstance(cost, (int, float)):
                raise ValueError("Documentation estimated cost must be numeric or null.")
            cost = float(cost)
            if not math.isfinite(cost) or cost < 0 or cost > 1_000_000:
                raise ValueError("Documentation estimated cost is outside its safe range.")
            cost = round(cost, 8)
        return cls(
            _safe_text(value["provider"], maximum=50),
            _safe_text(value["model"], maximum=200),
            counts[0],
            counts[1],
            cost,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass(frozen=True)
class DocumentationAttempt:
    schema_version: int
    attempt_id: str
    run_id: str
    team_task_id: str
    approval_id: str
    workspace_root: str
    workspace: WorkspaceCapabilities
    implementation_summary_reference: str
    validation_summary_reference: str
    classification: DocumentationClassification
    reviewer_requested: str
    reviewer_resolved: str
    provider: str
    model: str
    fallback: str
    fallback_reason: str
    classification_decision: str
    classification_reason: str
    known_commands: tuple[str, ...]
    configuration_changes: tuple[str, ...]
    documents_inspected: tuple[str, ...]
    checks: tuple[DocumentationCheck, ...]
    findings: tuple[DocumentationFinding, ...]
    status: str
    counts_by_severity: Mapping[str, int]
    counts_by_category: Mapping[str, int]
    started_at: str
    completed_at: str
    duration_seconds: float
    usage: DocumentationUsage
    safe_error_category: str
    safe_diagnostics: tuple[str, ...]
    artifact_paths: tuple[str, ...]

    @classmethod
    def from_value(cls, value: Any) -> "DocumentationAttempt":
        fields = {
            "schema_version", "attempt_id", "run_id", "team_task_id", "approval_id",
            "workspace_root", "workspace", "implementation_summary_reference",
            "validation_summary_reference", "classification", "reviewer_requested",
            "reviewer_resolved", "provider", "model", "fallback", "fallback_reason",
            "classification_decision", "classification_reason", "known_commands",
            "configuration_changes", "documents_inspected", "checks", "findings",
            "status", "counts_by_severity", "counts_by_category", "started_at",
            "completed_at", "duration_seconds", "usage", "safe_error_category",
            "safe_diagnostics", "artifact_paths",
        }
        value = _exact_mapping(value, fields, "Documentation review attempt")
        if value["schema_version"] != DOCUMENTATION_SCHEMA_VERSION:
            raise ValueError("Documentation review schema version is not supported.")
        attempt_id = _safe_text(value["attempt_id"], maximum=25, required=True).lower()
        if not DOCUMENTATION_ID_PATTERN.fullmatch(attempt_id):
            raise ValueError("Documentation attempt ID has an invalid format.")
        workspace = Path(_safe_text(value["workspace_root"], maximum=2_000, required=True)).expanduser()
        if not workspace.is_absolute():
            raise ValueError("Documentation review workspace must be absolute.")
        workspace_identity = WorkspaceCapabilities.from_value(value["workspace"])
        if not _same_path(workspace, workspace_identity.root):
            raise ValueError("Documentation workspace identity does not match its root.")
        status = _safe_text(value["status"], maximum=20, required=True).lower()
        if status not in DOCUMENTATION_STATUSES:
            raise ValueError(f"Documentation review status is unsupported: {status}")
        classification = DocumentationClassification.from_value(value["classification"])
        checks_value = value["checks"]
        findings_value = value["findings"]
        if not isinstance(checks_value, list) or len(checks_value) > 100:
            raise ValueError("Documentation checks must be a bounded JSON array.")
        if not isinstance(findings_value, list) or len(findings_value) > 100:
            raise ValueError("Documentation findings must be a bounded JSON array.")
        checks = tuple(DocumentationCheck.from_value(item) for item in checks_value)
        findings = tuple(DocumentationFinding.from_value(item) for item in findings_value)
        started_at, started = _timestamp(value["started_at"], "Documentation started_at")
        completed_at, completed = _timestamp(value["completed_at"], "Documentation completed_at")
        if completed < started:
            raise ValueError("Documentation completion cannot precede its start.")
        artifacts = _bounded_strings(
            value["artifact_paths"], "Documentation artifact paths", maximum_items=2,
            maximum_length=100,
        )
        expected = (
            f"documentation/{attempt_id}.json",
            f"documentation/{attempt_id}.log",
        )
        if artifacts != expected:
            raise ValueError("Documentation artifact paths do not match the attempt identity.")
        severity_counts = _exact_mapping(
            value["counts_by_severity"], set(FINDING_SEVERITIES),
            "Documentation severity counts",
        )
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in severity_counts.values()):
            raise ValueError("Documentation severity counts must be non-negative integers.")
        expected_severity = {
            severity: sum(item.severity == severity for item in findings)
            for severity in FINDING_SEVERITIES
        }
        if severity_counts != expected_severity:
            raise ValueError("Documentation severity counts do not match findings.")
        category_counts = value["counts_by_category"]
        if not isinstance(category_counts, dict) or len(category_counts) > len(FINDING_CATEGORIES):
            raise ValueError("Documentation category counts must be a bounded object.")
        if any(
            key not in FINDING_CATEGORIES
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
            for key, count in category_counts.items()
        ):
            raise ValueError("Documentation category counts are invalid.")
        expected_category = {
            category: sum(item.category == category for item in findings)
            for category in sorted({item.category for item in findings})
        }
        if category_counts != expected_category:
            raise ValueError("Documentation category counts do not match findings.")
        derived = (
            "failed" if any(item.severity == "error" for item in findings)
            else "warnings" if any(item.severity == "warning" or item.blocks_passed for item in findings)
            else "passed"
        )
        if status in {"passed", "warnings", "failed"} and status != derived:
            raise ValueError("Documentation status does not match its findings.")
        if status == "not_required" and classification.required:
            raise ValueError("Required documentation cannot be recorded as not required.")
        if status in {"unavailable", "error"} and not value["safe_error_category"]:
            raise ValueError("Unavailable and error documentation attempts require a safe category.")
        run_id = _safe_text(value["run_id"], maximum=100, required=True).lower()
        team_task_id = _safe_text(value["team_task_id"], maximum=100, required=True)
        approval_id = _safe_text(value["approval_id"], maximum=110, required=True).lower()
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError("Documentation run ID has an invalid format.")
        if not TEAM_TASK_ID_PATTERN.fullmatch(team_task_id):
            raise ValueError("Documentation task ID has an invalid format.")
        if not APPROVAL_ID_PATTERN.fullmatch(approval_id):
            raise ValueError("Documentation approval ID has an invalid format.")
        decision = _safe_text(value["classification_decision"], maximum=40, required=True).lower()
        if decision not in CLASSIFICATION_DECISIONS:
            raise ValueError("Documentation classification decision is unsupported.")
        return cls(
            DOCUMENTATION_SCHEMA_VERSION,
            attempt_id,
            run_id,
            team_task_id,
            approval_id,
            str(workspace.resolve()),
            workspace_identity,
            _safe_text(value["implementation_summary_reference"], maximum=100, required=True),
            _safe_text(value["validation_summary_reference"], maximum=100),
            classification,
            _safe_text(value["reviewer_requested"], maximum=300, required=True),
            _safe_text(value["reviewer_resolved"], maximum=300),
            _safe_text(value["provider"], maximum=50),
            _safe_text(value["model"], maximum=200),
            _safe_text(value["fallback"], maximum=300),
            _safe_text(value["fallback_reason"], maximum=500),
            decision,
            _safe_text(value["classification_reason"], maximum=1_000, required=True),
            _bounded_strings(value["known_commands"], "Known command changes", maximum_items=100),
            _bounded_strings(value["configuration_changes"], "Configuration changes", maximum_items=100),
            _bounded_strings(
                value["documents_inspected"], "Documents inspected", maximum_items=100,
                maximum_length=500,
            ),
            checks,
            findings,
            status,
            dict(severity_counts),
            dict(category_counts),
            started_at,
            completed_at,
            _duration(value["duration_seconds"]),
            DocumentationUsage.from_value(value["usage"]),
            _safe_text(value["safe_error_category"], maximum=80),
            _bounded_strings(
                value["safe_diagnostics"], "Documentation diagnostics", maximum_items=50,
            ),
            artifacts,
        )

    @property
    def review_status(self) -> str:
        return {
            "passed": "Documentation Passed",
            "warnings": "Documentation Warnings",
            "failed": "Documentation Failed",
            "not_required": "Documentation Not Required",
            "unavailable": "Documentation Unavailable",
            "error": "Documentation Error",
        }[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "attempt_id": self.attempt_id,
            "run_id": self.run_id,
            "team_task_id": self.team_task_id,
            "approval_id": self.approval_id,
            "workspace_root": self.workspace_root,
            "workspace": self.workspace.to_dict(),
            "implementation_summary_reference": self.implementation_summary_reference,
            "validation_summary_reference": self.validation_summary_reference,
            "classification": self.classification.to_dict(),
            "reviewer_requested": self.reviewer_requested,
            "reviewer_resolved": self.reviewer_resolved,
            "provider": self.provider,
            "model": self.model,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "classification_decision": self.classification_decision,
            "classification_reason": self.classification_reason,
            "known_commands": list(self.known_commands),
            "configuration_changes": list(self.configuration_changes),
            "documents_inspected": list(self.documents_inspected),
            "checks": [item.to_dict() for item in self.checks],
            "findings": [item.to_dict() for item in self.findings],
            "status": self.status,
            "counts_by_severity": dict(self.counts_by_severity),
            "counts_by_category": dict(self.counts_by_category),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "usage": self.usage.to_dict(),
            "safe_error_category": self.safe_error_category,
            "safe_diagnostics": list(self.safe_diagnostics),
            "artifact_paths": list(self.artifact_paths),
        }


@dataclass(frozen=True)
class DocumentationRequest:
    attempt_id: str
    run_id: str
    team_task_id: str
    approval_id: str
    workspace: WorkspaceCapabilities
    active_workspace: str
    changes: WorkspaceChangeSet
    implementation_result: Mapping[str, Any]
    plan_goal: str
    plan_steps: tuple[str, ...]
    validation: ValidationAttempt | None
    validation_reference: str
    baseline: WorkspaceBaseline
    blob_root: Path
    protected_baseline: Mapping[str, Any] | None
    artifact_paths: tuple[str, str]


@dataclass(frozen=True)
class _InventoryDocument:
    path: str
    changed: bool
    headings: tuple[str, ...]
    excerpt: str


@dataclass(frozen=True)
class _ModelReview:
    classification_decision: str
    classification_reason: str
    findings: tuple[DocumentationFinding, ...]


class DocumentationReviewService:
    """Classify and review documentation without granting file or command tools."""

    MAX_PROVIDER_RESPONSE_CHARS = 50_000
    SYSTEM_PROMPT = """You are Orion's Documentation Reviewer.
Review only the bounded, sanitized evidence supplied by Orion. Do not request or infer
credentials, unrestricted source, mailbox data, environment variables, or unrelated files.
Do not edit files, produce patches, run commands, use tools, approve work, or perform Git actions.
Return exactly one JSON object and no Markdown with these keys:
classification_decision: one of confirm_required, challenge_not_required, challenge_required;
classification_reason: concise string;
findings: array of strict finding objects with severity, category, document, section,
finding, implementation_evidence, recommended_correction, confidence, blocks_passed.
Allowed severities: info, warning, error. Allowed categories: missing, inaccurate, stale,
inconsistent, broken-link, undocumented-command, help-mismatch, configuration, safety,
architecture, changelog, versioning, example, coverage.
Use error only for material user-facing, safety, setup, command, configuration, or
developer-contract gaps. Return an empty findings array when coverage is complete."""

    def __init__(
        self,
        config_manager,
        role_registry,
        provider_factory,
        *,
        snapshot_service: WorkspaceSnapshotService | None = None,
        validation_service: AutomaticValidationService | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config_manager
        self.roles = role_registry
        self.provider_factory = provider_factory
        self.snapshots = snapshot_service or WorkspaceSnapshotService()
        self.validation_service = validation_service or AutomaticValidationService(
            config_manager,
            snapshot_service=self.snapshots,
            now=now,
        )
        self._now = now or (lambda: datetime.now(timezone.utc))

    def review(self, request: DocumentationRequest) -> DocumentationAttempt:
        started_wall = self._timestamp()
        started = time.monotonic()
        workspace = Path(request.workspace.root).expanduser().resolve()
        if not _same_path(workspace, request.active_workspace):
            raise PermissionError("Documentation Review workspace does not match the active workspace.")
        if not _same_path(request.changes.workspace_root, workspace):
            raise PermissionError("Documentation Review change artifacts belong to another workspace.")
        recorded_problem = self._recorded_state_problem(request.changes, workspace)
        if recorded_problem:
            raise ValueError(recorded_problem)
        metadata_before = self._workspace_metadata(workspace)
        protected_before = self.validation_service.protected_state(workspace)

        command_changes = self._command_changes(request, workspace)
        configuration_changes = self._configuration_changes(request, workspace)
        classification = self.classify(
            request.changes,
            request.plan_goal,
            request.plan_steps,
            str(request.implementation_result.get("summary", "")),
            command_changes=command_changes,
            configuration_changes=configuration_changes,
        )
        inventory = self._inventory(workspace, request.changes, classification)
        checks, findings = self._deterministic_checks(
            workspace,
            request.changes,
            classification,
            inventory,
            command_changes,
            configuration_changes,
        )
        reviewer = self._reviewer_status(request)

        if not classification.required:
            checks.append(DocumentationCheck(
                "requirement_classification",
                "Documentation requirement classification",
                "skipped",
                "The deterministic classifier found no user-facing or developer-contract impact.",
            ))
            return self._attempt(
                request,
                classification,
                reviewer,
                "not_required",
                "deterministic_not_required",
                "The change is internal and has no meaningful documentation impact.",
                command_changes,
                configuration_changes,
                inventory,
                checks,
                findings,
                started_wall,
                started,
            )

        if not reviewer.available or self.roles is None or self.provider_factory is None:
            return self._attempt(
                request,
                classification,
                reviewer,
                "unavailable",
                "provider_unavailable",
                reviewer.fallback_reason or "Documentation Reviewer provider/model is unavailable.",
                command_changes,
                configuration_changes,
                inventory,
                checks,
                findings,
                started_wall,
                started,
                safe_error_category="provider_unavailable",
            )

        prompt = self._provider_prompt(
            request,
            classification,
            inventory,
            checks,
            command_changes,
            configuration_changes,
        )
        candidates = self.roles.planning_candidates(
            "documentation",
            f"Documentation review for {request.plan_goal}",
        )
        model_review, selected, usage, failures = self._run_provider(prompt, candidates)
        findings.extend(model_review.findings)
        if model_review.classification_decision == "challenge_not_required":
            findings.append(DocumentationFinding(
                "warning",
                "coverage",
                "documentation",
                "Requirement classification",
                "The Documentation Reviewer challenged Orion's deterministic requirement classification.",
                "; ".join(classification.reasons),
                "A human should confirm whether the documented contract changed before accepting the run.",
                0.8,
                True,
            ))
        checks.append(DocumentationCheck(
            "planning_model_review",
            "Documentation Reviewer model assessment",
            "warning" if model_review.classification_decision == "challenge_not_required" else "passed",
            model_review.classification_reason,
            tuple(item.path for item in inventory),
        ))
        fallback_reason = selected.fallback_reason
        if failures:
            fallback_reason = (
                f"{'; '.join(failures)} failed; selected {selected.actual_assignment} through "
                f"{self.roles.routing_profile()} routing."
            )
        resolved = TeamRoleSnapshot.from_value({
            **reviewer.to_dict(),
            "actual_assignment": f"{usage.provider}:{usage.model}",
            "available": True,
            "fallback_reason": fallback_reason,
        })

        final_problem = self._recorded_state_problem(request.changes, workspace)
        metadata_after = self._workspace_metadata(workspace)
        protected_after = self.validation_service.protected_state(workspace)
        protected_problem = self._protected_problem(request.protected_baseline, workspace)
        if (
            final_problem
            or metadata_after != metadata_before
            or protected_after.get("directories") != protected_before.get("directories")
            or protected_problem
        ):
            checks.append(DocumentationCheck(
                "reviewer_read_only",
                "Documentation Reviewer read-only boundary",
                "failed",
                "Documentation Review detected an unexpected workspace or protected-metadata write.",
            ))
            findings.append(DocumentationFinding(
                "error", "safety", "workspace", "",
                "Documentation Review altered or observed altered workspace state.",
                "The post-review snapshot did not match the recorded implementation snapshot.",
                "Discard the review result, inspect the workspace, and rerun after restoring integrity.",
                1.0, True,
            ))
        else:
            checks.append(DocumentationCheck(
                "reviewer_read_only",
                "Documentation Reviewer read-only boundary",
                "passed",
                "Documentation Review left implementation, documentation, and protected metadata unchanged.",
            ))

        status = self._status(findings)
        return self._attempt(
            request,
            classification,
            resolved,
            status,
            model_review.classification_decision,
            model_review.classification_reason,
            command_changes,
            configuration_changes,
            inventory,
            checks,
            findings,
            started_wall,
            started,
            usage=usage,
        )

    def error(
        self,
        request: DocumentationRequest,
        category: str,
        message: str,
    ) -> DocumentationAttempt:
        started = self._timestamp()
        try:
            classification = self.classify(
                request.changes,
                request.plan_goal,
                request.plan_steps,
                str(request.implementation_result.get("summary", "")),
            )
        except (TypeError, ValueError):
            classification = DocumentationClassification(
                True,
                ("Documentation requirement could not be classified safely.",),
                ("Completed implementation artifact",),
                ("feature",),
            )
        return self._attempt(
            request,
            classification,
            self._reviewer_status(request),
            "error",
            "review_error",
            "Documentation Review stopped safely before producing a verdict.",
            (),
            (),
            (),
            (),
            (),
            started,
            time.monotonic(),
            safe_error_category=_safe_text(category, maximum=80, required=True),
            diagnostics=(_safe_text(message, maximum=1_000, required=True),),
        )

    def classify(
        self,
        changes: WorkspaceChangeSet,
        plan_goal: str,
        plan_steps: tuple[str, ...],
        implementation_summary: str,
        *,
        command_changes: tuple[str, ...] = (),
        configuration_changes: tuple[str, ...] = (),
    ) -> DocumentationClassification:
        paths = tuple(item.path for item in changes.changes)
        lowered = " ".join((plan_goal, *plan_steps, implementation_summary)).casefold()
        categories: set[str] = set()
        reasons: list[str] = []
        evidence: list[str] = []

        def add(category: str, reason: str, item: str) -> None:
            categories.add(category)
            if reason not in reasons:
                reasons.append(reason)
            if item not in evidence:
                evidence.append(item)

        if command_changes or any(path in {"orion/core/router.py", "orion/ui/console.py"} for path in paths):
            add("command", "User-facing command or interactive help behavior changed.",
                ", ".join(command_changes) if command_changes else "Command router/help changed")
        if configuration_changes or any(path in {"config/default.yaml", "orion/core/config.py"} for path in paths):
            add("configuration", "Configuration keys or defaults changed.",
                ", ".join(configuration_changes) if configuration_changes else "Configuration implementation changed")
        path_rules = (
            ("provider", ("provider",)),
            ("service", ("orion/services/",)),
            ("plugin", ("plugin",)),
            ("agent", ("agent",)),
            ("execution-engine", ("execution_engine", "execution-engines")),
            ("setup", ("setup", "install", "onboarding")),
        )
        for category, fragments in path_rules:
            matches = [path for path in paths if any(fragment in path.casefold() for fragment in fragments)]
            if matches:
                add(category, f"A {category.replace('-', ' ')} contract changed.", matches[0])
        word_rules = (
            ("role", (" role", "reviewer", "tester", "architect"), "AI Team role behavior changed."),
            ("center", (" center",), "A user-facing center changed."),
            ("safety", ("safety", "permission", "approval", "credential", "workspace", "sandbox", "vault"),
             "Safety, approval, credential, permission, or workspace behavior changed."),
            ("public-api", ("public api", "extension contract", "interface contract"), "A public API or extension contract changed."),
            ("artifact-format", ("artifact", "schema", "file location", "path moved"), "Artifact format or file location changed."),
            ("troubleshooting", ("troubleshoot", "failure scenario", "error message"), "A troubleshooting scenario changed."),
            ("release", ("release", "upgrade", "update behavior"), "Release or update behavior changed."),
            ("architecture", ("architecture", "workflow", "orchestrat"), "Architecture or workflow behavior changed."),
            ("user-output", ("output", "display", "prompt", "status"), "User-visible output changed."),
            ("platform", ("windows", "linux", "macos", "platform"), "Supported platform behavior changed."),
            ("feature", ("feature", "add ", "implement", "support"), "A feature was added, removed, or changed."),
        )
        for category, terms, reason in word_rules:
            if any(term in lowered for term in terms):
                add(category, reason, next((term.strip() for term in terms if term in lowered), category))

        markdown_paths = [path for path in paths if Path(path).suffix.casefold() == ".md"]
        non_test_paths = [
            path for path in paths
            if not path.casefold().startswith("tests/") and Path(path).suffix.casefold() != ".md"
        ]
        only_tests = bool(paths) and all(
            path.casefold().startswith("tests/") or Path(path).name.casefold().startswith("test_")
            for path in paths
        )
        internal_terms = (
            "internal refactor", "formatting only", "formatting-only", "internal comment",
            "dead code", "performance only", "performance-only", "no observable behavior",
        )
        explicitly_internal = any(term in lowered for term in internal_terms)
        if markdown_paths and not non_test_paths:
            add("documentation-only", "Documentation content itself changed.", markdown_paths[0])

        required = bool(categories) and not only_tests
        if explicitly_internal and not {
            "command", "configuration", "safety", "public-api", "artifact-format",
            "architecture", "setup", "release", "user-output", "platform",
        }.intersection(categories):
            required = False
            reasons = ["The change is an internal implementation detail with no observable contract impact."]
            evidence = [next(term for term in internal_terms if term in lowered)]
            categories.clear()
        elif only_tests:
            required = False
            reasons = ["Only test files changed; no product or documentation contract changed."]
            evidence = list(paths[:10])
            categories.clear()
        elif not required and paths:
            required = True
            add("feature", "Product files changed and require conservative documentation review.", paths[0])

        return DocumentationClassification.from_value({
            "required": required,
            "reasons": reasons,
            "evidence": evidence[:60],
            "categories": sorted(categories),
        })

    def documentation_log(self, attempt: DocumentationAttempt) -> str:
        lines = [
            f"Documentation Review {attempt.attempt_id}",
            f"Status: {attempt.status.upper()}",
            f"Reviewer: {attempt.reviewer_resolved or attempt.reviewer_requested}",
        ]
        for check in attempt.checks:
            lines.append(f"{check.status.upper():7} {check.name}: {check.summary}")
        for finding in attempt.findings:
            lines.append(
                f"{finding.severity.upper():7} {finding.document}: {finding.finding}"
            )
        for diagnostic in attempt.safe_diagnostics:
            lines.append(f"INFO    {diagnostic}")
        return "\n".join(_safe_text(line, maximum=1_500, required=True) for line in lines) + "\n"

    def _deterministic_checks(
        self,
        workspace: Path,
        changes: WorkspaceChangeSet,
        classification: DocumentationClassification,
        inventory: tuple[_InventoryDocument, ...],
        command_changes: tuple[str, ...],
        configuration_changes: tuple[str, ...],
    ) -> tuple[list[DocumentationCheck], list[DocumentationFinding]]:
        checks: list[DocumentationCheck] = []
        findings: list[DocumentationFinding] = []
        inventory_by_path = {item.path: item for item in inventory}
        changed_paths = {item.path.casefold() for item in changes.changes}

        applicable = set(ROOT_DOCUMENTS)
        for category in classification.categories:
            applicable.update(CATEGORY_DOCUMENTS.get(category, ()))
        missing = sorted(path for path in applicable if not (workspace / path).is_file())
        if missing:
            checks.append(DocumentationCheck(
                "coverage_inventory", "Applicable documentation inventory", "failed",
                f"{len(missing)} applicable documentation file(s) are missing.", tuple(missing),
            ))
            for path in missing:
                findings.append(self._finding(
                    "error", "missing", path,
                    "Applicable documentation file is missing.",
                    ", ".join(classification.reasons) or "Documentation is required.",
                    "Create the applicable document or update the coverage rules.",
                ))
        else:
            checks.append(DocumentationCheck(
                "coverage_inventory", "Applicable documentation inventory", "passed",
                f"Located {len(applicable)} applicable documentation file(s).",
                tuple(sorted(applicable, key=str.casefold)),
            ))

        markdown = [item.path for item in inventory if Path(item.path).suffix.casefold() == ".md"]
        if markdown:
            validation_checks = self.validation_service.markdown_checks(workspace, markdown)
            for item in validation_checks:
                status = {
                    "passed": "passed", "warning": "warning", "failed": "failed",
                    "error": "failed", "skipped": "skipped",
                }[item.status]
                checks.append(DocumentationCheck(
                    f"documentation_{item.check_id}", item.name, status, item.summary, item.files,
                ))
                if item.status in {"warning", "failed", "error"}:
                    severity = "error" if item.status in {"failed", "error"} else "warning"
                    category = "broken-link" if "link" in item.check_id else "inaccurate"
                    document = item.files[0] if item.files else "documentation"
                    findings.append(self._finding(
                        severity, category, document, item.summary,
                        "Deterministic Markdown validation.",
                        "Correct the Markdown structure or local link target.",
                    ))

        if command_changes:
            guide = self._read_document(workspace, "docs/USER_GUIDE.md", 1_000_000).casefold()
            help_text = self._read_document(workspace, "orion/core/router.py", 1_000_000).casefold()
            missing_guide = [item for item in command_changes if item.casefold() not in guide]
            missing_help = [
                item for item in command_changes
                if item.casefold() not in help_text
            ]
            checks.append(DocumentationCheck(
                "command_reference", "User Guide command coverage",
                "failed" if missing_guide else "passed",
                f"Missing command documentation: {', '.join(missing_guide)}"
                if missing_guide else f"Documented {len(command_changes)} changed command(s).",
                ("docs/USER_GUIDE.md",),
            ))
            for command in missing_guide:
                findings.append(self._finding(
                    "error", "undocumented-command", "docs/USER_GUIDE.md",
                    f"Changed command `{command}` is absent from the User Guide.",
                    f"Command inventory added or changed `{command}`.",
                    "Add the exact command syntax and purpose to the command reference.",
                ))
            checks.append(DocumentationCheck(
                "interactive_help", "Interactive help coverage",
                "failed" if missing_help else "passed",
                f"Interactive help is missing: {', '.join(missing_help)}"
                if missing_help else "Changed commands are discoverable through interactive help.",
                ("orion/core/router.py",),
            ))
            for command in missing_help:
                findings.append(self._finding(
                    "error", "help-mismatch", "orion/core/router.py",
                    f"Changed command `{command}` is absent from interactive help.",
                    f"Command completion contains `{command}`.",
                    "Add the command to the AI Team help section.",
                ))

        if configuration_changes:
            reference = self._read_document(workspace, "docs/CONFIGURATION.md", 1_000_000).casefold()
            missing_keys = [item for item in configuration_changes if item.casefold() not in reference]
            checks.append(DocumentationCheck(
                "configuration_reference", "Configuration reference coverage",
                "failed" if missing_keys else "passed",
                f"Undocumented configuration: {', '.join(missing_keys)}"
                if missing_keys else f"Documented {len(configuration_changes)} changed configuration key(s).",
                ("docs/CONFIGURATION.md",),
            ))
            for key in missing_keys:
                findings.append(self._finding(
                    "error", "configuration", "docs/CONFIGURATION.md",
                    f"Configuration key `{key}` is undocumented.",
                    f"Default configuration added or changed `{key}`.",
                    "Document the key, default, bounds, and operational effect.",
                ))

        changelog_changed = any(
            Path(item.path).name.casefold().startswith("changelog")
            for item in changes.changes
        )
        checks.append(DocumentationCheck(
            "changelog", "Changelog coverage", "passed" if changelog_changed else "failed",
            "An unreleased changelog entry is included in the implementation."
            if changelog_changed else "No changelog file changed for a documentation-required implementation.",
            tuple(
                item.path for item in changes.changes
                if Path(item.path).name.casefold().startswith("changelog")
            ),
        ))
        if not changelog_changed:
            findings.append(self._finding(
                "error", "changelog", "CHANGELOG.md",
                "The documentation-required implementation has no changelog update.",
                ", ".join(classification.reasons),
                "Add a concise unreleased milestone entry.",
            ))

        coverage_rules = (
            ("architecture", "docs/ARCHITECTURE.md", "architecture"),
            ("safety", "docs/USER_GUIDE.md", "safety"),
            ("feature", "docs/USER_GUIDE.md", "coverage"),
        )
        for category, document, finding_category in coverage_rules:
            if category not in classification.categories:
                continue
            changed = document.casefold() in changed_paths
            checks.append(DocumentationCheck(
                f"{category}_coverage", f"{category.title()} documentation coverage",
                "passed" if changed else "failed",
                f"{document} changed with the implementation."
                if changed else f"{document} was not updated for the {category} change.",
                (document,),
            ))
            if not changed:
                findings.append(self._finding(
                    "error", finding_category, document,
                    f"The {category} change is not reflected in {document}.",
                    next(
                        (item for item in classification.reasons if category in item.casefold()),
                        classification.reasons[0],
                    ),
                    f"Update {document} to match the implemented behavior.",
                ))

        if inventory_by_path:
            checks.append(DocumentationCheck(
                "bounded_inventory", "Bounded documentation context", "passed",
                f"Inspected {len(inventory_by_path)} bounded document(s); no source file bodies were uploaded.",
                tuple(inventory_by_path),
            ))
        return checks, findings

    def _inventory(
        self,
        workspace: Path,
        changes: WorkspaceChangeSet,
        classification: DocumentationClassification,
    ) -> tuple[_InventoryDocument, ...]:
        selected = list(ROOT_DOCUMENTS)
        for category in classification.categories:
            selected.extend(CATEGORY_DOCUMENTS.get(category, ()))
        selected.extend(
            item.path for item in changes.changes
            if Path(item.path).suffix.casefold() == ".md"
        )
        for candidate in sorted(workspace.glob("CHANGELOG*"), key=lambda item: item.name.casefold()):
            if candidate.is_file():
                selected.append(candidate.relative_to(workspace).as_posix())
        selected.extend(("docs/DEFINITION_OF_DONE.md", "CONTRIBUTING.md", "AGENTS.md"))
        changed = {item.path.casefold() for item in changes.changes}
        maximum = self._max_documents()
        documents: list[_InventoryDocument] = []
        for relative in dict.fromkeys(selected):
            if len(documents) >= maximum:
                break
            try:
                normalized = _safe_relative_path(workspace, relative)
            except ValueError:
                continue
            path = workspace / normalized
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            text = self._read_document(workspace, normalized, 20_000)
            headings = tuple(
                line.lstrip("#").strip()[:200]
                for line in text.splitlines()
                if line.startswith("#")
            )[:100]
            documents.append(_InventoryDocument(
                normalized,
                normalized.casefold() in changed,
                headings,
                text,
            ))
        return tuple(documents)

    def _provider_prompt(
        self,
        request: DocumentationRequest,
        classification: DocumentationClassification,
        inventory: tuple[_InventoryDocument, ...],
        checks: list[DocumentationCheck],
        command_changes: tuple[str, ...],
        configuration_changes: tuple[str, ...],
    ) -> str:
        context_limit = self._max_context_chars()
        remaining = context_limit
        documents = []
        for item in sorted(inventory, key=lambda row: (not row.changed, row.path.casefold())):
            excerpt = _safe_text(item.excerpt, maximum=min(4_000, remaining)) if remaining else ""
            remaining = max(0, remaining - len(excerpt))
            documents.append({
                "path": item.path,
                "changed": item.changed,
                "headings": list(item.headings[:30]),
                "bounded_excerpt": excerpt,
            })
        validation = request.validation
        validation_summary = None if validation is None else {
            "status": validation.status,
            "checks_passed": list(validation.checks_passed[:30]),
            "checks_failed": list(validation.checks_failed[:30]),
            "warnings": list(validation.warnings[:30]),
        }
        reported = {
            str(item.get("path", "")): _safe_text(str(item.get("summary", "")), maximum=500)
            for item in request.implementation_result.get("files_changed", [])
            if isinstance(item, dict)
        }
        changes = [
            {
                "path": item.path,
                "kind": item.kind,
                "binary": item.binary,
                "before_size": item.before_size,
                "after_size": item.after_size,
                "safe_summary": reported.get(item.path, ""),
            }
            for item in request.changes.changes[:200]
        ]
        context = {
            "approved_plan": {
                "goal": _safe_text(request.plan_goal, maximum=4_000, required=True),
                "steps": [_safe_text(item, maximum=1_000, required=True) for item in request.plan_steps[:100]],
            },
            "implementation_summary": _safe_text(
                str(request.implementation_result.get("summary", "")), maximum=2_000,
            ),
            "actual_file_changes": changes,
            "automatic_validation": validation_summary,
            "classification": classification.to_dict(),
            "known_command_changes": list(command_changes),
            "configuration_changes": list(configuration_changes),
            "deterministic_checks": [item.to_dict() for item in checks[:50]],
            "documentation_inventory": documents,
            "safety_note": (
                "This is bounded sanitized documentation context. No source bodies, raw diffs, "
                "credentials, environment variables, Vault data, OAuth data, or unrelated files are included."
            ),
        }
        prompt = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        if len(prompt) > context_limit + 20_000:
            raise ValueError("Documentation provider prompt exceeded its bounded context limit.")
        return prompt

    def _run_provider(
        self,
        prompt: str,
        candidates: tuple[ResolvedTeamRole, ...],
    ) -> tuple[_ModelReview, ResolvedTeamRole, DocumentationUsage, list[str]]:
        failures: list[str] = []
        for candidate in candidates:
            try:
                provider = self.provider_factory.create(candidate.provider)
                current_model = str(getattr(provider, "model", ""))
                if current_model != candidate.model:
                    if not hasattr(provider, "select_model"):
                        raise ValueError("Provider does not support role-specific model selection.")
                    provider.select_model(candidate.model)
                system = self.SYSTEM_PROMPT
                agent = self.roles.agent("documentation") if self.roles is not None else None
                if agent is not None:
                    instructions = _safe_text(str(agent.instructions), maximum=2_000)
                    system += (
                        f"\nConfigured worker: {_safe_text(str(agent.name), maximum=100)}. "
                        f"Bounded instructions: {instructions}\n"
                        "The Orion role contract and no-tools safety boundary override agent instructions."
                    )
                raw = provider.chat(prompt, system_prompt=system)
                if len(str(raw)) > self.MAX_PROVIDER_RESPONSE_CHARS:
                    raise ValueError("Documentation Reviewer response exceeded its bounded limit.")
                review = self._parse_model_review(raw)
                actual_model = str(getattr(provider, "model", candidate.model))
                input_tokens = self._estimate_tokens(system + "\n" + prompt)
                output_tokens = self._estimate_tokens(str(raw))
                return review, candidate, DocumentationUsage(
                    candidate.provider,
                    actual_model,
                    input_tokens,
                    output_tokens,
                    self._estimate_cost(candidate.provider, input_tokens, output_tokens),
                ), failures
            except (ConnectionError, KeyError, OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
                failures.append(f"{candidate.actual_assignment} ({type(exc).__name__})")
        categories = ", ".join(failures) or "no available provider"
        raise RuntimeError(f"Documentation Reviewer provider routing failed: {categories}.")

    def _parse_model_review(self, raw: str) -> _ModelReview:
        text = str(raw).strip()
        if text.startswith("```"):
            lines = text.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Documentation Reviewer returned invalid JSON.") from exc
        value = _exact_mapping(
            value,
            {"classification_decision", "classification_reason", "findings"},
            "Documentation Reviewer output",
        )
        decision = _safe_text(value["classification_decision"], maximum=40, required=True).lower()
        if decision not in {"confirm_required", "challenge_not_required", "challenge_required"}:
            raise ValueError("Documentation Reviewer classification decision is invalid.")
        findings_value = value["findings"]
        if not isinstance(findings_value, list) or len(findings_value) > self._max_findings():
            raise ValueError("Documentation Reviewer findings exceeded the configured bound.")
        return _ModelReview(
            decision,
            _safe_text(value["classification_reason"], maximum=1_000, required=True),
            tuple(DocumentationFinding.from_value(item) for item in findings_value),
        )

    def _attempt(
        self,
        request: DocumentationRequest,
        classification: DocumentationClassification,
        reviewer: TeamRoleSnapshot,
        status: str,
        decision: str,
        decision_reason: str,
        command_changes: tuple[str, ...],
        configuration_changes: tuple[str, ...],
        inventory: tuple[_InventoryDocument, ...],
        checks: tuple[DocumentationCheck, ...] | list[DocumentationCheck],
        findings: tuple[DocumentationFinding, ...] | list[DocumentationFinding],
        started_wall: str,
        started: float,
        *,
        usage: DocumentationUsage | None = None,
        safe_error_category: str = "",
        diagnostics: tuple[str, ...] = (),
    ) -> DocumentationAttempt:
        severity_order = {"error": 0, "warning": 1, "info": 2}
        findings = tuple(sorted(
            findings,
            key=lambda item: (severity_order[item.severity], item.document.casefold(), item.finding.casefold()),
        ))[:self._max_findings()]
        usage = usage or DocumentationUsage("", "", 0, 0, None)
        severity = {
            item: sum(finding.severity == item for finding in findings)
            for item in FINDING_SEVERITIES
        }
        categories = {
            category: sum(finding.category == category for finding in findings)
            for category in sorted({finding.category for finding in findings})
        }
        return DocumentationAttempt.from_value({
            "schema_version": DOCUMENTATION_SCHEMA_VERSION,
            "attempt_id": request.attempt_id,
            "run_id": request.run_id,
            "team_task_id": request.team_task_id,
            "approval_id": request.approval_id,
            "workspace_root": request.workspace.root,
            "workspace": request.workspace.to_dict(),
            "implementation_summary_reference": "implementation-result.json",
            "validation_summary_reference": request.validation_reference,
            "classification": classification.to_dict(),
            "reviewer_requested": reviewer.requested_assignment,
            "reviewer_resolved": reviewer.actual_assignment if reviewer.available else "",
            "provider": usage.provider,
            "model": usage.model,
            "fallback": reviewer.fallback,
            "fallback_reason": reviewer.fallback_reason,
            "classification_decision": decision,
            "classification_reason": decision_reason,
            "known_commands": list(command_changes),
            "configuration_changes": list(configuration_changes),
            "documents_inspected": [item.path for item in inventory],
            "checks": [item.to_dict() for item in checks],
            "findings": [item.to_dict() for item in findings],
            "status": status,
            "counts_by_severity": severity,
            "counts_by_category": categories,
            "started_at": started_wall,
            "completed_at": self._timestamp(),
            "duration_seconds": round(max(0.0, time.monotonic() - started), 6),
            "usage": usage.to_dict(),
            "safe_error_category": safe_error_category,
            "safe_diagnostics": list(diagnostics),
            "artifact_paths": list(request.artifact_paths),
        })

    def _reviewer_status(self, request: DocumentationRequest) -> TeamRoleSnapshot:
        if self.roles is None:
            return TeamRoleSnapshot(
                role="documentation",
                display_name="Documentation Reviewer",
                category="Validation role (planning model)",
                requested_assignment="active-planning-model",
                actual_assignment="active-planning-model",
                available=False,
                capability="Structured documentation review",
                fallback="Provider routing",
                fallback_reason="AI Team role registry is unavailable.",
                source="default",
            )
        return self.roles.status(
            "documentation",
            prompt=f"Documentation review for {request.plan_goal}",
        ).snapshot()

    def _command_changes(self, request: DocumentationRequest, workspace: Path) -> tuple[str, ...]:
        relative = "orion/ui/console.py"
        changed = {item.path for item in request.changes.changes}
        if relative not in changed:
            return ()
        before = self._baseline_text(request, relative)
        after = self._read_document(workspace, relative, 2_000_000)
        before_commands = self._base_commands(before)
        after_commands = self._base_commands(after)
        return tuple(sorted(after_commands - before_commands, key=str.casefold))

    def _configuration_changes(self, request: DocumentationRequest, workspace: Path) -> tuple[str, ...]:
        relative = "config/default.yaml"
        changed = {item.path for item in request.changes.changes}
        if relative not in changed:
            return ()
        before = self._yaml_values(self._baseline_text(request, relative))
        after = self._yaml_values(self._read_document(workspace, relative, 2_000_000))
        changed_keys = {
            key for key, value in after.items()
            if key not in before or before[key] != value
        }
        return tuple(sorted(changed_keys, key=str.casefold))

    @staticmethod
    def _base_commands(text: str) -> set[str]:
        if not text.strip():
            return set()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return set()
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == "BASE_COMMANDS" for target in targets):
                    try:
                        value = ast.literal_eval(node.value)
                    except (TypeError, ValueError):
                        return set()
                    return {
                        _safe_text(item, maximum=300, required=True)
                        for item in value
                        if isinstance(item, str)
                    }
        return set()

    @staticmethod
    def _yaml_values(text: str) -> dict[str, str]:
        if not text.strip():
            return {}
        try:
            value = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            return {}
        result: dict[str, str] = {}

        def visit(current: Any, prefix: str = "") -> None:
            if not isinstance(current, dict):
                if prefix:
                    result[prefix] = json.dumps(current, sort_keys=True, ensure_ascii=False)
                return
            for key, child in current.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                visit(child, path)

        visit(value)
        return result

    @staticmethod
    def _baseline_text(request: DocumentationRequest, relative: str) -> str:
        item = next((entry for entry in request.baseline.files if entry.path == relative), None)
        if item is None or item.binary or not item.blob:
            return ""
        path = Path(request.blob_root) / item.blob
        try:
            data = zlib.decompress(path.read_bytes())
        except (OSError, zlib.error):
            return ""
        if len(data) > 2_000_000:
            return ""
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _read_document(workspace: Path, relative: str, maximum: int) -> str:
        try:
            normalized = _safe_relative_path(workspace, relative)
            path = workspace / normalized
            if not path.is_file() or path.stat().st_size > maximum:
                return ""
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _protected_problem(self, baseline: Mapping[str, Any] | None, workspace: Path) -> str:
        if baseline is None:
            return ""
        try:
            current = self.validation_service.protected_state(workspace)
            if current.get("directories") != baseline.get("directories"):
                return "Unexpected protected workspace metadata change."
        except (OSError, TypeError, ValueError):
            return "Protected workspace metadata could not be verified safely."
        return ""

    @staticmethod
    def _recorded_state_problem(changes: WorkspaceChangeSet, workspace: Path) -> str:
        for change in changes.changes:
            try:
                relative = _safe_relative_path(workspace, change.path)
            except ValueError:
                return "Documentation Review refused a protected or credential-like changed path."
            path = workspace / relative
            if change.kind == "deleted":
                if path.exists():
                    return f"Recorded deleted file exists: {relative}"
                continue
            if not path.is_file():
                return f"Recorded implementation file is missing: {relative}"
            digest = hashlib.sha256()
            try:
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                return f"Recorded implementation file could not be verified: {relative}"
            if digest.hexdigest() != change.after_sha256:
                return f"Recorded implementation file changed before review: {relative}"
        return ""

    def _workspace_metadata(self, workspace: Path) -> tuple[tuple[str, int, int, int], ...]:
        """Capture bounded stat-only metadata without reading source or credential bodies."""
        maximum_files = int(self.config.get("codex_bridge.snapshot_max_files", 10_000))
        rows: list[tuple[str, int, int, int]] = []
        for directory, names, files in os.walk(workspace, topdown=True, followlinks=False):
            names[:] = [
                name for name in names
                if name.casefold() not in DENIED_PARTS
                and name.casefold() not in METADATA_IGNORED_PARTS
            ]
            root = Path(directory)
            for name in sorted(files, key=str.casefold):
                relative_path = (root / name).relative_to(workspace)
                if (
                    relative_path.name.casefold() in DENIED_NAMES
                    or relative_path.suffix.casefold() in DENIED_SUFFIXES
                ):
                    continue
                path = workspace / relative_path
                try:
                    details = path.lstat()
                except OSError as exc:
                    raise ValueError("Documentation workspace metadata could not be inspected safely.") from exc
                rows.append((
                    relative_path.as_posix(),
                    int(details.st_size),
                    int(details.st_mtime_ns),
                    int(details.st_mode),
                ))
                if len(rows) > maximum_files:
                    raise ValueError("Documentation workspace metadata exceeds the snapshot file limit.")
        return tuple(sorted(rows, key=lambda item: item[0].casefold()))

    @staticmethod
    def _finding(
        severity: str,
        category: str,
        document: str,
        finding: str,
        evidence: str,
        correction: str,
    ) -> DocumentationFinding:
        return DocumentationFinding.from_value({
            "severity": severity,
            "category": category,
            "document": document,
            "section": "",
            "finding": finding,
            "implementation_evidence": evidence,
            "recommended_correction": correction,
            "confidence": 1.0,
            "blocks_passed": severity != "info",
        })

    @staticmethod
    def _status(findings: list[DocumentationFinding]) -> str:
        if any(item.severity == "error" for item in findings):
            return "failed"
        if any(item.severity == "warning" or item.blocks_passed for item in findings):
            return "warnings"
        return "passed"

    def _estimate_cost(self, provider: str, input_tokens: int, output_tokens: int) -> float | None:
        input_rate = self.config.get(f"team.pricing.{provider}.input_per_million", None)
        output_rate = self.config.get(f"team.pricing.{provider}.output_per_million", None)
        if input_rate is None or output_rate is None:
            return None
        try:
            return round(
                (input_tokens * float(input_rate) + output_tokens * float(output_rate)) / 1_000_000,
                8,
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _estimate_tokens(value: str) -> int:
        return max(1, (len(value) + 3) // 4)

    def _max_documents(self) -> int:
        value = self.config.get("team.documentation_review.max_documents", 24)
        if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 100:
            raise ValueError("Documentation maximum documents must be between 5 and 100.")
        return value

    def _max_findings(self) -> int:
        value = self.config.get("team.documentation_review.max_findings", 30)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise ValueError("Documentation maximum findings must be between 1 and 100.")
        return value

    def _max_context_chars(self) -> int:
        value = self.config.get("team.documentation_review.max_diff_summary_chars", 24_000)
        if isinstance(value, bool) or not isinstance(value, int) or not 4_000 <= value <= 200_000:
            raise ValueError("Documentation context limit must be between 4,000 and 200,000 characters.")
        return value

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat(timespec="seconds")
