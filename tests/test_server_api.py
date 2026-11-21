"""Contract tests for the local API (server Phase A).

Every endpoint: request/response shapes, error cases, unknown routes,
and the 202 contract (run/operation ID returned, durable record exists
before the response, record pollable after detached execution).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from orion.application.results import ApplicationResult
from orion.server.api import ApiRequest, LocalApi
from orion.server.executor import DetachedExecutor
from tests.test_command_center_workflow import (
    JOB_ID,
    TEAM_TASK_ID,
    WorkflowFixture,
)
from tests.test_mission_coordinator import (
    MissionCoordinatorFixture,
)
from tests.test_mission_coordinator_phase2 import (
    RUN_ID,
    MissionCoordinatorPhase2Fixture,
)


class FakeObservability:
    """Observability handler double with the real failure shapes."""

    def __init__(self) -> None:
        self.health_calls: list = []
        self.logs_calls: list = []

    def health(self, section=None):
        self.health_calls.append(section)
        if section is not None and section not in {"system", "providers"}:
            return ApplicationResult.failure(
                f"Unknown health section: {section}",
                data={"read_only": True, "sections": ["system", "providers"]},
                errors=(f"Unknown health section: {section}",),
            )
        return ApplicationResult.success(
            "Health.",
            data={"overall": "ok", "read_only": True},
        )

    def logs(self, request=None):
        self.logs_calls.append(request)
        return ApplicationResult.success(
            "Logs.",
            data={
                "entries": [],
                "limit": request.limit if request is not None else 50,
                "read_only": True,
            },
        )

    def diagnostics(self):
        return ApplicationResult.success(
            "Diagnostics.",
            data={"generated_at": "2026-08-08T12:00:00Z", "read_only": True},
        )


class FakeBridge:
    def __init__(self) -> None:
        self.runs: dict[str, object] = {}

    def run(self, run_id: str):
        if run_id not in self.runs:
            raise FileNotFoundError(f"AI Team run not found: {run_id}")
        return self.runs[run_id]


class FakeTeamApproval:
    def __init__(self) -> None:
        self.approve_calls: list = []
        self.result = ApplicationResult.success(
            "Team plan approved.",
            data={
                "team_task_id": TEAM_TASK_ID,
                "status": "approved",
                "approval_id": "approval-api-001",
                "plan_sha256": "f" * 64,
            },
        )

    def approve(self, request):
        self.approve_calls.append(request)
        return self.result


def fake_run(run_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id,
        team_task_id=TEAM_TASK_ID,
        status="awaiting_review",
        approval_id="approval-api-001",
        plan_hash="f" * 64,
        workspace_root="/tmp/workspace",
        workspace=SimpleNamespace(mode="standard", branch="", commit=""),
        started_at="2026-08-08T12:00:00Z",
        completed_at="2026-08-08T12:01:00Z",
        validation=None,
        documentation=None,
        final_review=None,
        result=SimpleNamespace(tests=(), risks=()),
        changes=None,
        error="",
    )


class ApiFixture:
    """Builds the LocalApi over real CC + mission services."""

    def build_api(self) -> None:
        self._cc_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._cc_temp.cleanup)
        cc_root = Path(self._cc_temp.name)
        self.cc_service, self.integration, self.fake_team, _, _ = (
            WorkflowFixture().build(cc_root)
        )
        self.cc_root = cc_root
        self.observability = FakeObservability()
        self.bridge = FakeBridge()
        self.team_approval = FakeTeamApproval()
        self.executor = DetachedExecutor()
        self.executor.start()
        self.addCleanup(self.executor.stop, drain_seconds=2.0)
        runtime = SimpleNamespace(
            version="0.8.0",
            command_center=self.cc_service,
            command_center_team=self.integration,
            missions=self.service,
            mission_coordinator=self.coordinator,
            mission_coordination_repository=self.audit_repository,
            team=self.fake_team,
            team_application=self.team_approval,
            codex_bridge=self.bridge,
            observability_application=self.observability,
        )
        self.api = LocalApi(runtime, executor=self.executor, version="0.8.0")

    def call(
        self,
        method: str,
        path: str,
        *,
        query: dict | None = None,
        body: dict | None = None,
    ):
        return self.api.handle(ApiRequest(
            method=method,
            path=path,
            query=query or {},
            body=body,
        ))

    def create_cc_job(self, **overrides):
        return WorkflowFixture.create_job(
            self.cc_service, str(self.cc_root), **overrides
        )


class ApiReadTests(MissionCoordinatorFixture, ApiFixture):
    def setUp(self) -> None:
        super().setUp()
        self.build_api()

    def test_healthz_reports_liveness_and_clean_reconciliation(self) -> None:
        response = self.call("GET", "/api/healthz")
        self.assertEqual(response.status, 200)
        data = response.body["data"]
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["version"], "0.8.0")
        self.assertTrue(data["reconciliation"]["clean"])
        self.assertEqual(data["in_flight_operations"], [])

    def test_unknown_route_is_404(self) -> None:
        response = self.call("GET", "/api/nope")
        self.assertEqual(response.status, 404)
        self.assertEqual(response.body["status"], "failure")

    def test_wrong_method_is_405(self) -> None:
        response = self.call("DELETE", "/api/jobs")
        self.assertEqual(response.status, 405)

    def test_snapshot_returns_command_center_contract(self) -> None:
        self.create_cc_job()
        response = self.call("GET", "/api/snapshot")
        self.assertEqual(response.status, 200)
        data = response.body["data"]
        self.assertIn("schema_version", data)
        self.assertIn("active_jobs", data)
        self.assertIn("queued_jobs", data)

    def test_list_jobs_empty_then_paged(self) -> None:
        empty = self.call("GET", "/api/jobs")
        self.assertEqual(empty.body["data"]["jobs"], [])
        self.create_cc_job()
        self.create_cc_job(
            job_id="job-command-center-002",
            title="Second job",
        )
        first = self.call("GET", "/api/jobs", query={"limit": "1"})
        self.assertEqual(first.status, 200)
        self.assertEqual(len(first.body["data"]["jobs"]), 1)
        cursor = first.body["data"]["next_cursor"]
        self.assertTrue(cursor)
        second = self.call(
            "GET", "/api/jobs", query={"limit": "1", "cursor": cursor}
        )
        self.assertEqual(len(second.body["data"]["jobs"]), 1)
        self.assertNotEqual(
            first.body["data"]["jobs"][0]["id"],
            second.body["data"]["jobs"][0]["id"],
        )

    def test_list_jobs_bad_limit_and_cursor(self) -> None:
        self.assertEqual(
            self.call("GET", "/api/jobs", query={"limit": "abc"}).status, 400
        )
        self.assertEqual(
            self.call("GET", "/api/jobs", query={"limit": "0"}).status, 400
        )
        self.assertEqual(
            self.call(
                "GET", "/api/jobs", query={"cursor": "job-nope"}
            ).status,
            400,
        )

    def test_get_job_and_404(self) -> None:
        self.create_cc_job()
        response = self.call("GET", f"/api/jobs/{JOB_ID}")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["data"]["id"], JOB_ID)
        self.assertEqual(response.body["data"]["title"], "Build workflow integration")
        self.assertEqual(self.call("GET", "/api/jobs/job-nope").status, 404)

    def test_activity_returns_newest_first(self) -> None:
        self.create_cc_job()
        response = self.call("GET", "/api/activity")
        self.assertEqual(response.status, 200)
        events = response.body["data"]["events"]
        self.assertTrue(events)
        self.assertIn("id", events[0])
        # Newest first: the job creation is the most recent event.
        self.assertEqual(events[0]["event_type"], "job.created")
        self.assertEqual(
            self.call("GET", "/api/activity", query={"limit": "0"}).status, 400
        )

    def test_doctor_health_logs(self) -> None:
        self.assertEqual(self.call("GET", "/api/doctor").status, 200)
        health = self.call("GET", "/api/health")
        self.assertEqual(health.status, 200)
        self.assertEqual(health.body["data"]["overall"], "ok")
        self.assertEqual(
            self.call("GET", "/api/health", query={"section": "nope"}).status,
            400,
        )
        logs = self.call("GET", "/api/logs", query={"limit": "5"})
        self.assertEqual(logs.status, 200)
        self.assertEqual(logs.body["data"]["limit"], 5)
        self.assertEqual(
            self.call("GET", "/api/logs", query={"limit": "9999"}).status, 400
        )

    def test_list_missions_with_status_filter(self) -> None:
        response = self.call("GET", "/api/missions")
        self.assertEqual(response.status, 200)
        missions = response.body["data"]["missions"]
        self.assertEqual(len(missions), 1)
        self.assertEqual(missions[0]["mission_id"], self.mission.mission_id)
        filtered = self.call(
            "GET", "/api/missions", query={"status": "awaiting_approval"}
        )
        self.assertEqual(len(filtered.body["data"]["missions"]), 1)
        none = self.call("GET", "/api/missions", query={"status": "completed"})
        self.assertEqual(len(none.body["data"]["missions"]), 0)
        self.assertEqual(
            self.call("GET", "/api/missions", query={"status": "nope"}).status,
            400,
        )

    def test_get_mission_and_history(self) -> None:
        mission_id = self.mission.mission_id
        response = self.call("GET", f"/api/missions/{mission_id}")
        self.assertEqual(response.status, 200)
        data = response.body["data"]
        self.assertEqual(data["mission_id"], mission_id)
        self.assertIsNone(data["coordination_audit"])
        self.assertIn("next_actions", data)
        self.assertEqual(
            self.call("GET", "/api/missions/" + "mission-" + "0" * 32).status,
            404,
        )
        history = self.call("GET", f"/api/missions/{mission_id}/history")
        self.assertEqual(history.status, 200)
        self.assertTrue(history.body["data"]["events"])
        self.assertEqual(history.body["data"]["attempt_history"], [])

    def test_get_team_task_404_before_launch(self) -> None:
        self.assertEqual(
            self.call("GET", f"/api/team/tasks/{TEAM_TASK_ID}").status, 404
        )

    def test_get_team_run(self) -> None:
        self.assertEqual(
            self.call("GET", f"/api/team/runs/{RUN_ID}").status, 404
        )
        self.bridge.runs[RUN_ID] = fake_run(RUN_ID)
        response = self.call("GET", f"/api/team/runs/{RUN_ID}")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["data"]["run_id"], RUN_ID)
        self.assertEqual(response.body["data"]["status"], "awaiting_review")


class ApiJobMutationTests(MissionCoordinatorFixture, ApiFixture):
    def setUp(self) -> None:
        super().setUp()
        self.build_api()

    def test_create_job_returns_201_view(self) -> None:
        response = self.call("POST", "/api/jobs", body={
            "title": "API job",
            "goal": "Do the thing.",
            "department": "Engineering",
            "priority": "high",
        })
        self.assertEqual(response.status, 201)
        data = response.body["data"]
        self.assertEqual(data["title"], "API job")
        self.assertEqual(data["priority"], "high")
        self.assertEqual(data["status"], "draft")

    def test_create_job_validation(self) -> None:
        self.assertEqual(
            self.call("POST", "/api/jobs", body={"goal": "x"}).status, 400
        )
        self.assertEqual(
            self.call("POST", "/api/jobs", body={"title": "x"}).status, 400
        )
        self.assertEqual(
            self.call("POST", "/api/jobs", body={
                "title": "x", "goal": "y", "priority": "nope",
            }).status,
            400,
        )
        self.assertEqual(
            self.call("POST", "/api/jobs", body={
                "title": "x", "goal": "y", "department": "No Such Dept",
            }).status,
            400,
        )

    def test_launch_preview_and_unknown_job(self) -> None:
        self.create_cc_job()
        response = self.call("POST", f"/api/jobs/{JOB_ID}/launch/preview", body={})
        self.assertEqual(response.status, 200)
        self.assertTrue(response.body["data"]["allowed"])
        self.assertIsNotNone(response.body["data"]["workflow"])
        self.assertEqual(
            self.call("POST", "/api/jobs/job-nope/launch/preview", body={}).status,
            404,
        )

    def test_launch_returns_202_with_durable_record_first(self) -> None:
        self.create_cc_job()
        response = self.call("POST", f"/api/jobs/{JOB_ID}/launch", body={})
        self.assertEqual(response.status, 202)
        data = response.body["data"]
        self.assertEqual(data["team_task_id"], TEAM_TASK_ID)
        self.assertEqual(data["operation_id"], TEAM_TASK_ID)
        self.assertEqual(data["poll_url"], f"/api/jobs/{JOB_ID}")
        # The durable record exists before the 202 returns: the job is
        # already linked and PLANNING, with no planning call yet needed.
        stored = self.cc_service.job(JOB_ID)
        self.assertEqual(stored.status.value, "planning")
        # After the detached execution drains, planning ran exactly once
        # and the job is pollable in its synced state.
        self.assertEqual(self.executor.drain(5.0), ())
        self.assertEqual(len(self.fake_team.plan_calls), 1)
        polled = self.call("GET", f"/api/jobs/{JOB_ID}")
        self.assertEqual(polled.body["data"]["team_task_id"], TEAM_TASK_ID)
        task = self.call("GET", f"/api/team/tasks/{TEAM_TASK_ID}")
        self.assertEqual(task.status, 200)
        self.assertEqual(task.body["data"]["team_task_id"], TEAM_TASK_ID)

    def test_launch_unknown_job_and_invalid_job(self) -> None:
        self.assertEqual(
            self.call("POST", "/api/jobs/job-nope/launch", body={}).status, 404
        )
        self.cc_service.create_job(
            job_id="job-no-department",
            title="No department",
            goal="Nothing to launch.",
            department="",
            workspace_reference=str(self.cc_root),
        )
        response = self.call(
            "POST", "/api/jobs/job-no-department/launch", body={}
        )
        self.assertEqual(response.status, 422)
        self.assertIn("preview", response.body["data"])

    def test_sync_requires_active_link(self) -> None:
        self.create_cc_job()
        self.assertEqual(
            self.call("POST", f"/api/jobs/{JOB_ID}/sync", body={}).status, 409
        )
        self.call("POST", f"/api/jobs/{JOB_ID}/launch", body={})
        self.assertEqual(self.executor.drain(5.0), ())
        response = self.call("POST", f"/api/jobs/{JOB_ID}/sync", body={})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["data"]["job_id"], JOB_ID)

    def test_cancel_unlinked_job(self) -> None:
        self.create_cc_job()
        response = self.call("POST", f"/api/jobs/{JOB_ID}/cancel", body={})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["data"]["status"], "cancelled")
        self.assertEqual(
            self.call("POST", "/api/jobs/job-nope/cancel", body={}).status, 404
        )

    def test_approve_team_task(self) -> None:
        self.create_cc_job()
        self.call("POST", f"/api/jobs/{JOB_ID}/launch", body={})
        self.assertEqual(self.executor.drain(5.0), ())
        response = self.call(
            "POST", f"/api/team/tasks/{TEAM_TASK_ID}/approve", body={}
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["data"]["status"], "approved")
        self.assertEqual(len(self.team_approval.approve_calls), 1)
        self.assertEqual(
            self.call(
                "POST", "/api/team/tasks/team-nope/approve", body={}
            ).status,
            404,
        )

    def test_approve_plan_hash_mismatch_is_409(self) -> None:
        self.create_cc_job()
        self.call("POST", f"/api/jobs/{JOB_ID}/launch", body={})
        self.assertEqual(self.executor.drain(5.0), ())
        response = self.call(
            "POST",
            f"/api/team/tasks/{TEAM_TASK_ID}/approve",
            body={"plan_sha256": "a" * 64},
        )
        self.assertEqual(response.status, 409)
        self.assertFalse(self.team_approval.approve_calls)


if __name__ == "__main__":
    unittest.main()


class ApiMissionMutationTests(MissionCoordinatorFixture, ApiFixture):
    def setUp(self) -> None:
        super().setUp()
        self.build_api()

    def _next(self):
        return self.call(
            "POST", f"/api/missions/{self.mission.mission_id}/next", body={}
        )

    def test_mission_next_returns_preview_with_token(self) -> None:
        response = self._next()
        self.assertEqual(response.status, 200)
        data = response.body["data"]
        self.assertEqual(data["capability_id"], "team.approve")
        self.assertTrue(data["advance_token"])
        self.assertFalse(data["blocked"])
        self.assertEqual(
            self.call(
                "POST", "/api/missions/" + "mission-" + "0" * 32 + "/next",
                body={},
            ).status,
            404,
        )

    def test_mission_advance_fast_runs_inline_200(self) -> None:
        token = self._next().body["data"]["advance_token"]
        response = self.call(
            "POST",
            f"/api/missions/{self.mission.mission_id}/advance",
            body={"advance_token": token, "confirmed": True},
        )
        self.assertEqual(response.status, 200)
        data = response.body["data"]
        self.assertEqual(data["capability_id"], "team.approve")
        self.assertEqual(data["audit_state"], "succeeded")
        self.assertEqual(data["new_status"], "approved")
        self.assertEqual(len(self.team_application.approve_calls), 1)

    def test_mission_advance_requires_token_and_confirmation(self) -> None:
        mission_id = self.mission.mission_id
        self.assertEqual(
            self.call(
                "POST", f"/api/missions/{mission_id}/advance",
                body={"confirmed": True},
            ).status,
            400,
        )
        token = self._next().body["data"]["advance_token"]
        self.assertEqual(
            self.call(
                "POST", f"/api/missions/{mission_id}/advance",
                body={"advance_token": token},
            ).status,
            400,
        )
        self.assertEqual(
            self.call(
                "POST", f"/api/missions/{mission_id}/advance",
                body={"advance_token": token, "confirmed": False},
            ).status,
            400,
        )

    def test_mission_advance_stale_token_is_409(self) -> None:
        response = self.call(
            "POST",
            f"/api/missions/{self.mission.mission_id}/advance",
            body={"advance_token": "0" * 64, "confirmed": True},
        )
        self.assertEqual(response.status, 409)

    def test_mission_resolve_preview_and_did_not_run(self) -> None:
        mission_id = self.mission.mission_id
        preview = self.call(
            "POST", f"/api/missions/{mission_id}/resolve/preview", body={}
        )
        self.assertEqual(preview.status, 200)
        self.assertEqual(preview.body["data"]["mission_id"], mission_id)
        # Reserve an advance, then resolve it as did-not-run via the API.
        token = self._next().body["data"]["advance_token"]
        from orion.application.missions import MissionAdvanceRequest

        self.coordinator.dispatch(MissionAdvanceRequest(
            mission_id, token, confirmed=True, actor="operator"
        ))
        resolved = self.call(
            "POST",
            f"/api/missions/{mission_id}/resolve",
            body={"finding": "did_not_run", "confirmed": True},
        )
        self.assertEqual(resolved.status, 200)
        self.assertEqual(resolved.body["data"]["audit_outcome"], "cleared")
        self.assertIsNone(self.audit_repository.get(mission_id))

    def test_mission_resolve_cannot_determine_is_422(self) -> None:
        response = self.call(
            "POST",
            f"/api/missions/{self.mission.mission_id}/resolve",
            body={"finding": "cannot_determine", "confirmed": True},
        )
        self.assertEqual(response.status, 422)

    def test_mission_resolve_requires_finding_and_confirmation(self) -> None:
        mission_id = self.mission.mission_id
        self.assertEqual(
            self.call(
                "POST", f"/api/missions/{mission_id}/resolve",
                body={"confirmed": True},
            ).status,
            400,
        )
        self.assertEqual(
            self.call(
                "POST", f"/api/missions/{mission_id}/resolve",
                body={"finding": "ran"},
            ).status,
            400,
        )
        self.assertEqual(
            self.call(
                "POST", f"/api/missions/{mission_id}/resolve",
                body={"finding": "maybe", "confirmed": True},
            ).status,
            400,
        )


class ApiMissionLongAdvanceTests(MissionCoordinatorPhase2Fixture, ApiFixture):
    def setUp(self) -> None:
        super().setUp()
        self.build_api()

    def test_mission_advance_long_returns_202_with_reserved_audit(self) -> None:
        mission_id = self.mission.mission_id
        preview = self.call("POST", f"/api/missions/{mission_id}/next", body={})
        self.assertEqual(preview.body["data"]["capability_id"], "team.implement")
        token = preview.body["data"]["advance_token"]
        response = self.call(
            "POST",
            f"/api/missions/{mission_id}/advance",
            body={"advance_token": token, "confirmed": True},
        )
        self.assertEqual(response.status, 202)
        data = response.body["data"]
        self.assertEqual(data["capability_id"], "team.implement")
        self.assertTrue(data["attempt_id"])
        self.assertEqual(data["operation_id"], data["attempt_id"])
        self.assertEqual(data["poll_url"], f"/api/missions/{mission_id}")
        # The durable record exists before the 202 returns.
        audit = self.audit_repository.get(mission_id)
        self.assertIsNotNone(audit)
        assert audit is not None
        self.assertEqual(audit.state, "reserved")
        self.assertEqual(audit.attempt_id, data["attempt_id"])
        # After the detached execution drains, the operation ran exactly
        # once and the mission is pollable in its new state.
        self.assertEqual(self.executor.drain(5.0), ())
        self.assertEqual(len(self.team_application.implement_calls), 1)
        completed = self.audit_repository.get(mission_id)
        assert completed is not None
        self.assertEqual(completed.state, "succeeded")
        polled = self.call("GET", f"/api/missions/{mission_id}")
        self.assertEqual(polled.body["data"]["status"], "awaiting_validation")
        self.assertEqual(
            polled.body["data"]["coordination_audit"]["state"], "succeeded"
        )
