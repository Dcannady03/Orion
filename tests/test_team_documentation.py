import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orion.core.router import CommandRouter
from orion.services.codex_bridge import (
    CodexBridge,
    CodexBridgeStore,
    CodexCLICapabilities,
    CodexProcessResult,
    CodexRun,
)
from orion.services.execution_engines import ENGINE_STATUS_INSTALLED, ExecutionEngine
from orion.services.team import (
    TEAM_STATUS_AWAITING_APPROVAL,
    RoleOutput,
    TeamArtifact,
    TeamTask,
    TeamTaskStore,
)
from orion.services.team_documentation import (
    DOCUMENTATION_SCHEMA_VERSION,
    DocumentationAttempt,
    DocumentationFinding,
    DocumentationRequest,
    DocumentationReviewService,
    sanitized_documentation_error,
)
from orion.services.team_roles import ResolvedTeamRole, ROLE_SPEC_BY_NAME
from orion.services.team_validation import AutomaticValidationService
from orion.services.workspace import WorkspaceCapabilities
from orion.services.workspace_snapshot import SnapshotLimits, WorkspaceSnapshotService


class FlatConfig:
    def __init__(self, values=None):
        self.values = {
            "codex_bridge.snapshot_max_files": 2_000,
            "codex_bridge.snapshot_max_file_bytes": 2_000_000,
            "codex_bridge.snapshot_max_total_bytes": 20_000_000,
            "codex_bridge.diff_max_bytes": 200_000,
            "team.documentation_review.enabled": True,
            "team.documentation_review.max_documents": 24,
            "team.documentation_review.max_findings": 30,
            "team.documentation_review.max_diff_summary_chars": 24_000,
            "team.pricing.ollama.input_per_million": 0,
            "team.pricing.ollama.output_per_million": 0,
            **(values or {}),
        }

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeProvider:
    def __init__(self, model, response, *, error=None, mutator=None):
        self.model = model
        self.response = response
        self.error = error
        self.mutator = mutator
        self.calls = []

    def select_model(self, model):
        self.model = model

    def chat(self, prompt, system_prompt=None):
        self.calls.append((prompt, system_prompt))
        if self.mutator:
            self.mutator()
        if self.error:
            raise self.error
        return self.response


class ScriptedProvider(FakeProvider):
    def __init__(self, model, outcomes):
        super().__init__(model, "")
        self.outcomes = list(outcomes)

    def chat(self, prompt, system_prompt=None):
        self.calls.append((prompt, system_prompt))
        if not self.outcomes:
            raise AssertionError("Scripted provider received an unexpected call.")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeProviderFactory:
    def __init__(self, providers):
        self.providers = providers
        self.calls = []

    def create(self, provider):
        self.calls.append(provider)
        value = self.providers[provider]
        if isinstance(value, BaseException):
            raise value
        return value


class DocumentationRoles:
    def __init__(
        self,
        engine,
        *,
        candidates=None,
        requested="active-planning-model",
        available=True,
        fallback_reason="",
    ):
        self.engine_value = engine
        self.requested = requested
        self.available = available
        self.fallback_reason = fallback_reason
        self.candidates = candidates or [
            ("ollama", "review-model", ""),
        ]

    def status(self, role, *, prompt=""):
        if role == "tester":
            return ResolvedTeamRole(
                spec=ROLE_SPEC_BY_NAME["tester"],
                requested_assignment="codex",
                actual_assignment="codex",
                source="default",
                available=True,
                fallback="None; unavailable assignments fail closed",
                engine_id="codex",
            )
        provider, model, fallback = self.candidates[0]
        return ResolvedTeamRole(
            spec=ROLE_SPEC_BY_NAME["documentation"],
            requested_assignment=self.requested,
            actual_assignment=f"{provider}:{model}",
            source="user-configured" if self.requested != "active-planning-model" else "default",
            available=self.available,
            fallback="Balanced provider routing",
            fallback_reason=self.fallback_reason or fallback,
            provider=provider,
            model=model,
        )

    def planning_candidates(self, role, prompt, *, validate_agent=True):
        if not self.available:
            raise ValueError("Documentation Reviewer provider is unavailable.")
        return tuple(
            ResolvedTeamRole(
                spec=ROLE_SPEC_BY_NAME["documentation"],
                requested_assignment=self.requested,
                actual_assignment=f"{provider}:{model}",
                source="user-configured" if self.requested != "active-planning-model" else "default",
                available=True,
                fallback="Balanced provider routing",
                fallback_reason=fallback,
                provider=provider,
                model=model,
            )
            for provider, model, fallback in self.candidates
        )

    def routing_profile(self):
        return "balanced"

    def agent(self, role):
        return None

    def engine(self, role):
        return self.engine_value


class StaticCapabilities:
    OPTIONS = frozenset({
        "--ask-for-approval", "--cd", "--config", "--ephemeral", "--ignore-user-config",
        "--json", "--output-schema", "--sandbox", "--skip-git-repo-check", "--strict-config",
    })

    def detect(self, executable):
        return CodexCLICapabilities(str(executable), self.OPTIONS)


