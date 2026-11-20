"""Tests for the Orion security audit (v0.4.7)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from orion.actions.approval import ActionPolicy, PolicyDecision
from orion.application.commands.observability_cli import (
    ObservabilityCliAdapter,
    dispatch_observability,
)
from orion.application.observability import (
    AuditReport,
    HealthState,
    ObservabilityApplicationHandler,
    SecurityAudit,
)
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter
from orion.services.provider_manager import ProviderStatus
from orion.ui.console import BASE_COMMANDS


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def _config(values=None):
    values = dict(values or {})
    return SimpleNamespace(
        config={"providers": {}, "workspace": {}},
        get=lambda key, default=None: values.get(key, default),
    )


def _secrets(sources=None, values=None):
    sources = dict(sources or {})
    values = dict(values or {})
    return SimpleNamespace(
        source=lambda key: sources.get(key, "not configured"),
        get=lambda key: values.get(key, ""),
    )


def _providers(*statuses, secrets=None):
    return SimpleNamespace(
        statuses=lambda: list(statuses),
        secrets=secrets or _secrets(),
    )


def _services(mapping):
    return SimpleNamespace(
        get=lambda name: mapping.get(name),
        snapshot=lambda: dict(mapping),
    )


def _action_service(*policies):
    return SimpleNamespace(
        approval=SimpleNamespace(policies=lambda: list(policies))
    )


def _plugins(loaded=(), failed=0):
    return SimpleNamespace(
        loaded_count=lambda: len(loaded),
        failed_count=lambda: failed,
        records=lambda: [SimpleNamespace(name=name) for name in loaded],
    )


def _audit(paths, clock, **kwargs):
    defaults = {
        "config_manager": _config({
            "providers.ollama.base_url": "http://localhost:11434",
            "providers.openai.enabled": True,
            "providers.openai.base_url": "https://api.openai.com/v1",
            "providers.gemini.enabled": False,
        }),
        "provider_manager": _providers(
            ProviderStatus("ollama", True, True, True, "llama3"),
            ProviderStatus("openai", True, True, False, "gpt-5"),
            ProviderStatus("gemini", True, False, False, ""),
            secrets=_secrets(
                {"openai": "local vault", "gemini": "not configured"},
                {"discord_bot": ""},
            ),
        ),
        "services": _services({
            "action_service": _action_service(
                ActionPolicy("image_save", PolicyDecision.REQUIRE_APPROVAL, "x"),
            ),
        }),
        "plugin_manager": _plugins(),
        "clock": clock,
    }
    defaults.update(kwargs)
    return SecurityAudit(paths, **defaults)


def _findings(report):
    return {finding.check: finding for finding in report.findings}


class SecurityAuditTests(TestCase):
    def setUp(self):
        # Ambient provider keys change onboarding/vault behavior; isolate tests.
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = OrionPaths(install_root=root, user_root=root / ".orion")
        self.paths.ensure()
        self.clock = _Clock()
        (self.paths.user_root / "vault").mkdir(parents=True, exist_ok=True)
        self.paths.vault.write_text("openai:\n  api_key: vault-value\n", encoding="utf-8")
        self.paths.config.write_text("providers:\n  default: ollama\n", encoding="utf-8")

    def test_all_checks_present(self):
        report = _audit(self.paths, self.clock).run()
        self.assertIsInstance(report, AuditReport)
        self.assertEqual(report.generated_at, "2026-11-20T12:00:00+00:00")
        self.assertEqual(
            sorted(_findings(report)),
            [
                "approval-policies",
                "cloud-endpoints",
                "discord",
                "event-redaction",
                "ollama-url",
                "plugins",
                "provider-sources",
                "secrets-in-config",
                "tokens-dir",
                "vault-file",
            ],
        )

    def test_clean_system_is_ok(self):
        report = _audit(self.paths, self.clock).run()
        self.assertEqual(report.overall, HealthState.OK)
        self.assertTrue(
            all(finding.state is HealthState.OK for finding in report.findings)
        )

    def test_secret_in_config_is_error_and_value_never_reported(self):
        secret = "sk-test-secret-value-12345"
        self.paths.config.write_text(
            f"providers:\n  default: ollama\n  api_key: {secret}\n",
            encoding="utf-8",
        )
        report = _audit(self.paths, self.clock).run()
        finding = _findings(report)["secrets-in-config"]
        self.assertEqual(finding.state, HealthState.ERROR)
        self.assertEqual(report.overall, HealthState.ERROR)
        dumped = json.dumps(report.to_dict())
        self.assertNotIn(secret, dumped)
        self.assertIn("api_key", finding.detail)

    def test_env_sourced_provider_is_warning(self):
        audit = _audit(
            self.paths, self.clock,
            provider_manager=_providers(
                ProviderStatus("openai", True, True, True, "gpt-5"),
                secrets=_secrets({"openai": "environment (OPENAI_API_KEY)"}),
            ),
        )
        report = audit.run()
        finding = _findings(report)["provider-sources"]
        self.assertEqual(finding.state, HealthState.WARNING)
        self.assertIn("environment variables", finding.detail)

    def test_ollama_lan_url_is_warning(self):
        audit = _audit(
            self.paths, self.clock,
            config_manager=_config({
                "providers.ollama.base_url": "http://192.168.1.5:11434",
            }),
        )
        report = audit.run()
        finding = _findings(report)["ollama-url"]
        self.assertEqual(finding.state, HealthState.WARNING)
        self.assertIn("non-local address", finding.detail)

    def test_cloud_endpoints_list_enabled_providers(self):
        report = _audit(self.paths, self.clock).run()
        finding = _findings(report)["cloud-endpoints"]
        self.assertEqual(finding.state, HealthState.OK)
        self.assertIn("openai", finding.detail)
        self.assertNotIn("gemini", finding.detail)

    def test_discord_configured_is_reported(self):
        audit = _audit(
            self.paths, self.clock,
            provider_manager=_providers(
                secrets=_secrets(values={"discord_bot": "token-value"}),
            ),
        )
        report = audit.run()
        finding = _findings(report)["discord"]
        self.assertEqual(finding.state, HealthState.OK)
        self.assertIn("configured", finding.detail)
        self.assertNotIn("token-value", json.dumps(report.to_dict()))

    def test_plugins_loaded_and_failed(self):
        audit = _audit(
            self.paths, self.clock, plugin_manager=_plugins(("demo",))
        )
        report = audit.run()
        finding = _findings(report)["plugins"]
        self.assertEqual(finding.state, HealthState.OK)
        self.assertIn("demo", finding.detail)
        audit = _audit(
            self.paths, self.clock, plugin_manager=_plugins(("demo",), failed=2)
        )
        report = audit.run()
        finding = _findings(report)["plugins"]
        self.assertEqual(finding.state, HealthState.WARNING)
        self.assertIn("2 plugin(s) failed", finding.detail)

    def test_explicit_allow_policy_is_warning(self):
        audit = _audit(
            self.paths, self.clock,
            services=_services({
                "action_service": _action_service(
                    ActionPolicy("file_delete", PolicyDecision.ALLOW, "x"),
                    ActionPolicy("image_save", PolicyDecision.REQUIRE_APPROVAL, "x"),
                ),
            }),
        )
        report = audit.run()
        finding = _findings(report)["approval-policies"]
        self.assertEqual(finding.state, HealthState.WARNING)
        self.assertIn("file_delete", finding.detail)

    def test_event_redaction_spot_check(self):
        events_root = self.paths.events
        events_root.mkdir(parents=True, exist_ok=True)
        (events_root / "2026-11-20.jsonl").write_text(
            '{"event": "clean"}\n', encoding="utf-8"
        )
        report = _audit(self.paths, self.clock).run()
        self.assertEqual(_findings(report)["event-redaction"].state, HealthState.OK)
        secret = "sk-leakedsecretvalue99999"
        (events_root / "2026-11-20.jsonl").write_text(
            f'{{"event": "leak", "data": "{secret}"}}\n', encoding="utf-8"
        )
        report = _audit(self.paths, self.clock).run()
        finding = _findings(report)["event-redaction"]
        self.assertEqual(finding.state, HealthState.ERROR)
        self.assertNotIn(secret, json.dumps(report.to_dict()))

    def test_missing_dependencies_are_unknown(self):
        report = SecurityAudit(self.paths, clock=self.clock).run()
        findings = _findings(report)
        self.assertEqual(findings["provider-sources"].state, HealthState.UNKNOWN)
        self.assertEqual(findings["approval-policies"].state, HealthState.UNKNOWN)
        self.assertEqual(findings["plugins"].state, HealthState.UNKNOWN)
        self.assertEqual(report.overall, HealthState.UNKNOWN)

    def test_posix_mode_check_flags_open_permissions(self):
        with patch("os.name", "posix"):
            report = _audit(self.paths, self.clock).run()
        finding = _findings(report)["vault-file"]
        self.assertEqual(finding.state, HealthState.ERROR)
        self.assertIn("accessible by other users", finding.detail)

    def test_report_serializes_to_json_safe_dict(self):
        report = _audit(self.paths, self.clock).run()
        data = report.to_dict()
        self.assertEqual(json.loads(json.dumps(data)), data)


class AuditHandlerTests(TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = OrionPaths(install_root=root, user_root=root / ".orion")
        self.paths.ensure()
        (self.paths.user_root / "vault").mkdir(parents=True, exist_ok=True)
        self.paths.vault.write_text("openai:\n  api_key: vault-value\n", encoding="utf-8")
        self.paths.config.write_text("providers:\n  default: ollama\n", encoding="utf-8")
        self.handler = ObservabilityApplicationHandler(
            paths=self.paths,
            config_manager=_config({
                "providers.ollama.base_url": "http://localhost:11434",
            }),
            provider_manager=_providers(
                ProviderStatus("ollama", True, True, True, "llama3"),
                secrets=_secrets(),
            ),
            services=_services({
                "action_service": _action_service(),
            }),
            plugin_manager=_plugins(),
            clock=_Clock(),
        )

    def test_audit_returns_findings_in_data(self):
        result = self.handler.audit()
        self.assertTrue(result.ok)
        self.assertIn("Orion Security Audit", result.message)
        self.assertEqual(result.data["read_only"], True)
        self.assertEqual(result.data["overall"], "ok")
        self.assertEqual(len(result.data["findings"]), 10)

    def test_audit_result_is_json_safe(self):
        result = self.handler.audit()
        payload = json.loads(result.to_json())
        self.assertEqual(payload["data"]["overall"], "ok")


class AuditCliTests(TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = OrionPaths(install_root=root, user_root=root / ".orion")
        self.paths.ensure()
        (self.paths.user_root / "vault").mkdir(parents=True, exist_ok=True)
        self.paths.vault.write_text("openai:\n  api_key: vault-value\n", encoding="utf-8")
        self.paths.config.write_text("providers:\n  default: ollama\n", encoding="utf-8")
        self.handler = ObservabilityApplicationHandler(
            paths=self.paths,
            config_manager=_config({
                "providers.ollama.base_url": "http://localhost:11434",
            }),
            provider_manager=_providers(
                ProviderStatus("ollama", True, True, True, "llama3"),
                secrets=_secrets(),
            ),
            services=_services({
                "action_service": _action_service(),
            }),
            plugin_manager=_plugins(),
            clock=_Clock(),
        )
        self.runtime = SimpleNamespace(observability_application=self.handler)
        self.output = StringIO()
        self.adapter = ObservabilityCliAdapter(
            self.runtime, output_provider=self.output.write
        )

    def test_audit_renders_findings(self):
        result = self.adapter.handle("audit")
        self.assertTrue(result.ok)
        rendered = self.output.getvalue()
        self.assertIn("Orion Security Audit", rendered)
        self.assertIn("vault-file", rendered)

    def test_audit_json_output_parses(self):
        result = self.adapter.handle("audit --json")
        self.assertTrue(result.ok)
        payload = json.loads(self.output.getvalue())
        self.assertEqual(len(payload["data"]["findings"]), 10)

    def test_audit_unknown_option_is_usage(self):
        result = self.adapter.handle("audit --bogus")
        self.assertFalse(result.ok)
        self.assertIn("Unknown audit option", result.message)

    def test_dispatch_recognizes_audit_only(self):
        with patch("builtins.print") as output:
            self.assertTrue(dispatch_observability(self.runtime, "audit"))
            self.assertTrue(dispatch_observability(self.runtime, "  AUDIT "))
        self.assertFalse(dispatch_observability(self.runtime, "audits"))
        self.assertFalse(dispatch_observability(self.runtime, ""))
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Orion Security Audit", rendered)

    def test_router_handles_audit(self):
        router = CommandRouter(self.runtime)
        with patch("builtins.print") as output:
            router.handle("audit")
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        self.assertIn("Orion Security Audit", rendered)


class AuditCoverageTests(TestCase):
    def test_audit_in_router_help_base_commands_and_guide(self):
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        help_text = "\n".join(
            str(call.args[0]) for call in output.call_args_list if call.args
        )
        self.assertIn("audit", help_text)
        self.assertIn("audit", BASE_COMMANDS)
        guide = Path(__file__).resolve().parents[1] / "docs" / "USER_GUIDE.md"
        guide_text = guide.read_text(encoding="utf-8")
        self.assertIn("audit", guide_text)
