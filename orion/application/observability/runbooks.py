"""Root-cause-oriented diagnostic runbooks.

A runbook is a structured procedure for one symptom: the checks to run
(mapped to real Orion commands), the likely root causes with the evidence
that confirms each one, and the fixes. Runbooks are descriptive only;
they never change anything. ``runbooks diagnose`` runs the health check
and suggests the runbooks matching the live findings.
"""

from __future__ import annotations

from dataclasses import dataclass

from orion.application.observability.health import (
    HealthReport,
    HealthState,
)


@dataclass(frozen=True)
class RunbookStep:
    """One diagnostic step: a command to run and what to look for."""

    title: str
    command: str
    look_for: str

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "command": self.command,
            "look_for": self.look_for,
        }


@dataclass(frozen=True)
class RunbookCause:
    """One likely root cause, its confirming evidence, and its fix."""

    cause: str
    evidence: str
    fix: str

    def to_dict(self) -> dict[str, str]:
        return {
            "cause": self.cause,
            "evidence": self.evidence,
            "fix": self.fix,
        }


@dataclass(frozen=True)
class Runbook:
    """A complete diagnostic procedure for one symptom."""

    runbook_id: str
    title: str
    symptom: str
    steps: tuple[RunbookStep, ...] = ()
    causes: tuple[RunbookCause, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.runbook_id,
            "title": self.title,
            "symptom": self.symptom,
            "steps": [step.to_dict() for step in self.steps],
            "causes": [cause.to_dict() for cause in self.causes],
        }


RUNBOOKS: tuple[Runbook, ...] = (
    Runbook(
        runbook_id="provider-unavailable",
        title="AI provider unavailable",
        symptom="Orion cannot reach its AI provider, or the provider is not configured.",
        steps=(
            RunbookStep(
                "Check provider status",
                "health providers",
                "Which provider is active, and whether it is configured.",
            ),
            RunbookStep(
                "Test the provider connection",
                "ai test <provider>",
                "Whether the credential verifies and models are visible.",
            ),
            RunbookStep(
                "Check recent provider errors",
                "logs --severity error --search provider",
                "Connection, credential, or model errors from the provider.",
            ),
            RunbookStep(
                "Check the environment report",
                "diagnostics",
                "The providers section: enabled, configured, and active flags.",
            ),
        ),
        causes=(
            RunbookCause(
                "API key is not configured",
                "health providers shows the active provider as not configured.",
                "Run `ai provider configure <provider>` to store and verify a key in Vault.",
            ),
            RunbookCause(
                "Ollama is not running",
                "Ollama is active but `ai test ollama` fails to connect.",
                "Start Ollama (`ollama serve` in a terminal), then run `ai test ollama`.",
            ),
            RunbookCause(
                "Provider is disabled",
                "diagnostics shows the provider as disabled.",
                "Enable the provider in configuration, then run `ai provider use <provider>`.",
            ),
            RunbookCause(
                "Wrong base URL",
                "`ai test` fails with a connection error to an unexpected address.",
                "Correct providers.<name>.base_url in configuration, then re-test.",
            ),
        ),
    ),
    Runbook(
        runbook_id="disk-full",
        title="Disk space warnings",
        symptom="The health check warns about low disk space on the user-data volume.",
        steps=(
            RunbookStep(
                "Check disk space",
                "health disk",
                "Free bytes and free percent on the user-data volume.",
            ),
            RunbookStep(
                "List backups",
                "backup list",
                "How many backup archives exist and how large they are.",
            ),
            RunbookStep(
                "Check the event history size",
                "diagnostics",
                "The events section: number of daily event files.",
            ),
            RunbookStep(
                "Check recent errors",
                "logs --severity error",
                "Disk-full or write-failed errors from any component.",
            ),
        ),
        causes=(
            RunbookCause(
                "Backup archives accumulated",
                "`backup list` shows many large archives.",
                "Delete old archives from the backups directory, keeping the newest.",
            ),
            RunbookCause(
                "Event history grew large",
                "diagnostics shows many daily event files.",
                "Archive or delete the oldest event files under the events directory.",
            ),
            RunbookCause(
                "Log files grew large",
                "Large files under the logs directory.",
                "Delete old log files; they are not needed for Orion to run.",
            ),
            RunbookCause(
                "Another application filled the volume",
                "Orion's own directories are small but free space is still low.",
                "Free space outside Orion (downloads, caches, other apps).",
            ),
        ),
    ),
    Runbook(
        runbook_id="events-unhealthy",
        title="Event Store warnings",
        symptom="The health check reports Event Store warnings, or event history looks wrong.",
        steps=(
            RunbookStep(
                "Check Event Store status",
                "health events",
                "The latest event timestamp and any warnings.",
            ),
            RunbookStep(
                "Search recent errors",
                "logs --severity error --source events",
                "Malformed-record or write errors from the Event Store.",
            ),
            RunbookStep(
                "Check disk space",
                "health disk",
                "Whether a full disk is blocking event writes.",
            ),
        ),
        causes=(
            RunbookCause(
                "Malformed event records",
                "logs reports malformed event records being ignored.",
                "The store skips bad lines automatically; no action is needed unless the count grows.",
            ),
            RunbookCause(
                "Disk full",
                "health disk shows very low free space.",
                "Follow the disk-full runbook, then confirm new events are recorded.",
            ),
            RunbookCause(
                "Event Store is disabled",
                "health events says the Event Store is disabled.",
                "Re-enable event recording in configuration and restart Orion.",
            ),
        ),
    ),
    Runbook(
        runbook_id="service-degraded",
        title="Service degraded or unavailable",
        symptom="A registered service reports degraded or unavailable status.",
        steps=(
            RunbookStep(
                "Check service status",
                "health services",
                "Which service is degraded and its status message.",
            ),
            RunbookStep(
                "Search the service's recent logs",
                "logs --search <service-name>",
                "Errors or warnings mentioning the service.",
            ),
            RunbookStep(
                "Check the dependency tree",
                "graphs dependencies",
                "The service's state in the context of everything it depends on.",
            ),
        ),
        causes=(
            RunbookCause(
                "A dependency is down",
                "The dependency tree shows a provider or service the degraded service needs is also down.",
                "Fix the dependency first using its own runbook, then re-check.",
            ),
            RunbookCause(
                "Credentials expired",
                "Logs mention authentication or credential errors for the service.",
                "Reconnect the service (for example `ai provider configure` for providers).",
            ),
            RunbookCause(
                "External service outage",
                "The service's own status page or a connection test shows it is unreachable.",
                "Wait for the external service to recover; Orion retries nothing automatically.",
            ),
        ),
    ),
    Runbook(
        runbook_id="startup-failure",
        title="Orion fails to start",
        symptom="Orion exits during startup or immediately after launching.",
        steps=(
            RunbookStep(
                "Check the configuration",
                "health config",
                "Whether the configuration loads and how many keys it has.",
            ),
            RunbookStep(
                "Check recent errors",
                "logs --severity error",
                "The last errors before the failure.",
            ),
            RunbookStep(
                "Build the environment report",
                "diagnostics",
                "Version, commit, platform, and provider state for an issue report.",
            ),
            RunbookStep(
                "Run the security audit",
                "audit",
                "Whether Vault or token storage is missing or unreadable.",
            ),
        ),
        causes=(
            RunbookCause(
                "Invalid configuration file",
                "health config reports the configuration is missing or empty.",
                "Restore config.yaml from a backup (`backup list`, then `backup import`).",
            ),
            RunbookCause(
                "Active provider cannot be created",
                "Logs show the default provider failing to initialize.",
                "Switch the default provider to ollama in configuration, then restart.",
            ),
            RunbookCause(
                "Corrupt Vault store",
                "audit reports the Vault file is missing or unreadable.",
                "Restore the Vault from a copy made outside Orion (Orion backups never include it); without one, re-enter the credentials; they cannot be recovered.",
            ),
        ),
    ),
    Runbook(
        runbook_id="slow-responses",
        title="Slow AI responses",
        symptom="Orion answers, but responses take much longer than usual.",
        steps=(
            RunbookStep(
                "Check overall health",
                "health",
                "Whether any section is degraded (disk, providers, services).",
            ),
            RunbookStep(
                "Check the active provider",
                "health providers",
                "Which provider and model are active.",
            ),
            RunbookStep(
                "Check the environment report",
                "diagnostics",
                "Platform details and provider state for comparison.",
            ),
            RunbookStep(
                "Check recent warnings",
                "logs --severity warning",
                "Timeouts or retries around the slow responses.",
            ),
        ),
        causes=(
            RunbookCause(
                "Ollama is loading a large model",
                "The first response after starting Ollama is slow, later ones are fast.",
                "Wait for the model to load; consider a smaller model for quick answers.",
            ),
            RunbookCause(
                "Cloud provider latency",
                "A cloud provider is active and its responses are slow.",
                "Try `ai provider use ollama` for local responses, or wait out the provider.",
            ),
            RunbookCause(
                "Disk pressure",
                "health disk shows very low free space.",
                "Follow the disk-full runbook; a full disk slows everything.",
            ),
        ),
    ),
)


