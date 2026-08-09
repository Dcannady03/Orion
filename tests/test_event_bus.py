from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from orion.application.capabilities import default_capability_registry
from orion.application.commands.ai_team_commands import (
    AiTeamApplicationHandler,
    TeamPlanRequest,
)
from orion.application.commands.event_cli import EventCliAdapter, dispatch_events
from orion.application.events import (
    DiagnosticEventLogger,
    EventApplicationHandler,
    EventBus,
    EventDelivery,
    EventFactory,
    EventHistoryRequest,
    EventPublisher,
    EventReferenceRequest,
    EventSeverity,
    EventStore,
    EventTypes,
    InMemoryEventSubscriber,
    OrionEvent,
    canonical_json,
)
from orion.application.goals import (
    GoalApplicationHandler,
    GoalEngine,
    GoalRequest,
)
from orion.application.goals.proposals import (
    CreateGoalProposalRequest,
    GoalProposalAcceptance,
    GoalProposalApplicationHandler,
    GoalProposalRejection,
    GoalProposalRepository,
    GoalProposalService,
    GoalProposalTranslator,
)
from orion.application.goals.proposals.handler import (
    GoalProposalReferenceRequest,
)
from orion.application.results import ApplicationResult
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter
from orion.ui.console import BASE_COMMANDS


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.mutations = 0

    def set_workspace(self, _root: Path) -> None:
        self.mutations += 1
        raise AssertionError("Event observation must not change workspaces.")

    def refresh_capabilities(self) -> None:
        self.mutations += 1
        raise AssertionError("Event observation must not refresh workspaces.")


class _CommandCenter:
    def departments(self):
        return (
            SimpleNamespace(
                department_id="engineering",
                name="Engineering",
                enabled=True,
            ),
        )

    def create_job(self, *_args, **_kwargs):
        raise AssertionError("Event observation must not create jobs.")


class _TeamService:
    def __init__(self) -> None:
        self.calls = 0

    def plan(self, goal: str, **_kwargs):
        self.calls += 1
        return SimpleNamespace(
            task_id=f"team-task-{self.calls}",
            goal=goal,
            status="awaiting_approval",
            selected_agents=(),
            agent_snapshots=(),
            artifacts=(),
            role_assignments=(),
            final_plan=(),
            usage=(),
            error="",
            artifact=lambda _role: None,
        )


class _FailingEventStore(EventStore):
    def append(self, event: OrionEvent) -> Path:
        raise OSError("simulated store failure")


class _FailingSubscriber:
    def handle_event(self, _delivery: EventDelivery) -> None:
        raise RuntimeError("observer failed")


class _FailingTeamApplication:
    def plan(self, _request: TeamPlanRequest) -> ApplicationResult:
        return ApplicationResult.failure(
            "AI Team planning failed safely.",
            data={"status": "failed"},
            errors=("planning unavailable",),
        )


class EventModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        ids = iter(
            f"event-{character * 32}"
            for character in "abcdef0123456789"
        )
        self.factory = EventFactory(
            clock=self.clock,
            id_factory=lambda: next(ids),
        )

    def event(self, **overrides) -> OrionEvent:
        values = {
            "event_type": EventTypes.GOAL_PLAN_CREATED,
            "source": "goal_engine",
            "correlation_id": "goal-123",
            "subject_id": "goal-123",
            "data": {"nested": {"values": [1, 2]}},
        }
        values.update(overrides)
        return self.factory.create(**values)

    def test_event_is_immutable_and_defensively_copies_nested_payloads(self) -> None:
        payload = {"nested": {"values": [1, 2]}}
        event = self.event(data=payload)
        payload["nested"]["values"].append(3)

        self.assertEqual(event.to_dict()["data"]["nested"]["values"], [1, 2])
        with self.assertRaises(FrozenInstanceError):
            event.source = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            event.data["other"] = True  # type: ignore[index]
        with self.assertRaises(TypeError):
            event.data["nested"]["values"][0] = 9  # type: ignore[index]

    def test_event_round_trip_is_json_safe_and_canonical(self) -> None:
        event = self.event()
        reloaded = OrionEvent.from_value(json.loads(event.to_json()))

        self.assertEqual(reloaded, event)
        self.assertEqual(
            canonical_json({"b": 2, "a": 1}),
            '{"a":1,"b":2}',
        )
        json.dumps(event.to_dict(), allow_nan=False)

    def test_factory_assigns_unique_ids_and_utc_timestamps(self) -> None:
        first = self.event()
        self.clock.advance(seconds=1)
        second = self.event()

        self.assertNotEqual(first.event_id, second.event_id)
        self.assertEqual(first.occurred_at, "2026-07-31T12:00:00Z")
        self.assertTrue(second.occurred_at.endswith("Z"))
        self.assertEqual(first.schema_version, 1)

    def test_event_type_severity_timestamp_and_schema_are_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "not registered"):
            self.event(event_type="goal.plan.typo")
        with self.assertRaisesRegex(ValueError, "severity"):
            self.event(severity="urgent")
        with self.assertRaisesRegex(ValueError, "UTC"):
            OrionEvent(
                event_id=f"event-{'a' * 32}",
                event_type=EventTypes.GOAL_PLAN_CREATED,
                occurred_at="2026-07-31T12:00:00-07:00",
                source="goal_engine",
            )
        value = self.event().to_dict()
        value["schema_version"] = 2
        with self.assertRaisesRegex(ValueError, "schema version"):
            OrionEvent.from_value(value)

    def test_unsupported_objects_sensitive_keys_and_oversize_are_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "JSON primitives"):
            self.event(data={"path": Path("private.txt")})
        with self.assertRaisesRegex(ValueError, "sensitive field"):
            self.event(data={"api_key": "not-persisted"})
        with self.assertRaisesRegex(ValueError, "secret-like"):
            self.event(data={"summary": "Bearer abcdefghijklmnopqrstuvwxyz"})
        bounded = EventFactory(
            clock=self.clock,
            id_factory=lambda: f"event-{'f' * 32}",
            max_event_bytes=1_024,
        )
        with self.assertRaisesRegex(ValueError, "byte limit"):
            bounded.create(
                event_type=EventTypes.GOAL_PLAN_CREATED,
                source="goal_engine",
                data={"summary": "x" * 2_000},
            )


class EventStoreAndBusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.install_root = self.root / "application"
        self.install_root.mkdir()
        self.event_root = self.root / "user-data" / "events"
        self.clock = _Clock()
        self.factory = EventFactory(clock=self.clock)
        self.store = EventStore(
            self.event_root,
            forbidden_root=self.install_root,
        )
        self.bus = EventBus(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def event(
        self,
        event_type: str = EventTypes.GOAL_PLAN_CREATED,
        *,
        subject_id: str = "goal-1",
        correlation_id: str = "correlation-1",
        source: str = "goal_engine",
        severity: str = "info",
    ) -> OrionEvent:
        return self.factory.create(
            event_type=event_type,
            source=source,
            severity=severity,
            subject_id=subject_id,
            correlation_id=correlation_id,
            data={"sequence": self.clock.value.second},
        )

    def test_store_appends_canonical_daily_jsonl_without_rewriting(self) -> None:
        first = self.event()
        path = self.store.append(first)
        before = path.read_bytes()
        self.clock.advance(seconds=1)
        second = self.event(EventTypes.GOAL_PROPOSAL_CREATED)
        self.store.append(second)
        after = path.read_bytes()

        self.assertEqual(path.name, "2026-07-31.jsonl")
        self.assertTrue(after.startswith(before))
        lines = after.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], canonical_json(first.to_dict()))
        self.assertEqual(lines[1], canonical_json(second.to_dict()))

    def test_history_is_newest_first_bounded_and_filterable(self) -> None:
        first = self.event(subject_id="goal-1")
        self.store.append(first)
        self.clock.advance(seconds=1)
        second = self.event(
            EventTypes.GOAL_PROPOSAL_CREATED,
            subject_id="proposal-1",
            source="goal_proposals",
            severity="notice",
        )
        self.store.append(second)

        self.assertEqual(
            [item.event_id for item in self.store.history(limit=2).events],
            [second.event_id, first.event_id],
        )
        filtered = self.store.history(
            subject_id="proposal-1",
            source="goal_proposals",
            severity="notice",
            correlation_id="correlation-1",
        )
        self.assertEqual(filtered.events, (second,))
        with self.assertRaisesRegex(ValueError, "between"):
            self.store.history(limit=1_001)

    def test_store_rechecks_event_size_at_append_boundary(self) -> None:
        large_factory = EventFactory(
            clock=self.clock,
            max_event_bytes=4_096,
        )
        event = large_factory.create(
            event_type=EventTypes.GOAL_PLAN_CREATED,
            source="goal_engine",
            data={"summary": "x" * 2_000},
        )
        small_store = EventStore(
            self.root / "small-events",
            forbidden_root=self.install_root,
            max_event_bytes=1_024,
        )
        with self.assertRaisesRegex(ValueError, "byte limit"):
            small_store.append(event)

    def test_history_surfaces_malformed_and_unsupported_records(self) -> None:
        event = self.event()
        path = self.store.append(event)
        with path.open("ab") as handle:
            handle.write(b'{"partial":')
        self.clock.advance(seconds=1)
        recovered = self.event(EventTypes.GOAL_PROPOSAL_CREATED)
        self.store.append(recovered)
        history = self.store.history(limit=10)

        self.assertEqual(history.events, (recovered, event))
        self.assertTrue(any("Malformed" in item for item in history.warnings))

    def test_store_rejects_repository_location_and_symlinked_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "application repository"):
            EventStore(
                self.install_root / "events",
                forbidden_root=self.install_root,
            )
        target = self.root / "event-target"
        target.mkdir()
        link = self.root / "event-link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks are not available.")
        linked = EventStore(link, forbidden_root=self.install_root)
        with self.assertRaisesRegex(PermissionError, "symlink"):
            linked.append(self.event())

    def test_safe_concurrent_appends_preserve_every_event(self) -> None:
        events = tuple(self.event(subject_id=f"goal-{index}") for index in range(30))
        with ThreadPoolExecutor(max_workers=8) as executor:
            tuple(executor.map(self.store.append, events))

        history = self.store.history(limit=100)
        self.assertEqual(len(history.events), 30)
        self.assertEqual(
            {item.event_id for item in history.events},
            {item.event_id for item in events},
        )

    def test_publish_persists_before_ordered_delivery(self) -> None:
        calls: list[str] = []

        class _Observer:
            def __init__(self, name: str) -> None:
                self.name = name

            def handle_event(self, delivery: EventDelivery) -> None:
                persisted, _warnings = self_store.get(delivery.event.event_id)
                self.assertion = persisted.event_id == delivery.event.event_id
                calls.append(self.name)

        self_store = self.store
        first = _Observer("first")
        second = _Observer("second")
        self.bus.subscribe(first, name="first")
        self.bus.subscribe(second, name="second")
        result = self.bus.publish(self.event())

        self.assertTrue(result.persisted)
        self.assertEqual(result.delivered_count, 2)
        self.assertEqual(calls, ["first", "second"])
        self.assertTrue(first.assertion)
        self.assertTrue(second.assertion)

    def test_store_failure_prevents_subscriber_delivery(self) -> None:
        failing = EventBus(_FailingEventStore(
            self.root / "failed-publish",
            forbidden_root=self.install_root,
        ))
        observer = InMemoryEventSubscriber()
        failing.subscribe(observer, name="memory")
        with self.assertRaises(OSError):
            failing.publish(self.event())
        self.assertEqual(observer.deliveries, [])

    def test_subscriber_failure_duplicate_and_unsubscribe_are_isolated(self) -> None:
        memory = InMemoryEventSubscriber()
        failing_id = self.bus.subscribe(_FailingSubscriber(), name="failing")
        self.bus.subscribe(memory, name="memory")
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.bus.subscribe(InMemoryEventSubscriber(), name="memory")

        result = self.bus.publish(self.event())
        self.assertEqual(result.delivered_count, 1)
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(len(memory.deliveries), 1)
        self.assertTrue(self.bus.unsubscribe(failing_id))
        self.assertFalse(self.bus.unsubscribe(failing_id))

    def test_registration_uses_publish_snapshot_and_results_are_ignored(self) -> None:
        late = InMemoryEventSubscriber()

        class _Registering:
            def handle_event(inner_self, _delivery: EventDelivery):
                if not self.bus.list_subscribers()[-1].name == "late":
                    self.bus.subscribe(late, name="late")
                return {"action": "must be ignored"}

        self.bus.subscribe(_Registering(), name="registering")
        first = self.bus.publish(self.event())
        self.clock.advance(seconds=1)
        second = self.bus.publish(self.event())

        self.assertEqual(first.delivered_count, 1)
        self.assertEqual(second.delivered_count, 2)
        self.assertEqual(len(late.deliveries), 1)

    def test_recursive_publish_is_blocked_without_recursive_record(self) -> None:
        nested = self.event(EventTypes.GOAL_PROPOSAL_CREATED)

        class _Recursive:
            def handle_event(inner_self, _delivery: EventDelivery) -> None:
                self.bus.publish(nested)

        self.bus.subscribe(_Recursive(), name="recursive")
        result = self.bus.publish(self.event())

        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(len(self.store.history(limit=10).events), 1)

    def test_replay_is_bounded_marked_and_never_repersisted(self) -> None:
        for _index in range(3):
            self.bus.publish(self.event())
            self.clock.advance(seconds=1)
        path = self.event_root / "2026-07-31.jsonl"
        before = path.read_bytes()
        observer = InMemoryEventSubscriber()

        replay = self.bus.replay(observer, limit=2)

        self.assertEqual(replay.delivered_count, 2)
        self.assertEqual(len(observer.deliveries), 2)
        self.assertTrue(all(item.replayed for item in observer.deliveries))
        self.assertEqual(
            replay.event_ids,
            tuple(item.event.event_id for item in observer.deliveries),
        )
        self.assertEqual(path.read_bytes(), before)

    def test_replay_callback_cannot_publish_a_new_event(self) -> None:
        self.bus.publish(self.event())
        nested = self.event(EventTypes.GOAL_PROPOSAL_CREATED)

        class _PublishingReplayObserver:
            def handle_event(inner_self, _delivery: EventDelivery) -> None:
                self.bus.publish(nested)

        before = len(self.store.history(limit=10).events)
        result = self.bus.replay(_PublishingReplayObserver(), limit=1)
        after = len(self.store.history(limit=10).events)

        self.assertEqual(result.delivered_count, 0)
        self.assertTrue(result.warnings)
        self.assertEqual(after, before)

    def test_paths_keep_event_store_outside_repository(self) -> None:
        paths = OrionPaths(
            install_root=self.install_root,
            user_root=self.root / "external-user",
        )
        self.assertEqual(
            paths.events,
            (self.root / "external-user" / "events").resolve(),
        )
        self.assertFalse(str(paths.events).startswith(str(paths.install_root)))


class EventIntegrationAndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace_root = self.root / "workspace"
        self.workspace_root.mkdir()
        self.install_root = self.root / "application"
        self.install_root.mkdir()
        self.clock = _Clock()
        self.store = EventStore(
            self.root / "user-data" / "events",
            forbidden_root=self.install_root,
        )
        self.factory = EventFactory(clock=self.clock)
        self.bus = EventBus(self.store)
        self.memory = InMemoryEventSubscriber()
        self.bus.subscribe(self.memory, name="memory")
        self.publisher = EventPublisher(self.factory, self.bus)
        self.workspace = _Workspace(self.workspace_root)
        self.command_center = _CommandCenter()
        self.registry = default_capability_registry()
        self.engine = GoalEngine(
            self.registry,
            workspace_manager=self.workspace,
            project_context=SimpleNamespace(
                workspace_root=self.workspace_root,
                initialized=False,
            ),
            command_center=self.command_center,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def proposal_components(self):
        repository = GoalProposalRepository(
            self.root / "user-data" / "goals" / "proposals",
            forbidden_root=self.install_root,
        )
        runtime = SimpleNamespace(
            team=_TeamService(),
            event_publisher=self.publisher,
        )
        team_application = AiTeamApplicationHandler(runtime)
        service = GoalProposalService(
            repository,
            self.registry,
            translator=GoalProposalTranslator(),
            team_application=team_application,
            workspace_manager=self.workspace,
            command_center=self.command_center,
            now=self.clock,
            event_publisher=self.publisher,
        )
        handler = GoalProposalApplicationHandler(
            self.engine,
            service,
            event_publisher=self.publisher,
        )
        return repository, service, handler, runtime

    def test_goal_plan_success_emits_one_safe_event_and_failure_emits_none(self) -> None:
        handler = GoalApplicationHandler(
            self.engine,
            event_publisher=self.publisher,
        )
        result = handler.plan(GoalRequest("Prepare Orion for release."))
        failure = handler.plan(GoalRequest("Please do the thing."))

        self.assertTrue(result.ok)
        self.assertFalse(failure.ok)
        events = self.store.history(limit=10).events
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, EventTypes.GOAL_PLAN_CREATED)
        self.assertNotIn("goal", events[0].data)
        self.assertEqual(self.workspace.mutations, 0)

    def test_proposal_create_validate_reject_emit_after_persistence(self) -> None:
        repository, _service, handler, _runtime = self.proposal_components()
        created = handler.create(CreateGoalProposalRequest(
            GoalRequest("Prepare Orion for release.")
        ))
        proposal_id = created.data["proposal_id"]
        validated = handler.validate(GoalProposalReferenceRequest(proposal_id))
        rejected = handler.reject(GoalProposalRejection(
            proposal_id,
            rejected_by="operator",
            reason="Wrong workspace",
        ))

        self.assertTrue(created.ok)
        self.assertTrue(validated.ok)
        self.assertTrue(rejected.ok)
        self.assertEqual(repository.get(proposal_id).status.value, "rejected")
        types = [
            item.event_type
            for item in reversed(self.store.history(limit=10).events)
        ]
        self.assertEqual(types, [
            EventTypes.GOAL_PROPOSAL_CREATED,
            EventTypes.GOAL_PROPOSAL_VALIDATED,
            EventTypes.GOAL_PROPOSAL_REJECTED,
        ])

    def test_acceptance_team_plan_and_consumption_propagate_correlation(self) -> None:
        repository, _service, handler, runtime = self.proposal_components()
        created = handler.create(CreateGoalProposalRequest(
            GoalRequest("Prepare Orion for release.")
        ))

        observed_acceptance_states: list[str] = []

        class _AcceptanceObserver:
            def handle_event(inner_self, delivery: EventDelivery) -> None:
                if delivery.event.event_type == EventTypes.GOAL_PROPOSAL_ACCEPTED:
                    observed_acceptance_states.append(
                        repository.get(created.data["proposal_id"]).status.value
                    )

        self.bus.subscribe(_AcceptanceObserver(), name="acceptance-state")
        accepted = handler.accept(GoalProposalAcceptance(
            created.data["proposal_id"],
            created.data["plan_hash"],
            True,
            accepted_by="operator",
        ))

        self.assertTrue(accepted.ok)
        self.assertEqual(accepted.data["status"], "consumed")
        events = tuple(reversed(self.store.history(limit=20).events))
        accepted_event = next(
            item for item in events
            if item.event_type == EventTypes.GOAL_PROPOSAL_ACCEPTED
        )
        team_event = next(
            item for item in events
            if item.event_type == EventTypes.TEAM_PLAN_CREATED
        )
        consumed_event = next(
            item for item in events
            if item.event_type == EventTypes.GOAL_PROPOSAL_CONSUMED
        )
        self.assertEqual(team_event.correlation_id, created.data["goal_id"])
        self.assertEqual(team_event.causation_id, accepted_event.event_id)
        self.assertEqual(consumed_event.causation_id, accepted_event.event_id)
        self.assertEqual(runtime.team.calls, 1)
        self.assertEqual(observed_acceptance_states, ["accepted"])
        self.assertEqual(
            accepted.data["application_result"]["data"]["approval_status"],
            "pending",
        )

    def test_expiry_and_failed_dispatch_emit_terminal_lifecycle_events(self) -> None:
        repository, service, handler, _runtime = self.proposal_components()
        expiring = handler.create(CreateGoalProposalRequest(
            GoalRequest("Prepare Orion for release."),
            expiry_hours=1,
        ))
        self.clock.advance(hours=2)
        expired = handler.accept(GoalProposalAcceptance(
            expiring.data["proposal_id"],
            expiring.data["plan_hash"],
            True,
        ))
        self.assertFalse(expired.ok)
        self.assertEqual(
            repository.get(expiring.data["proposal_id"]).status.value,
            "expired",
        )

        service.team_application = _FailingTeamApplication()
        failing = handler.create(CreateGoalProposalRequest(
            GoalRequest("Prepare another Orion release.")
        ))
        failed = handler.accept(GoalProposalAcceptance(
            failing.data["proposal_id"],
            failing.data["plan_hash"],
            True,
        ))
        self.assertFalse(failed.ok)
        self.assertEqual(failed.data["status"], "failed")
        event_types = {
            item.event_type for item in self.store.history(limit=30).events
        }
        self.assertIn(EventTypes.GOAL_PROPOSAL_EXPIRED, event_types)
        self.assertIn(EventTypes.GOAL_PROPOSAL_FAILED, event_types)

    def test_team_plan_event_is_only_emitted_after_success(self) -> None:
        runtime = SimpleNamespace(
            team=_TeamService(),
            event_publisher=self.publisher,
        )
        handler = AiTeamApplicationHandler(runtime)
        failure = handler.plan(TeamPlanRequest(""))
        success = handler.plan(TeamPlanRequest(
            "Plan safely",
            correlation_id="goal-correlation",
        ))

        self.assertFalse(failure.ok)
        self.assertTrue(success.ok)
        events = self.store.history(
            event_type=EventTypes.TEAM_PLAN_CREATED,
            limit=10,
        ).events
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].correlation_id, "goal-correlation")

    def test_event_store_failure_preserves_domain_state_and_surfaces_warning(self) -> None:
        failing_bus = EventBus(_FailingEventStore(
            self.root / "failed-events",
            forbidden_root=self.install_root,
        ))
        publisher = EventPublisher(self.factory, failing_bus)
        repository = GoalProposalRepository(
            self.root / "user-data" / "goals" / "failure-proposals",
            forbidden_root=self.install_root,
        )
        service = GoalProposalService(
            repository,
            self.registry,
            translator=GoalProposalTranslator(),
            team_application=None,
            workspace_manager=self.workspace,
            command_center=self.command_center,
            now=self.clock,
            event_publisher=publisher,
        )
        handler = GoalProposalApplicationHandler(
            self.engine,
            service,
            event_publisher=publisher,
        )
        result = handler.create(CreateGoalProposalRequest(
            GoalRequest("Prepare Orion for release.")
        ))

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "warning")
        self.assertTrue(result.warnings)
        self.assertEqual(
            repository.get(result.data["proposal_id"]).status.value,
            "pending",
        )

    def test_read_only_handler_and_cli_cover_history_without_publish(self) -> None:
        event = self.factory.create(
            event_type=EventTypes.GOAL_PLAN_CREATED,
            source="goal_engine",
            correlation_id="correlation-1",
            subject_id="goal-1",
            data={"planning_only": True},
        )
        self.bus.publish(event)
        application = EventApplicationHandler(self.bus, self.store)
        runtime = SimpleNamespace(event_application=application)
        output: list[str] = []
        adapter = EventCliAdapter(runtime, output_provider=output.append)
        before = (self.root / "user-data" / "events" / "2026-07-31.jsonl").read_bytes()

        self.assertTrue(adapter.handle("status").ok)
        self.assertEqual(adapter.handle("list --limit 1").data["count"], 1)
        self.assertEqual(adapter.handle(f"show {event.event_id}").data["event_id"], event.event_id)
        self.assertEqual(adapter.handle("correlation correlation-1").data["count"], 1)
        self.assertEqual(adapter.handle("subject goal-1").data["count"], 1)
        self.assertTrue(adapter.handle("types").ok)
        self.assertTrue(adapter.handle("subscribers").ok)
        self.assertFalse(adapter.handle("publish goal.fake.created").ok)
        self.assertEqual(
            (self.root / "user-data" / "events" / "2026-07-31.jsonl").read_bytes(),
            before,
        )
        self.assertTrue(output)

    def test_router_delegates_and_completions_expose_only_read_commands(self) -> None:
        application = EventApplicationHandler(self.bus, self.store)
        runtime = SimpleNamespace(event_application=application)
        with redirect_stdout(StringIO()):
            self.assertTrue(CommandRouter(runtime).handle("events status"))

        with redirect_stdout(StringIO()):
            self.assertTrue(dispatch_events(runtime, "events types"))
        self.assertFalse(dispatch_events(runtime, "goal plan"))
        for command in (
            "events status",
            "events list",
            "events show",
            "events correlation",
            "events subject",
            "events types",
            "events subscribers",
        ):
            self.assertIn(command, BASE_COMMANDS)
        self.assertNotIn("events publish", BASE_COMMANDS)
        unavailable = EventCliAdapter(
            SimpleNamespace(),
            output_provider=lambda _text: None,
        ).handle("status")
        self.assertFalse(unavailable.ok)

    def test_diagnostic_subscriber_observes_without_publishing(self) -> None:
        diagnostic = DiagnosticEventLogger()
        self.bus.subscribe(diagnostic, name="diagnostic")
        before = len(self.store.history(limit=10).events)
        result = self.bus.publish(self.factory.create(
            event_type=EventTypes.GOAL_PLAN_CREATED,
            source="goal_engine",
            data={"planning_only": True},
        ))
        after = len(self.store.history(limit=10).events)

        self.assertEqual(after, before + 1)
        self.assertEqual(result.delivered_count, 2)
