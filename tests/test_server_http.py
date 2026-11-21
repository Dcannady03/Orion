"""Live-socket HTTP integration tests (server Phase B: aiohttp).

The LocalApiServer is started for real on an ephemeral loopback port
and exercised over HTTP with urllib: envelope shapes, error mapping,
request/response bounds, the 202 poll contract, and the access log.
The contract is identical to Phase A — only the transport moved.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from orion.core.paths import OrionPaths
from orion.server.config import ServerConfig
from orion.server.aiohttp_server import LocalApiServer
from orion.server.logging import close_server_logging, setup_server_logging
from tests.test_command_center_workflow import JOB_ID, TEAM_TASK_ID
from tests.test_mission_coordinator import MissionCoordinatorFixture
from tests.test_mission_coordinator_phase2 import (
    MissionCoordinatorPhase2Fixture,
)
from tests.test_server_api import ApiFixture


class HttpFixture(ApiFixture):
    """Starts a real LocalApiServer on an ephemeral loopback port."""

    server_kwargs: dict = {}

    def start_server(self) -> None:
        self._log_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._log_temp.cleanup)
        self._env = patch.dict(os.environ, {"ORION_SERVER_AUTH": "off"})
        self._env.start()
        self.addCleanup(self._env.stop)
        logger = setup_server_logging(
            OrionPaths(user_root=self._log_temp.name)
        )
        self.log_path = (
            Path(self._log_temp.name) / "logs" / "server.log"
        )
        kwargs: dict = {
            "host": "127.0.0.1",
            "port": 0,
            "auth_required": True,
            "shutdown_drain_seconds": 5,
            "max_concurrent_requests": 4,
            "max_request_bytes": 1_048_576,
            "max_response_bytes": 4_194_304,
            "request_timeout_seconds": 30,
        }
        kwargs.update(self.server_kwargs)
        config = ServerConfig(**kwargs)
        self.server = LocalApiServer(
            self.runtime, config, logger=logger
        )
        self.server.start()
        self.addCleanup(self._stop_server)
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"

    def _stop_server(self) -> None:
        if self.server.started:
            self.server.stop()
        close_server_logging()

    def http(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        raw_body: bytes | None = None,
    ) -> tuple[int, dict]:
        data = raw_body
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
            return exc.code, payload

    def poll_job_status(self, job_id: str, timeout: float = 5.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, payload = self.http("GET", f"/api/jobs/{job_id}")
            self.assertEqual(status, 200)
            current = payload["data"]["status"]
            if current != "planning":
                return current
            time.sleep(0.1)
        raise AssertionError(f"job {job_id} stayed in planning")


class HttpApiTests(MissionCoordinatorFixture, HttpFixture):
    def setUp(self) -> None:
        super().setUp()
        self.build_api()
        self.start_server()

    def test_healthz_over_http(self) -> None:
        status, payload = self.http("GET", "/api/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["data"]["status"], "ok")

    def test_auth_login_disabled_in_dev_mode(self) -> None:
        # Dev mode (ORION_SERVER_AUTH=off): the gate is skipped, and
        # the auth endpoints honestly report that auth is off.
        status, payload = self.http(
            "POST", "/api/auth/login", body={"password": "whatever"}
        )
        self.assertEqual(status, 503)
        self.assertEqual(payload["data"]["error_code"], "auth_disabled")

    def test_unknown_route_over_http(self) -> None:
        status, payload = self.http("GET", "/api/nope")
        self.assertEqual(status, 404)
        self.assertEqual(payload["status"], "failure")

    def test_malformed_json_body_is_400(self) -> None:
        status, _ = self.http(
            "POST", "/api/jobs", raw_body=b"{not json"
        )
        self.assertEqual(status, 400)

    def test_non_object_json_body_is_400(self) -> None:
        status, _ = self.http("POST", "/api/jobs", raw_body=b"[1, 2]")
        self.assertEqual(status, 400)

    def test_create_and_list_jobs_over_http(self) -> None:
        status, payload = self.http("POST", "/api/jobs", body={
            "title": "HTTP job",
            "goal": "Do the thing.",
            "department": "Engineering",
        })
        self.assertEqual(status, 201)
        job_id = payload["data"]["id"]
        status, payload = self.http("GET", "/api/jobs")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["data"]["jobs"]), 1)
        self.assertEqual(payload["data"]["jobs"][0]["id"], job_id)

    def test_launch_202_then_poll_over_http(self) -> None:
        self.create_cc_job()
        status, payload = self.http(
            "POST", f"/api/jobs/{JOB_ID}/launch/preview", body={}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["allowed"])
        status, payload = self.http("POST", f"/api/jobs/{JOB_ID}/launch", body={})
        self.assertEqual(status, 202)
        self.assertEqual(payload["data"]["team_task_id"], TEAM_TASK_ID)
        final = self.poll_job_status(JOB_ID)
        self.assertEqual(final, "awaiting_approval")
        status, payload = self.http("GET", f"/api/team/tasks/{TEAM_TASK_ID}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["team_task_id"], TEAM_TASK_ID)

    def test_mission_next_and_advance_over_http(self) -> None:
        mission_id = self.mission.mission_id
        status, payload = self.http(
            "POST", f"/api/missions/{mission_id}/next", body={}
        )
        self.assertEqual(status, 200)
        token = payload["data"]["advance_token"]
        status, payload = self.http(
            "POST",
            f"/api/missions/{mission_id}/advance",
            body={"advance_token": token, "confirmed": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["audit_state"], "succeeded")
        status, payload = self.http("GET", f"/api/missions/{mission_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["status"], "approved")

    def test_access_log_writes_structured_lines(self) -> None:
        self.http("GET", "/api/healthz")
        self.http("GET", "/api/nope")
        # The access line is written after the response is sent, so the
        # server thread may lag the client; poll briefly for the lines.
        deadline = time.monotonic() + 5.0
        access: list[dict] = []
        while time.monotonic() < deadline:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()
            access = []
            for line in lines:
                if '"type": "access"' not in line:
                    continue
                # The log formatter prefixes each line; the JSON starts
                # at the first brace.
                access.append(json.loads(line[line.index("{"):]))
            if len(access) >= 2:
                break
            time.sleep(0.05)
        self.assertTrue(access)
        paths = {(item["method"], item["path"]) for item in access}
        self.assertIn(("GET", "/api/healthz"), paths)
        self.assertIn(("GET", "/api/nope"), paths)
        statuses = {item["status"] for item in access}
        self.assertIn(200, statuses)
        self.assertIn(404, statuses)


class HttpMissionLongAdvanceTests(MissionCoordinatorPhase2Fixture, HttpFixture):
    def setUp(self) -> None:
        super().setUp()
        self.build_api()
        self.start_server()

    def test_mission_long_advance_202_then_poll_over_http(self) -> None:
        mission_id = self.mission.mission_id
        _, preview = self.http(
            "POST", f"/api/missions/{mission_id}/next", body={}
        )
        self.assertEqual(preview["data"]["capability_id"], "team.implement")
        status, payload = self.http(
            "POST",
            f"/api/missions/{mission_id}/advance",
            body={
                "advance_token": preview["data"]["advance_token"],
                "confirmed": True,
            },
        )
        self.assertEqual(status, 202)
        attempt_id = payload["data"]["attempt_id"]
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            _, polled = self.http("GET", f"/api/missions/{mission_id}")
            audit = polled["data"]["coordination_audit"]
            if audit is not None and audit["state"] == "succeeded":
                break
            time.sleep(0.1)
        else:
            raise AssertionError("mission advance did not complete")
        self.assertEqual(audit["attempt_id"], attempt_id)
        self.assertEqual(polled["data"]["status"], "awaiting_validation")


class HttpBoundsTests(MissionCoordinatorFixture, HttpFixture):
    server_kwargs = {"max_request_bytes": 100, "max_response_bytes": 200}

    def setUp(self) -> None:
        super().setUp()
        self.build_api()
        self.start_server()

    def test_oversized_request_body_is_413(self) -> None:
        status, payload = self.http("POST", "/api/jobs", body={
            "title": "x" * 500,
            "goal": "y",
        })
        self.assertEqual(status, 413)
        self.assertEqual(payload["data"]["error_code"], "payload_too_large")

    def test_oversized_response_is_500_not_truncated(self) -> None:
        status, payload = self.http("GET", "/api/snapshot")
        self.assertEqual(status, 500)
        self.assertIn("size limit", payload["message"])


class ServerConfigTests(unittest.TestCase):
    def test_non_loopback_host_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            ServerConfig(host="0.0.0.0")
        with self.assertRaises(ValueError):
            ServerConfig(host="192.168.1.1")

    def test_loopback_hosts_accepted(self) -> None:
        for host in ("127.0.0.1", "::1", "localhost"):
            self.assertEqual(ServerConfig(host=host).host, host)

    def test_port_bounds(self) -> None:
        with self.assertRaises(ValueError):
            ServerConfig(port=70000)
        with self.assertRaises(ValueError):
            ServerConfig(port=-1)
        self.assertEqual(ServerConfig(port=0).port, 0)

    def test_auth_disabled_without_opt_out_refuses_start(self) -> None:
        config = ServerConfig(auth_required=False)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORION_SERVER_AUTH", None)
            with self.assertRaises(RuntimeError):
                config.require_startable()

    def test_auth_required_without_opt_out_starts(self) -> None:
        # Phase B implements auth: auth required + no opt-out is the
        # normal deployed configuration (authenticated, or setup mode
        # when no password exists yet).
        config = ServerConfig(auth_required=True)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORION_SERVER_AUTH", None)
            config.require_startable()

    def test_dev_opt_out_allows_either_auth_setting(self) -> None:
        with patch.dict(os.environ, {"ORION_SERVER_AUTH": "off"}):
            ServerConfig(auth_required=True).require_startable()
            ServerConfig(auth_required=False).require_startable()

    def test_secure_cookies_default_true_and_must_be_bool(self) -> None:
        self.assertTrue(ServerConfig().secure_cookies)
        self.assertFalse(ServerConfig(secure_cookies=False).secure_cookies)
        with self.assertRaises(ValueError):
            ServerConfig(secure_cookies="yes")  # type: ignore[arg-type]

    def test_tailscale_host_default_empty_and_normalized(self) -> None:
        self.assertEqual(ServerConfig().tailscale_host, "")
        self.assertEqual(
            ServerConfig(
                tailscale_host="  DANIELS-SERVER.Test.TS.NET "
            ).tailscale_host,
            "daniels-server.test.ts.net",
        )
        with self.assertRaises(ValueError):
            ServerConfig(tailscale_host=123)  # type: ignore[arg-type]

    def test_bind_conflict_fails_fast_with_clear_error(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import Mock

        runtime = SimpleNamespace(
            version="0.8.0",
            missions=Mock(list=Mock(return_value=())),
            mission_coordination_repository=Mock(),
            command_center=Mock(jobs=Mock(return_value=())),
            team=Mock(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            logger = setup_server_logging(OrionPaths(user_root=tmp))
            try:
                with patch.dict(os.environ, {"ORION_SERVER_AUTH": "off"}):
                    first = LocalApiServer(
                        runtime,
                        ServerConfig(port=0),
                        logger=logger,
                    )
                    first.start()
                    try:
                        port = first.server_address[1]
                        second = LocalApiServer(
                            runtime,
                            ServerConfig(port=port),
                            logger=logger,
                        )
                        with self.assertRaises(RuntimeError) as caught:
                            second.start(bind_attempts=2, bind_backoff_seconds=0.05)
                        self.assertIn("Could not bind", str(caught.exception))
                        self.assertIn(str(port), str(caught.exception))
                    finally:
                        first.stop()
            finally:
                close_server_logging()


if __name__ == "__main__":
    unittest.main()