def find_runbook(runbook_id: str) -> Runbook | None:
    """Return one runbook by id, or None when it does not exist."""
    normalized = (runbook_id or "").strip().lower()
    for runbook in RUNBOOKS:
        if runbook.runbook_id == normalized:
            return runbook
    return None


def match_runbooks(text: str) -> list[Runbook]:
    """Return runbooks matching a symptom description, best match first.

    Scores each runbook by how many of the query words appear in its
    title, symptom, and causes. Runbooks with no matching word are left out.
    """
    words = {
        word.strip(".,;:!?()\"'").lower()
        for word in (text or "").split()
    }
    words.discard("")
    if not words:
        return []
    scored: list[tuple[int, Runbook]] = []
    for runbook in RUNBOOKS:
        haystack = " ".join(
            [
                runbook.title,
                runbook.symptom,
                *(cause.cause for cause in runbook.causes),
            ]
        ).lower()
        score = sum(1 for word in words if word in haystack)
        if score:
            scored.append((score, runbook))
    scored.sort(key=lambda item: (-item[0], item[1].runbook_id))
    return [runbook for _, runbook in scored]


def suggest_runbooks(report: HealthReport) -> list[tuple[Runbook, str]]:
    """Suggest runbooks for the problems a health report shows.

    Returns (runbook, reason) pairs in a fixed order: providers, disk,
    events, services, config. An all-OK report suggests nothing.
    """
    checks = {check.name: check for check in report.checks}
    suggestions: list[tuple[Runbook, str]] = []

    def _problem(name: str) -> bool:
        check = checks.get(name)
        return check is not None and check.state in {
            HealthState.WARNING,
            HealthState.ERROR,
        }

    def _add(runbook_id: str, section: str) -> None:
        runbook = find_runbook(runbook_id)
        check = checks.get(section)
        if runbook is not None and check is not None:
            suggestions.append(
                (
                    runbook,
                    f"Health check: {section} is {check.state.value} - {check.detail}",
                )
            )

    if _problem("providers"):
        _add("provider-unavailable", "providers")
    if _problem("disk"):
        _add("disk-full", "disk")
    if _problem("events"):
        _add("events-unhealthy", "events")
    if _problem("services"):
        _add("service-degraded", "services")
    if _problem("config"):
        _add("startup-failure", "config")
    return suggestions