class ImplementationRunner:
    def __init__(self, result, mutator):
        self.result = result
        self.mutator = mutator
        self.calls = []

    def run(self, command, *, cwd, prompt, timeout):
        self.calls.append(tuple(command))
        self.mutator(Path(cwd))
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": json.dumps(self.result)},
        }
        return CodexProcessResult(0, json.dumps(event) + "\n", "")


def model_response(findings=(), *, decision="confirm_required", reason="Coverage reviewed."):
    return json.dumps({
        "classification_decision": decision,
        "classification_reason": reason,
        "findings": list(findings),
    })


def finding(
    severity="warning",
    category="stale",
    document="docs/USER_GUIDE.md",
    text="An example is stale.",
    *,
    blocks=True,
    confidence=0.9,
):
    return {
        "severity": severity,
        "category": category,
        "document": document,
        "section": "Example",
        "finding": text,
        "implementation_evidence": "The bounded implementation summary uses different behavior.",
        "recommended_correction": "Update the example to match the current command.",
        "confidence": confidence,
        "blocks_passed": blocks,
    }


class DocumentationReviewerTests(unittest.TestCase):
    def setUp(self):
        self.config = FlatConfig()
        self.engine = ExecutionEngine(
            "codex", "Codex CLI", ENGINE_STATUS_INSTALLED, True, True, True,
            executable="codex.cmd",
        )

    @staticmethod
    def provider_diagnostics(attempt):
        values = []
        for item in attempt.safe_diagnostics:
            try:
                value = json.loads(item)
            except json.JSONDecodeError:
                continue
            if value.get("kind") == "documentation_provider_attempt":
                values.append(value)
        return values

    @staticmethod
    def common_docs():
        return {
            "README.md": "# Orion\n\nWidget feature overview.\n",
            "CHANGELOG.md": "# Unreleased\n",
            "docs/USER_GUIDE.md": "# User Guide\n\nWidget feature reference.\n",
            "docs/AI_TEAM.md": "# AI Team\n\nAI Team workflow.\n",
            "docs/CODEX_BRIDGE.md": "# Codex Bridge\n\nExecution artifacts.\n",
            "docs/EXECUTION_ENGINES.md": "# Execution Engines\n\nCodex CLI.\n",
            "docs/CONFIGURATION.md": "# Configuration\n\nConfiguration reference.\n",
            "docs/ARCHITECTURE.md": "# Architecture\n\nService architecture.\n",
            "docs/SERVICES.md": "# Services\n\nWidget service.\n",
            "docs/IMAGE_CENTER.md": "# Image Center\n\nProvider-neutral image service.\n",
            "docs/ROADMAP.md": "# Roadmap\n\nWidget milestone.\n",
            "docs/DEFINITION_OF_DONE.md": "# Definition of Done\n\nUpdate documentation.\n",
        }

    @staticmethod
    def write(root, relative, content):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            if path.exists():
                path.unlink()
            return
        path.write_text(content, encoding="utf-8")

    def request(
        self,
        root,
        *,
        goal="Add widget feature",
        before_extra=None,
        after=None,
        provider_response=None,
        providers=None,
        roles=None,
        validation=None,
        implementation_summary="Implemented the widget feature.",
        config=None,
    ):
        root = Path(root)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / ".git").mkdir()
        before = self.common_docs()
        before.update(before_extra or {})
        for relative, content in before.items():
            self.write(workspace, relative, content)
        selected_config = config or self.config
        snapshots = WorkspaceSnapshotService()
        validation_service = AutomaticValidationService(
            selected_config,
            snapshot_service=snapshots,
            now=lambda: datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
        )
        protected = validation_service.protected_state(workspace)
        capabilities = WorkspaceCapabilities.detect(workspace, which=lambda _name: None)
        blob_root = root / "artifacts/snapshot/blobs"
        baseline = snapshots.capture(
            capabilities,
            blob_root,
            SnapshotLimits.from_config(selected_config),
            created_at="2026-07-20T11:59:00+00:00",
        )
        after = after or {
            "feature.txt": "widget ready\n",
            "docs/USER_GUIDE.md": "# User Guide\n\nWidget feature reference and example.\n",
            "CHANGELOG.md": "# Unreleased\n\n- Added widget feature.\n",
        }
        for relative, content in after.items():
            self.write(workspace, relative, content)
        changes, _ = snapshots.compare(
            baseline, blob_root, SnapshotLimits.from_config(selected_config)
        )
        result = {
            "summary": implementation_summary,
            "files_changed": [
                {"path": item.path, "summary": f"{item.kind.title()} file."}
                for item in changes.changes
            ],
            "tests": [{"command": "reported", "status": "passed", "summary": "Passed."}],
            "risks": [], "remaining_work": [], "review_notes": [],
        }
        provider = FakeProvider(
            "review-model",
            provider_response or model_response(),
        )
        factory = FakeProviderFactory(providers or {"ollama": provider})
        selected_roles = roles or DocumentationRoles(self.engine)
        service = DocumentationReviewService(
            selected_config,
            selected_roles,
            factory,
            snapshot_service=snapshots,
            validation_service=validation_service,
            now=lambda: datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
        )
        request = DocumentationRequest(
            attempt_id="documentation-0001",
            run_id="run-documentation-001",
            team_task_id="team-documentation-001",
            approval_id="approval-documentation-001",
            workspace=capabilities,
            active_workspace=str(workspace.resolve()),
            changes=changes,
            implementation_result=result,
            plan_goal=goal,
            plan_steps=(goal, "Update tests and documentation."),
            validation=validation,
            validation_reference="validation/validation-0001.json" if validation else "",
            baseline=baseline,
            blob_root=blob_root,
            protected_baseline=protected,
            artifact_paths=(
                "documentation/documentation-0001.json",
                "documentation/documentation-0001.log",
            ),
        )
        return service, request, workspace, provider, factory

    def test_deterministic_classifier_requires_commands_configuration_safety_and_architecture(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(tmp)
            classified = service.classify(
                request.changes,
                "Add a command and configuration key with approval safety architecture changes",
                ("Update interactive output",),
                "Implemented.",
                command_changes=("team docs",),
                configuration_changes=("team.documentation_review.enabled",),
            )
            self.assertTrue(classified.required)
            self.assertTrue({"command", "configuration", "safety", "architecture"}.issubset(classified.categories))
            self.assertTrue(classified.reasons)
            self.assertTrue(classified.evidence)

    def test_internal_refactor_and_test_only_changes_are_not_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, provider, _ = self.request(
                tmp,
                goal="Internal refactor with no observable behavior change",
                after={"orion/services/internal.py": "VALUE = 1\n"},
            )
            attempt = service.review(request)
            self.assertEqual(attempt.status, "not_required")
            self.assertEqual(provider.calls, [])

    def test_trivial_standalone_example_is_not_required_without_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, provider, factory = self.request(
                tmp,
                goal="Create hello.py that prints Hello Orion!",
                after={
                    "hello.py": (
                        "# Run with Python 3; acceptance: print Hello Orion! exactly.\n"
                        'print("Hello Orion!")\n'
                    ),
                },
                implementation_summary=(
                    "Created the small standalone hello.py example."
                ),
            )
            attempt = service.review(request)
            self.assertEqual(attempt.status, "not_required")
            self.assertEqual(attempt.classification_decision, "deterministic_not_required")
            self.assertEqual(provider.calls, [])
            self.assertEqual(factory.calls, [])
            self.assertIn("hello.py", attempt.classification.evidence)
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, provider, _ = self.request(
                tmp,
                goal="Improve internal tests",
                after={"tests/test_internal.py": "VALUE = 1\n"},
            )
            attempt = service.review(request)
            self.assertEqual(attempt.status, "not_required")
            self.assertEqual(provider.calls, [])

    def test_complete_documentation_passes_independently_of_validation_status(self):
        for validation_status in ("passed", "warnings", "failed"):
            with self.subTest(validation_status=validation_status), tempfile.TemporaryDirectory() as tmp:
                validation = SimpleNamespace(
                    status=validation_status,
                    checks_passed=("Integrity",),
                    checks_failed=("Tests",) if validation_status == "failed" else (),
                    warnings=("Optional warning",) if validation_status == "warnings" else (),
                )
                service, request, _, _, _ = self.request(tmp, validation=validation)
                attempt = service.review(request)
                self.assertEqual(attempt.status, "passed")

    def test_image_center_changes_select_focused_documentation_inventory(self):
        after = {
            "orion/services/image.py": "class ImageService:\n    pass\n",
            "docs/IMAGE_CENTER.md": "# Image Center\n\nSafe generation and copy workflow.\n",
            "docs/USER_GUIDE.md": "# User Guide\n\nImage Center commands and Discord setup.\n",
            "docs/CONFIGURATION.md": "# Configuration\n\nImage and Discord limits.\n",
            "docs/SERVICES.md": "# Services\n\nRegistered ImageService.\n",
            "CHANGELOG.md": "# Unreleased\n\n- Added Image Center.\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                goal="Add provider-neutral image generation",
                after=after,
            )
            attempt = service.review(request)
            inspected = set(attempt.documents_inspected)
            self.assertIn("docs/IMAGE_CENTER.md", inspected)
            self.assertIn("docs/CONFIGURATION.md", inspected)
            self.assertIn("docs/SERVICES.md", inspected)

    def test_model_warning_and_material_error_produce_independent_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                provider_response=model_response([finding()]),
            )
            attempt = service.review(request)
            self.assertEqual(attempt.status, "warnings")
            self.assertEqual(attempt.counts_by_severity["warning"], 1)
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                provider_response=model_response([
                    finding("error", "safety", text="The safety claim is inaccurate."),
                ]),
            )
            attempt = service.review(request)
            self.assertEqual(attempt.status, "failed")
            self.assertEqual(attempt.counts_by_severity["error"], 1)

    def test_numeric_confidence_remains_canonical_without_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, provider, _ = self.request(
                tmp,
                provider_response=model_response([
                    finding(confidence=0.75),
                ]),
            )
            attempt = service.review(request)
            diagnostic = self.provider_diagnostics(attempt)[0]
            self.assertEqual(attempt.findings[-1].confidence, 0.75)
            self.assertIsInstance(attempt.to_dict()["findings"][-1]["confidence"], float)
            self.assertEqual(len(provider.calls), 1)
            self.assertFalse(diagnostic["normalization_attempted"])
            self.assertFalse(diagnostic["normalization_applied"])

    def test_openai_numeric_string_confidence_is_normalized_without_repair(self):
        openai_finding = finding(confidence="0.8")
        with self.assertRaisesRegex(
            ValueError,
            "Documentation finding confidence must be numeric",
        ):
            DocumentationFinding.from_value(openai_finding)
        roles = DocumentationRoles(
            self.engine,
            candidates=[("openai", "gpt-4.1-mini", "")],
        )
        provider = FakeProvider(
            "gpt-4.1-mini",
            model_response([openai_finding]),
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                providers={"openai": provider},
                roles=roles,
            )
            attempt = service.review(request)
            diagnostic = self.provider_diagnostics(attempt)[0]
            self.assertEqual(attempt.findings[-1].confidence, 0.8)
            self.assertEqual(len(provider.calls), 1)
            self.assertTrue(diagnostic["normalization_attempted"])
            self.assertTrue(diagnostic["normalization_applied"])
            self.assertEqual(diagnostic["confidence_shapes"], ["numeric_string"])
            self.assertFalse(diagnostic["repair_attempted"])

    def test_percentage_confidence_is_normalized_to_unit_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, provider, _ = self.request(
                tmp,
                provider_response=model_response([
                    finding(confidence="80%"),
                ]),
            )
            attempt = service.review(request)
            diagnostic = self.provider_diagnostics(attempt)[0]
            self.assertEqual(attempt.findings[-1].confidence, 0.8)
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(diagnostic["confidence_shapes"], ["percentage"])
            self.assertTrue(diagnostic["normalization_applied"])

    def test_confidence_labels_are_rejected_without_a_mapping_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, _, _, _, _ = self.request(tmp)
            for label in ("low", "medium", "high"):
                with self.subTest(label=label), self.assertRaisesRegex(
                    ValueError,
                    "numeric value or percentage",
                ):
                    service._parse_model_review(model_response([
                        finding(confidence=label),
                    ]))

    def test_out_of_range_nan_and_infinity_confidence_are_rejected(self):
        values = (1.01, -0.01, float("nan"), float("inf"), float("-inf"))
        with tempfile.TemporaryDirectory() as tmp:
            service, _, _, _, _ = self.request(tmp)
            for value in values:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    service._parse_model_review(model_response([
                        finding(confidence=value),
                    ]))

    def test_arbitrary_confidence_gets_one_repair_attempt_then_fails(self):
        invalid = model_response([finding(confidence="very likely")])
        provider = ScriptedProvider("review-model", [invalid, invalid])
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                providers={"ollama": provider},
            )
            with self.assertRaises(RuntimeError) as captured:
                service.review(request)
            diagnostic = json.loads(captured.exception.safe_diagnostics[0])
            self.assertEqual(len(provider.calls), 2)
            self.assertEqual(diagnostic["failure_category"], "response_validation")
            self.assertTrue(diagnostic["normalization_attempted"])
            self.assertFalse(diagnostic["normalization_applied"])
            self.assertTrue(diagnostic["repair_attempted"])
            self.assertFalse(diagnostic["repair_succeeded"])

    def test_structured_output_repair_retry_can_complete_review(self):
        provider = ScriptedProvider(
            "review-model",
            [
                model_response([finding(confidence="very likely")]),
                model_response([finding(confidence=0.85)]),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                providers={"ollama": provider},
            )
            attempt = service.review(request)
            diagnostic = self.provider_diagnostics(attempt)[0]
            self.assertEqual(len(provider.calls), 2)
            self.assertEqual(attempt.findings[-1].confidence, 0.85)
            self.assertTrue(diagnostic["repair_attempted"])
            self.assertTrue(diagnostic["repair_succeeded"])
            self.assertEqual(diagnostic["outcome"], "success")

    def test_failed_repair_falls_back_to_next_provider(self):
        invalid = model_response([finding(confidence="unknown")])
        first = ScriptedProvider("gpt-4.1-mini", [invalid, invalid])
        second = FakeProvider(
            "review-model",
            model_response([finding(confidence=0.9)]),
        )
        roles = DocumentationRoles(
            self.engine,
            candidates=[
                ("openai", "gpt-4.1-mini", ""),
                ("ollama", "review-model", "Runtime fallback."),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, factory = self.request(
                tmp,
                providers={"openai": first, "ollama": second},
                roles=roles,
            )
            attempt = service.review(request)
            diagnostics = self.provider_diagnostics(attempt)
            self.assertEqual(factory.calls, ["openai", "ollama"])
            self.assertEqual(len(first.calls), 2)
            self.assertEqual(len(second.calls), 1)
            self.assertEqual(attempt.provider, "ollama")
            self.assertEqual(diagnostics[0]["failure_category"], "response_validation")
            self.assertTrue(diagnostics[0]["repair_attempted"])
            self.assertTrue(diagnostics[0]["fallback_occurred"])
            self.assertTrue(diagnostics[1]["fallback_occurred"])

    def test_timeout_falls_back_without_repairing_that_provider(self):
        first = FakeProvider(
            "slow-model",
            "",
            error=TimeoutError("timed out"),
        )
        second = FakeProvider("review-model", model_response())
        roles = DocumentationRoles(
            self.engine,
            candidates=[
                ("ollama", "slow-model", ""),
                ("openai", "review-model", "Runtime fallback."),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                providers={"ollama": first, "openai": second},
                roles=roles,
            )
            attempt = service.review(request)
            diagnostics = self.provider_diagnostics(attempt)
            self.assertEqual(len(first.calls), 1)
            self.assertEqual(attempt.provider, "openai")
            self.assertEqual(diagnostics[0]["failure_category"], "timeout")
            self.assertFalse(diagnostics[0]["repair_attempted"])

    def test_billing_failure_falls_back_without_repairing_that_provider(self):
        first = FakeProvider(
            "billing-model",
            "",
            error=ConnectionError("prepaid credits depleted"),
        )
        second = FakeProvider("review-model", model_response())
        roles = DocumentationRoles(
            self.engine,
            candidates=[
                ("gemini", "billing-model", ""),
                ("ollama", "review-model", "Runtime fallback."),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                providers={"gemini": first, "ollama": second},
                roles=roles,
            )
            attempt = service.review(request)
            diagnostics = self.provider_diagnostics(attempt)
            self.assertEqual(len(first.calls), 1)
            self.assertEqual(attempt.provider, "ollama")
            self.assertEqual(diagnostics[0]["failure_category"], "billing")
            self.assertFalse(diagnostics[0]["repair_attempted"])

    def test_all_providers_unavailable_preserves_safe_routing_diagnostics(self):
        roles = DocumentationRoles(
            self.engine,
            candidates=[
                ("ollama", "slow-model", ""),
                ("gemini", "billing-model", ""),
                ("openai", "offline-model", ""),
            ],
        )
        providers = {
            "ollama": FakeProvider(
                "slow-model", "", error=TimeoutError("timed out")
            ),
            "gemini": FakeProvider(
                "billing-model", "", error=ConnectionError("prepaid credits depleted")
            ),
            "openai": FakeProvider(
                "offline-model", "", error=ConnectionError("network unavailable")
            ),
        }
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                providers=providers,
                roles=roles,
            )
            with self.assertRaises(RuntimeError) as captured:
                service.review(request)
            attempt = service.error(
                request,
                "documentation_review_error",
                sanitized_documentation_error(captured.exception),
                diagnostics=captured.exception.safe_diagnostics,
            )
            diagnostics = self.provider_diagnostics(attempt)
            payload = json.dumps(attempt.to_dict())
            self.assertEqual(
                [item["failure_category"] for item in diagnostics],
                ["timeout", "billing", "connection"],
            )
            self.assertEqual(attempt.status, "error")
            self.assertNotIn("prepaid credits depleted", payload)
            self.assertNotIn("network unavailable", payload)

    def test_missing_user_guide_command_and_interactive_help_fail(self):
        before_console = 'BASE_COMMANDS = ("help",)\n'
        after_console = 'BASE_COMMANDS = ("help", "team docs example")\n'
        after = {
            "orion/ui/console.py": after_console,
            "docs/USER_GUIDE.md": "# User Guide\n\nFeature exists without its command syntax.\n",
            "CHANGELOG.md": "# Unreleased\n\n- Added example command.\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                goal="Add a user-facing command",
                before_extra={"orion/ui/console.py": before_console},
                after=after,
            )
            attempt = service.review(request)
            categories = {item.category for item in attempt.findings}
            self.assertEqual(attempt.status, "failed")
            self.assertIn("undocumented-command", categories)
            self.assertIn("help-mismatch", categories)

    def test_undocumented_configuration_and_missing_changelog_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                goal="Change a configuration default",
                before_extra={"config/default.yaml": "team: {}\n"},
                after={
                    "config/default.yaml": "team:\n  documentation_review:\n    enabled: true\n",
                    "docs/USER_GUIDE.md": "# User Guide\n\nConfiguration changed.\n",
                },
            )
            attempt = service.review(request)
            categories = {item.category for item in attempt.findings}
            self.assertEqual(attempt.status, "failed")
            self.assertIn("configuration", categories)
            self.assertIn("changelog", categories)

    def test_stale_architecture_contradiction_and_example_findings_are_preserved(self):
        values = [
            finding("warning", "stale", "docs/AI_TEAM.md", "Old command syntax remains."),
            finding("error", "architecture", "docs/ARCHITECTURE.md", "Workflow omits Documentation Review."),
            finding("warning", "inconsistent", "README.md", "README contradicts the User Guide."),
            finding("warning", "example", "docs/USER_GUIDE.md", "Representative CLI output is stale."),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                goal="Change architecture workflow",
                after={
                    "feature.txt": "ready\n",
                    "docs/USER_GUIDE.md": "# User Guide\n\nUpdated workflow.\n",
                    "docs/ARCHITECTURE.md": "# Architecture\n\nUpdated workflow.\n",
                    "CHANGELOG.md": "# Unreleased\n\n- Changed workflow.\n",
                },
                provider_response=model_response(values),
            )
            attempt = service.review(request)
            self.assertEqual(attempt.status, "failed")
            self.assertTrue({"stale", "architecture", "inconsistent", "example"}.issubset(attempt.counts_by_category))

    def test_broken_local_link_is_a_material_deterministic_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                after={
                    "feature.txt": "ready\n",
                    "docs/USER_GUIDE.md": "# User Guide\n\n[Missing](missing.md)\n",
                    "CHANGELOG.md": "# Unreleased\n\n- Added feature.\n",
                },
            )
            attempt = service.review(request)
            self.assertEqual(attempt.status, "warnings")
            self.assertIn("broken-link", {item.category for item in attempt.findings})

    def test_explicit_assignment_dynamic_fallback_usage_and_cost_are_recorded(self):
        first = FakeProvider("bad-model", "", error=TimeoutError("secret failure"))
        second = FakeProvider("review-model", model_response())
        roles = DocumentationRoles(
            self.engine,
            requested="active-planning-model",
            candidates=[
                ("openai", "bad-model", ""),
                ("ollama", "review-model", "Runtime fallback."),
            ],
        )
        config = FlatConfig({
            "team.pricing.ollama.input_per_million": 1,
            "team.pricing.ollama.output_per_million": 2,
        })
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, factory = self.request(
                tmp,
                providers={"openai": first, "ollama": second},
                roles=roles,
                config=config,
            )
            attempt = service.review(request)
            self.assertEqual(factory.calls, ["openai", "ollama"])
            self.assertEqual(attempt.provider, "ollama")
            self.assertEqual(attempt.model, "review-model")
            self.assertIn("openai:bad-model", attempt.fallback_reason)
            self.assertGreater(attempt.usage.input_tokens, 0)
            self.assertIsNotNone(attempt.usage.estimated_cost_usd)

    def test_explicit_provider_assignment_is_honored(self):
        provider = FakeProvider("fixed-model", model_response())
        roles = DocumentationRoles(
            self.engine,
            requested="openai:fixed-model",
            candidates=[("openai", "fixed-model", "")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, factory = self.request(
                tmp,
                providers={"openai": provider},
                roles=roles,
            )
            attempt = service.review(request)
            self.assertEqual(factory.calls, ["openai"])
            self.assertEqual(attempt.reviewer_requested, "openai:fixed-model")
            self.assertEqual(attempt.reviewer_resolved, "openai:fixed-model")

    def test_unavailable_role_records_unavailable_without_provider_call(self):
        roles = DocumentationRoles(
            self.engine,
            available=False,
            fallback_reason="Provider is disabled.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, provider, _ = self.request(tmp, roles=roles)
            attempt = service.review(request)
            self.assertEqual(attempt.status, "unavailable")
            self.assertEqual(attempt.safe_error_category, "provider_unavailable")
            self.assertEqual(provider.calls, [])

    def test_provider_error_is_sanitized_by_bridge_error_envelope(self):
        roles = DocumentationRoles(self.engine)
        provider = FakeProvider("review-model", "", error=RuntimeError("sk-secret-never-persist"))
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(
                tmp,
                providers={"ollama": provider},
                roles=roles,
            )
            with self.assertRaises(RuntimeError) as captured:
                service.review(request)
            reason = sanitized_documentation_error(captured.exception)
            attempt = service.error(request, "provider_error", reason)
            payload = json.dumps(attempt.to_dict())
            self.assertEqual(attempt.status, "error")
            self.assertNotIn("sk-secret", payload)
            self.assertIn("[REDACTED]", payload)
            self.assertIn("ollama:review-model", payload)
            self.assertEqual(attempt.safe_error_category, "provider_error")

    def test_prompt_is_bounded_redacted_and_contains_no_source_or_vault_content(self):
        secret = "sk-thismustneverpersist123456"
        source_body = f"SOURCE_BODY_{secret}"
        with tempfile.TemporaryDirectory() as tmp:
            service, request, workspace, provider, _ = self.request(
                tmp,
                before_extra={"vault/vault.yaml": secret},
                after={
                    "feature.py": source_body,
                    "docs/USER_GUIDE.md": "# User Guide\n\nFeature.\n",
                    "CHANGELOG.md": "# Unreleased\n\n- Added feature.\n",
                },
                implementation_summary=f"Implemented with credential {secret}",
            )
            attempt = service.review(request)
            prompt_text = provider.calls[0][0]
            payload = json.dumps(attempt.to_dict())
            self.assertLess(len(prompt_text), 50_000)
            self.assertNotIn(secret, prompt_text + payload)
            self.assertNotIn("SOURCE_BODY_", prompt_text)
            self.assertNotIn("vault.yaml", prompt_text)
            self.assertEqual((workspace / "vault/vault.yaml").read_text(encoding="utf-8"), secret)

    def test_reviewer_workspace_mutation_is_detected_and_not_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, request, workspace, provider, _ = self.request(root)
            target = workspace / "feature.txt"
            provider.mutator = lambda: target.write_text("mutated\n", encoding="utf-8")
            attempt = service.review(request)
            self.assertEqual(attempt.status, "failed")
            self.assertEqual(target.read_text(encoding="utf-8"), "mutated\n")
            self.assertIn("safety", {item.category for item in attempt.findings})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, request, workspace, provider, _ = self.request(root)
            marker = workspace / ".git/reviewer-mutated"
            provider.mutator = lambda: marker.write_text("mutated\n", encoding="utf-8")
            attempt = service.review(replace(request, protected_baseline=None))
            self.assertEqual(attempt.status, "failed")
            self.assertTrue(marker.is_file())
            self.assertIn("safety", {item.category for item in attempt.findings})

    def test_immutable_attempt_artifacts_preserve_history_and_bound_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, request, _, _, _ = self.request(tmp)
            store = CodexBridgeStore(Path(tmp) / "user/codex")
            first = service.review(request)
            paths = store.write_documentation_attempt(first, service.documentation_log(first))
            restored = store.load_documentation_attempt(first.run_id, first.artifact_paths[0])
            self.assertEqual(restored.to_dict(), first.to_dict())
            self.assertLess(paths[1].stat().st_size, 100_000)
            second_request = replace(
                request,
                attempt_id="documentation-0002",
                artifact_paths=(
                    "documentation/documentation-0002.json",
                    "documentation/documentation-0002.log",
                ),
            )
            second = service.review(second_request)
            store.write_documentation_attempt(second, service.documentation_log(second))
            self.assertTrue((store.run_directory(first.run_id) / first.artifact_paths[0]).is_file())
            self.assertTrue((store.run_directory(first.run_id) / second.artifact_paths[0]).is_file())


class DocumentationWorkflowTests(unittest.TestCase):
    def bridge(self, root, *, provider_response=None, provider_error=None):
        root = Path(root)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / ".git").mkdir()
        for relative, content in DocumentationReviewerTests.common_docs().items():
            DocumentationReviewerTests.write(workspace, relative, content)
        capabilities = WorkspaceCapabilities.detect(workspace, which=lambda _name: None)
        store = TeamTaskStore(root / "user/team/tasks")
        task = TeamTask(
            task_id="team-documentation-001",
            goal="Add a documented widget feature",
            status=TEAM_STATUS_AWAITING_APPROVAL,
            artifacts=[TeamArtifact(
                role="engineer",
                kind="engineering_review",
                output=RoleOutput(
                    "Ready",
                    ("Add feature.txt and update User Guide and changelog",),
                    (),
                    "Approve",
                ),
                created_at="2026-07-20T11:00:00+00:00",
            )],
            final_plan=["Add feature.txt and update User Guide and changelog"],
            created_at="2026-07-20T11:00:00+00:00",
            updated_at="2026-07-20T11:00:00+00:00",
        )
        store.save(task)
        engine = ExecutionEngine(
            "codex", "Codex CLI", ENGINE_STATUS_INSTALLED, True, True, True,
            executable=str(root / "codex.cmd"),
        )
        result = {
            "summary": "Added a documented widget feature.",
            "files_changed": [
                {"path": "feature.txt", "summary": "Added feature."},
                {"path": "docs/USER_GUIDE.md", "summary": "Documented feature."},
                {"path": "CHANGELOG.md", "summary": "Added milestone entry."},
            ],
            "tests": [{"command": "manual", "status": "passed", "summary": "Passed."}],
            "risks": [], "remaining_work": [], "review_notes": [],
        }

        def mutate(cwd):
            (cwd / "feature.txt").write_text("widget ready\n", encoding="utf-8")
            (cwd / "docs/USER_GUIDE.md").write_text(
                "# User Guide\n\nDocumented widget feature.\n", encoding="utf-8"
            )
            (cwd / "CHANGELOG.md").write_text(
                "# Unreleased\n\n- Added widget feature.\n", encoding="utf-8"
            )

        runner = ImplementationRunner(result, mutate)
        config = FlatConfig()
        roles = DocumentationRoles(engine)
        provider = FakeProvider(
            "review-model",
            provider_response or model_response(),
            error=provider_error,
        )
        factory = FakeProviderFactory({"ollama": provider})
        bridge = CodexBridge(
            config,
            store,
            CodexBridgeStore(root / "user/codex"),
            workspace,
            workspace_capabilities=capabilities,
            runner=runner,
            capability_detector=StaticCapabilities(),
            team_roles=roles,
            provider_factory=factory,
            default_execution_engine=engine,
            now=lambda: datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
            approval_id_factory=lambda: "approval-documentation-001",
            run_id_factory=lambda: "run-documentation-001",
            platform_name="nt",
        )
        return bridge, workspace, runner, provider

    def test_documentation_runtime_error_preserves_sanitized_provider_reason(self):
        secret = "sk-secret-never-persist"
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, _, _ = self.bridge(
                tmp,
                provider_error=RuntimeError(
                    "local model runner ran out of memory; "
                    f"credential {secret}; path C:\\private\\model.bin"
                ),
            )
            approval = bridge.approve("team-documentation-001")
            run = bridge.execute(
                "team-documentation-001",
                approval.approval_id,
            )
            self.assertEqual(run.documentation.status, "error")
            diagnostics = " ".join(run.documentation.safe_diagnostics)
            self.assertIn("ran out of memory", diagnostics)
            self.assertIn("[REDACTED]", diagnostics)
            self.assertIn("[path]", diagnostics)
            self.assertNotIn(secret, json.dumps(run.documentation.to_dict()))
            restored = bridge.store.load_documentation_attempt(
                run.run_id,
                run.documentation_history[-1],
            )
            self.assertEqual(
                restored.safe_diagnostics,
                run.documentation.safe_diagnostics,
            )

    def test_automatic_documentation_review_follows_validation_and_is_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, runner, provider = self.bridge(tmp)
            approval = bridge.approve("team-documentation-001")
            run = bridge.execute("team-documentation-001", approval.approval_id)
            self.assertEqual(run.status, "awaiting_review")
            self.assertIsNotNone(run.validation)
            self.assertIsNotNone(run.documentation)
            self.assertEqual(run.documentation.status, "passed")
            self.assertEqual(len(run.documentation_history), 1)
            self.assertEqual(len(runner.calls), 1)
            self.assertEqual(len(provider.calls), 1)

    def test_rerun_preserves_attempts_without_implementation_validation_or_approval_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, runner, provider = self.bridge(tmp)
            approval = bridge.approve("team-documentation-001")
            first = bridge.execute("team-documentation-001", approval.approval_id)
            validation_history = first.validation_history
            claim = bridge.store.approval_claim_path(
                first.team_task_id, first.approval_id
            ).read_text(encoding="utf-8")
            second = bridge.document(first.run_id)
            self.assertEqual(len(second.documentation_history), 2)
            self.assertEqual(second.validation_history, validation_history)
            self.assertEqual(len(runner.calls), 1)
            self.assertEqual(len(provider.calls), 2)
            self.assertEqual(
                bridge.store.approval_claim_path(
                    first.team_task_id, first.approval_id
                ).read_text(encoding="utf-8"),
                claim,
            )

    def test_team_docs_last_explicit_and_show_use_the_same_bounded_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, _, _ = self.bridge(tmp)
            approval = bridge.approve("team-documentation-001")
            run = bridge.execute("team-documentation-001", approval.approval_id)
            router = CommandRouter(SimpleNamespace(codex_bridge=bridge))
            with patch("builtins.print") as output:
                router.handle("team docs last")
                router.handle(f"team docs {run.run_id}")
                router.handle(f"team docs show {run.run_id}")
            rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
            self.assertIn(f"Selected documentation run: {run.run_id}", rendered)
            self.assertIn("Documentation Review", rendered)
            self.assertIn("Overall Review Status", rendered)
            self.assertEqual(len(bridge.run(run.run_id).documentation_history), 3)

    def test_invalid_incomplete_workspace_mismatch_and_rolled_back_runs_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, workspace, _, _ = self.bridge(tmp)
            with self.assertRaises(FileNotFoundError):
                bridge.document("run-does-not-exist")
            approval = bridge.approve("team-documentation-001")
            run = bridge.execute("team-documentation-001", approval.approval_id)
            other = Path(tmp) / "other"
            other.mkdir()
            bridge.bind(other, WorkspaceCapabilities.detect(other, which=lambda _name: None))
            with self.assertRaises(PermissionError):
                bridge.document(run.run_id)
            bridge.bind(workspace, WorkspaceCapabilities.detect(workspace, which=lambda _name: None))
            rolled = bridge.rollback(run.run_id)
            self.assertEqual(rolled.status, "rolled_back")
            self.assertTrue(rolled.documentation_history)
            with self.assertRaisesRegex(ValueError, "Rolled-back"):
                bridge.document(run.run_id)

    def test_existing_schema_v2_run_without_documentation_fields_remains_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge, _, _, _ = self.bridge(tmp)
            approval = bridge.approve("team-documentation-001")
            run = bridge.execute("team-documentation-001", approval.approval_id)
            legacy = run.to_dict()
            legacy.pop("documentation")
            legacy.pop("documentation_history")
            restored = CodexRun.from_value(legacy)
            self.assertIsNone(restored.documentation)
            self.assertEqual(restored.documentation_history, ())

    def test_help_completion_and_user_guide_expose_all_documentation_commands(self):
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
        for command in ("team docs <run-id>", "team docs last", "team docs show <run-id>"):
            self.assertIn(command, rendered)
        base = __import__("orion.ui.console", fromlist=["BASE_COMMANDS"]).BASE_COMMANDS
        self.assertIn("team docs", base)
        self.assertIn("team docs last", base)
        self.assertIn("team docs show", base)

    def test_attempt_schema_rejects_secret_and_unknown_fields(self):
        finding_value = DocumentationFinding.from_value(finding()).to_dict()
        self.assertEqual(finding_value["severity"], "warning")
        invalid = dict(finding_value)
        invalid["unexpected"] = "value"
        with self.assertRaises(ValueError):
            DocumentationFinding.from_value(invalid)
        protected = dict(finding_value)
        protected["document"] = "vault/vault.yaml"
        with self.assertRaises(ValueError):
            DocumentationFinding.from_value(protected)
        self.assertEqual(DOCUMENTATION_SCHEMA_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
