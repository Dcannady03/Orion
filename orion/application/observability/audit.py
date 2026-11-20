"""Read-only security audit: permissions, secrets, exposed interfaces.

Checks the Vault and token storage, scans configuration and recent events
for leaked secrets, reports outbound interfaces (providers, Discord,
plugins) and explicit approval policies. The report never contains secret
values: findings name files, keys, and counts only.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from orion.application.observability.health import HealthState
from orion.core.paths import OrionPaths

MAX_SCAN_BYTES = 1024 * 1024

# Secret-like value patterns. Matches are counted, never reported verbatim.
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{10,}"),
    re.compile(r"ghp_[A-Za-z0-9]{10,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
)
_SECRET_KEY_PATTERN = re.compile(
    r"(?m)^\s*(api_key|apikey|secret|token|password|passwd)\s*:\s*\S+"
)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


@dataclass(frozen=True)
class AuditFinding:
    """One audit observation with a severity and a safe detail."""

    check: str
    category: str
    state: HealthState
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "check": self.check,
            "category": self.category,
            "state": self.state.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AuditReport:
    """An immutable snapshot of every audit finding."""

    generated_at: str
    findings: tuple[AuditFinding, ...] = ()

    @property
    def overall(self) -> HealthState:
        order = {
            HealthState.OK: 0,
            HealthState.UNKNOWN: 1,
            HealthState.WARNING: 2,
            HealthState.ERROR: 3,
        }
        worst = HealthState.OK
        for finding in self.findings:
            if order[finding.state] > order[worst]:
                worst = finding.state
        return worst

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "overall": self.overall.value,
            "findings": [finding.to_dict() for finding in self.findings],
        }


class SecurityAudit:
    """Run read-only security checks without mutating runtime state.

    Every check is isolated: a failing check reports UNKNOWN instead of
    hiding the remaining checks. No finding ever contains a secret value.
    """

    def __init__(
        self,
        paths: OrionPaths,
        *,
        config_manager: Any = None,
        provider_manager: Any = None,
        services: Any = None,
        plugin_manager: Any = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(paths, OrionPaths):
            raise TypeError("Security audit requires OrionPaths.")
        self._paths = paths
        self._config_manager = config_manager
        self._provider_manager = provider_manager
        self._services = services
        self._plugin_manager = plugin_manager
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run(self) -> AuditReport:
        """Run every audit check."""
        checks = (
            self._check_vault_file,
            self._check_tokens_dir,
            self._check_secrets_in_config,
            self._check_provider_sources,
            self._check_ollama_url,
            self._check_cloud_endpoints,
            self._check_discord,
            self._check_plugins,
            self._check_approval_policies,
            self._check_event_redaction,
        )
        findings: list[AuditFinding] = []
        for check in checks:
            try:
                findings.append(check())
            except Exception as exc:  # noqa: BLE001 - one broken check must not hide the rest
                findings.append(
                    AuditFinding(
                        check.__name__.removeprefix("_check_").replace("_", "-"),
                        "audit",
                        HealthState.UNKNOWN,
                        f"Check failed ({type(exc).__name__}): {exc}",
                    )
                )
        return AuditReport(
            generated_at=self._clock().isoformat(),
            findings=tuple(findings),
        )

    def _check_vault_file(self) -> AuditFinding:
        vault = self._paths.vault
        if not vault.exists():
            return AuditFinding(
                "vault-file", "permissions", HealthState.WARNING,
                "Vault file does not exist yet; no credentials are stored.",
            )
        return self._check_sensitive_path(vault, "vault-file", "Vault file")

    def _check_tokens_dir(self) -> AuditFinding:
        tokens = self._paths.tokens
        if not tokens.exists():
            return AuditFinding(
                "tokens-dir", "permissions", HealthState.OK,
                "Tokens directory does not exist; no tokens are stored.",
            )
        return self._check_sensitive_path(tokens, "tokens-dir", "Tokens directory")

    def _check_sensitive_path(
        self, path: Path, check: str, label: str
    ) -> AuditFinding:
        if os.name == "posix":
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                return AuditFinding(
                    check, "permissions", HealthState.ERROR,
                    f"{label} is accessible by other users (mode {oct(mode)}); "
                    "restrict it to owner-only access.",
                )
            return AuditFinding(
                check, "permissions", HealthState.OK,
                f"{label} is owner-only (mode {oct(mode)}).",
            )
        home = Path.home()
        try:
            inside_home = path.resolve().is_relative_to(home.resolve())
        except (OSError, ValueError):
            inside_home = False
        if not inside_home:
            return AuditFinding(
                check, "permissions", HealthState.WARNING,
                f"{label} is outside the user profile; verify its Windows "
                "ACLs grant access only to this user.",
            )
        return AuditFinding(
            check, "permissions", HealthState.OK,
            f"{label} is under the user profile; access is controlled by "
            "Windows ACLs (not verified by this audit).",
        )

    def _check_secrets_in_config(self) -> AuditFinding:
        found: list[str] = []
        for path in (self._paths.config, self._paths.profile):
            if not path.is_file():
                continue
            try:
                if path.stat().st_size > MAX_SCAN_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            keys = sorted(set(_SECRET_KEY_PATTERN.findall(text)))
            patterns = sum(
                len(pattern.findall(text)) for pattern in _SECRET_PATTERNS
            )
            if keys or patterns:
                found.append(
                    f"{path.name} (keys: {', '.join(keys) or 'none'}, "
                    f"secret-like values: {patterns})"
                )
        if found:
            return AuditFinding(
                "secrets-in-config", "secrets", HealthState.ERROR,
                "Possible secrets in configuration files: "
                + "; ".join(found)
                + ". Move credentials to Vault.",
            )
        return AuditFinding(
            "secrets-in-config", "secrets", HealthState.OK,
            "No secrets found in configuration files.",
        )

    def _check_provider_sources(self) -> AuditFinding:
        if self._provider_manager is None:
            return AuditFinding(
                "provider-sources", "secrets", HealthState.UNKNOWN,
                "Provider manager is unavailable.",
            )
        secrets = getattr(self._provider_manager, "secrets", None)
        if secrets is None or not callable(getattr(secrets, "source", None)):
            return AuditFinding(
                "provider-sources", "secrets", HealthState.UNKNOWN,
                "Secret store is unavailable.",
            )
        env_sourced: list[str] = []
        parts: list[str] = []
        for status in self._provider_manager.statuses():
            if status.key == "ollama":
                continue
            source = str(secrets.source(status.key))
            parts.append(f"{status.key}: {source}")
            if source.startswith("environment"):
                env_sourced.append(status.key)
        if env_sourced:
            return AuditFinding(
                "provider-sources", "secrets", HealthState.WARNING,
                "API keys for "
                + ", ".join(env_sourced)
                + " come from environment variables, which child processes "
                "can read; prefer Vault storage. (" + "; ".join(parts) + ")",
            )
        return AuditFinding(
            "provider-sources", "secrets", HealthState.OK,
            "Provider credentials: " + ("; ".join(parts) if parts else "none configured"),
        )

    def _check_ollama_url(self) -> AuditFinding:
        if self._config_manager is None:
            return AuditFinding(
                "ollama-url", "interfaces", HealthState.UNKNOWN,
                "Configuration manager is unavailable.",
            )
        url = str(
            self._config_manager.get(
                "providers.ollama.base_url", "http://localhost:11434"
            )
            or ""
        )
        host = urlparse(url).hostname or ""
        if host.lower() in _LOCAL_HOSTS:
            return AuditFinding(
                "ollama-url", "interfaces", HealthState.OK,
                f"Ollama is local-only ({url}).",
            )
        return AuditFinding(
            "ollama-url", "interfaces", HealthState.WARNING,
            f"Ollama is configured on a non-local address ({url}); prompts "
            "leave this machine.",
        )

    def _check_cloud_endpoints(self) -> AuditFinding:
        if self._config_manager is None:
            return AuditFinding(
                "cloud-endpoints", "interfaces", HealthState.UNKNOWN,
                "Configuration manager is unavailable.",
            )
        endpoints: list[str] = []
        for key in ("openai", "gemini"):
            enabled = bool(
                self._config_manager.get(f"providers.{key}.enabled", False)
            )
            if not enabled:
                continue
            url = str(
                self._config_manager.get(f"providers.{key}.base_url", "") or ""
            )
            endpoints.append(f"{key}: {url or 'default endpoint'}")
        if not endpoints:
            return AuditFinding(
                "cloud-endpoints", "interfaces", HealthState.OK,
                "No cloud providers are enabled.",
            )
        return AuditFinding(
            "cloud-endpoints", "interfaces", HealthState.OK,
            "Cloud providers enabled (prompts leave this machine by design): "
            + "; ".join(endpoints),
        )

    def _check_discord(self) -> AuditFinding:
        if self._provider_manager is None:
            return AuditFinding(
                "discord", "interfaces", HealthState.UNKNOWN,
                "Provider manager is unavailable.",
            )
        secrets = getattr(self._provider_manager, "secrets", None)
        if secrets is None or not callable(getattr(secrets, "get", None)):
            return AuditFinding(
                "discord", "interfaces", HealthState.UNKNOWN,
                "Secret store is unavailable.",
            )
        configured = bool(secrets.get("discord_bot"))
        if configured:
            return AuditFinding(
                "discord", "interfaces", HealthState.OK,
                "Discord bot token is configured (outbound connection only).",
            )
        return AuditFinding(
            "discord", "interfaces", HealthState.OK,
            "Discord bot token is not configured.",
        )

    def _check_plugins(self) -> AuditFinding:
        if self._plugin_manager is None:
            return AuditFinding(
                "plugins", "interfaces", HealthState.UNKNOWN,
                "Plugin manager is unavailable.",
            )
        try:
            loaded = int(self._plugin_manager.loaded_count())
            failed = int(self._plugin_manager.failed_count())
            names = [
                str(getattr(record, "name", ""))
                for record in self._plugin_manager.records()
            ]
        except (AttributeError, TypeError, ValueError) as exc:
            return AuditFinding(
                "plugins", "interfaces", HealthState.UNKNOWN,
                f"Could not list plugins: {exc}",
            )
        if failed:
            return AuditFinding(
                "plugins", "interfaces", HealthState.WARNING,
                f"{failed} plugin(s) failed to load; {loaded} loaded "
                f"({', '.join(names) or 'none'}).",
            )
        if not loaded:
            return AuditFinding(
                "plugins", "interfaces", HealthState.OK,
                "No plugins loaded.",
            )
        return AuditFinding(
            "plugins", "interfaces", HealthState.OK,
            f"{loaded} plugin(s) loaded ({', '.join(names)}); plugins run "
            "as code inside Orion.",
        )

    def _check_approval_policies(self) -> AuditFinding:
        if self._services is None:
            return AuditFinding(
                "approval-policies", "policies", HealthState.UNKNOWN,
                "Service registry is unavailable.",
            )
        getter = getattr(self._services, "get", None)
        action_service = getter("action_service") if callable(getter) else None
        approval = getattr(action_service, "approval", None) if action_service else None
        policies = getattr(approval, "policies", None) if approval else None
        if not callable(policies):
            return AuditFinding(
                "approval-policies", "policies", HealthState.UNKNOWN,
                "Approval engine is unavailable.",
            )
        allowed: list[str] = []
        total = 0
        for policy in policies():
            total += 1
            decision = getattr(policy, "decision", None)
            value = getattr(decision, "value", decision)
            if str(value) == "allow":
                allowed.append(str(getattr(policy, "action_type", "")))
        if allowed:
            return AuditFinding(
                "approval-policies", "policies", HealthState.WARNING,
                "Actions explicitly allowed without approval: "
                + ", ".join(sorted(allowed))
                + f" ({total} explicit policies total).",
            )
        return AuditFinding(
            "approval-policies", "policies", HealthState.OK,
            f"{total} explicit approval policies; nothing is allowed "
            "without approval.",
        )

    def _check_event_redaction(self) -> AuditFinding:
        events_root = self._paths.events
        if not events_root.is_dir():
            return AuditFinding(
                "event-redaction", "secrets", HealthState.OK,
                "Event Store directory does not exist; nothing to scan.",
            )
        try:
            files = sorted(
                (path for path in events_root.iterdir() if path.suffix == ".jsonl"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return AuditFinding(
                "event-redaction", "secrets", HealthState.UNKNOWN,
                "Could not list Event Store files.",
            )
        if not files:
            return AuditFinding(
                "event-redaction", "secrets", HealthState.OK,
                "No event files recorded yet.",
            )
        latest = files[0]
        try:
            if latest.stat().st_size > MAX_SCAN_BYTES:
                return AuditFinding(
                    "event-redaction", "secrets", HealthState.UNKNOWN,
                    f"Latest event file {latest.name} is too large to scan.",
                )
            text = latest.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return AuditFinding(
                "event-redaction", "secrets", HealthState.UNKNOWN,
                f"Could not read {latest.name}: {exc}",
            )
        matches = sum(
            len(pattern.findall(text)) for pattern in _SECRET_PATTERNS
        )
        if matches:
            return AuditFinding(
                "event-redaction", "secrets", HealthState.ERROR,
                f"{matches} secret-like value(s) found in {latest.name}; "
                "events must be redacted before storage.",
            )
        return AuditFinding(
            "event-redaction", "secrets", HealthState.OK,
            f"No secret-like values in the latest event file ({latest.name}).",
        )
