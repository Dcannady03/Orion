"""Tests for Orion Docker integration (discovery, control, health, logs)."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from orion.application.commands.observability_cli import (
    ObservabilityCliAdapter,
    dispatch_observability,
)
from orion.application.observability import (
    AmbiguousContainerError,
    ContainerDetail,
    ContainerDiagnosis,
    ContainerList,
    ContainerSummary,
    DiagnoseFinding,
    DockerService,
    DockerUnavailableError,
    ObservabilityApplicationHandler,
    PortProbeResult,
    PublishedPort,
    UnknownContainerError,
)
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter
from orion.ui.console import BASE_COMMANDS

_WEB_ID = "a1b2c3d4e5f6" + "0" * 52
_DB_ID = "b2c3d4e5f6a7" + "1" * 52
_WORKER_ID = "c3d4e5f6a7b8" + "2" * 52
_AMBIGUOUS_A_ID = "abcd1111" + "3" * 56
_AMBIGUOUS_B_ID = "abcd2222" + "4" * 56

_DAEMON_STDERR = (
    "error during connect: Get "
    "\"http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.51/version\": "
    "open //./pipe/dockerDesktopLinuxEngine: "
    "The system cannot find the file specified."
)


def _ps_row(container_id, name, image, status, ports):
    return {
        "ID": container_id,
        "Image": image,
        "Command": "\"/docker-entrypoint.sh\"",
        "CreatedAt": "2026-11-19 10:00:00 +0000 UTC",
        "RunningFor": "2 days ago",
        "Ports": ports,
        "Status": status,
        "Size": "0B",
        "Names": "/" + name,
        "Labels": "",
        "Mounts": "",
        "Networks": "bridge",
    }


_PS_ROWS = [
    _ps_row(_WEB_ID, "web", "nginx:latest", "Up 2 days (healthy)", "0.0.0.0:8080->80/tcp"),
    _ps_row(_DB_ID, "db", "postgres:16", "Up 2 days (unhealthy)", "5432/tcp"),
    _ps_row(_WORKER_ID, "worker", "myapp:latest", "Exited (0) 3 hours ago", ""),
]


def _inspect_data(container_id, name, image, status, running, health):
    return {
        "Id": container_id,
        "Created": "2026-11-19T10:00:00Z",
        "Name": "/" + name,
        "Config": {"Image": image},
        "State": {
            "Status": status,
            "Running": running,
            "Paused": False,
            "Restarting": False,
            "OOMKilled": False,
            "Dead": False,
            "Pid": 1234 if running else 0,
            "ExitCode": 0,
            "Error": "",
            "StartedAt": "2026-11-19T10:00:01Z" if running else "2026-11-19T10:00:01Z",
            "FinishedAt": "0001-01-01T00:00:00Z" if running else "2026-11-20T09:00:00Z",
            "Health": {"Status": health, "FailingStreak": 0, "Log": []},
        },
        "NetworkSettings": {
            "Ports": {
                "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]
            }
            if name == "web"
            else {"5432/tcp": None}
            if name == "db"
            else {},
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/srv/web",
                "Destination": "/usr/share/nginx/html",
                "Mode": "rw",
                "RW": True,
            }
        ]
        if name == "web"
        else [],
    }


_INSPECT = {
    _WEB_ID: _inspect_data(_WEB_ID, "web", "nginx:latest", "running", True, "healthy"),
    _DB_ID: _inspect_data(_DB_ID, "db", "postgres:16", "running", True, "unhealthy"),
    _WORKER_ID: _inspect_data(_WORKER_ID, "worker", "myapp:latest", "exited", False, "none"),
}


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeDocker:
    """In-memory stand-in for the `docker` CLI (no daemon needed)."""

    def __init__(
        self,
        *,
        daemon_down=False,
        cli_missing=False,
        timeout=False,
        ps_rows=None,
        inspect_data=None,
        log_lines=None,
        fail_actions=None,
        sticky_state=False,
    ):
        self.daemon_down = daemon_down
        self.cli_missing = cli_missing
        self.timeout = timeout
        self.calls: list[list[str]] = []
        self.ps_rows = copy.deepcopy(_PS_ROWS if ps_rows is None else ps_rows)
        self.inspect_data = copy.deepcopy(
            _INSPECT if inspect_data is None else inspect_data
        )
        self.log_lines = list(log_lines if log_lines is not None else [])
        self.fail_actions = set(fail_actions or ())
        self.sticky_state = sticky_state
        self.states = {
            row["ID"]: ("running" if row["Status"].startswith("Up") else "exited")
            for row in self.ps_rows
        }

    def __call__(self, args, timeout, *, merge_stderr=False):
        self.calls.append(list(args))
        if self.cli_missing:
            raise FileNotFoundError("docker")
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)
        if self.daemon_down:
            return _Completed(1, "", _DAEMON_STDERR)
        sub = args[1] if len(args) > 1 else ""
        if sub == "version":
            return _Completed(0, "28.5.1\n", "")
        if sub == "ps":
            lines = "\n".join(json.dumps(row) for row in self.ps_rows)
            return _Completed(0, lines + "\n" if lines else "", "")
        if sub == "inspect":
            target = args[-1]
            data = self.inspect_data.get(target)
            if data is None:
                return _Completed(1, "", f"Error: No such container: {target}\n")
            return _Completed(0, json.dumps(data), "")
        if sub in {"start", "stop", "restart"}:
            target = args[-1]
            if target not in self.states:
                return _Completed(1, "", f"Error: No such container: {target}\n")
            if sub in self.fail_actions:
                return _Completed(1, "", f"Error: failed to {sub} container {target}\n")
            if not self.sticky_state:
                if sub == "start":
                    self.states[target] = "running"
                elif sub == "stop":
                    self.states[target] = "exited"
                elif sub == "restart":
                    self.states[target] = "running"
                self._sync_inspect(target)
            name = next(
                (row["Names"].lstrip("/") for row in self.ps_rows if row["ID"] == target),
                target,
            )
            return _Completed(0, name + "\n", "")
        if sub == "logs":
            tail = len(self.log_lines)
            if "--tail" in args:
                tail = int(args[args.index("--tail") + 1])
            lines = self.log_lines[-tail:] if tail else []
            return _Completed(0, "\n".join(lines) + "\n" if lines else "", "")
        return _Completed(1, "", f"unexpected docker command: {args}")

    def _sync_inspect(self, container_id):
        """Keep the inspect view consistent with the simulated state."""
        data = self.inspect_data.get(container_id)
        if data is None:
            return
        state = self.states[container_id]
        data["State"]["Status"] = state
        data["State"]["Running"] = state == "running"
        for row in self.ps_rows:
            if row["ID"] != container_id:
                continue
            if state == "running":
                health = data["State"]["Health"]["Status"]
                row["Status"] = f"Up 1 minute ({health})"
            else:
                row["Status"] = "Exited (0) 1 minute ago"


def _service(fake=None, port_probe=None):
    return DockerService(runner=fake or _FakeDocker(), port_probe=port_probe)


def _probe_ok(host_ip, host_port, container_port):
    return PortProbeResult(
        host_ip, host_port, container_port, True, 200,
        f"HTTP 200 from 127.0.0.1:{host_port}",
    )


def _probe_dead(host_ip, host_port, container_port):
    return PortProbeResult(
        host_ip, host_port, container_port, False, None,
        f"TCP connect to 127.0.0.1:{host_port} failed: refused",
    )


def _probe_http_error(host_ip, host_port, container_port):
    return PortProbeResult(
        host_ip, host_port, container_port, True, 500,
        f"HTTP 500 from 127.0.0.1:{host_port}",
    )


def _probe_not_http(host_ip, host_port, container_port):
    return PortProbeResult(
        host_ip, host_port, container_port, True, None,
        "Port answers TCP but not HTTP",
    )


class _Clock:
    def __call__(self):
        from datetime import datetime, timezone

        return datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _handler(paths, fake=None, port_probe=None):
    return ObservabilityApplicationHandler(
        paths=paths,
        docker_service=_service(fake, port_probe),
        clock=_Clock(),
    )


class DockerServiceTests(TestCase):
    def test_require_available_returns_server_version(self):
        self.assertEqual(_service().require_available(), "28.5.1")

    def test_cli_missing_is_unavailable(self):
        service = _service(_FakeDocker(cli_missing=True))
        with self.assertRaises(DockerUnavailableError) as ctx:
            service.require_available()
        self.assertEqual(ctx.exception.reason, "cli_missing")

    def test_daemon_down_is_unavailable(self):
        service = _service(_FakeDocker(daemon_down=True))
        with self.assertRaises(DockerUnavailableError) as ctx:
            service.require_available()
        self.assertEqual(ctx.exception.reason, "daemon_unreachable")

    def test_timeout_is_unavailable(self):
        service = _service(_FakeDocker(timeout=True))
        with self.assertRaises(DockerUnavailableError) as ctx:
            service.require_available()
        self.assertEqual(ctx.exception.reason, "timeout")

    def test_list_parses_containers(self):
        listing = _service().list_containers()
        self.assertIsInstance(listing, ContainerList)
        self.assertEqual(len(listing.containers), 3)
        self.assertEqual(listing.skipped, 0)
        web = listing.containers[0]
        self.assertIsInstance(web, ContainerSummary)
        self.assertEqual(web.name, "web")
        self.assertEqual(web.image, "nginx:latest")
        self.assertEqual(web.state, "running")
        self.assertEqual(web.health, "healthy")
        self.assertEqual(web.ports, ("0.0.0.0:8080->80/tcp",))
        self.assertEqual(listing.containers[1].health, "unhealthy")
        self.assertEqual(listing.containers[2].state, "exited")
        self.assertEqual(listing.containers[2].health, "none")

    def test_list_skips_malformed_lines(self):
        fake = _FakeDocker()
        original = fake.__call__

        def _with_junk(args, timeout, *, merge_stderr=False):
            result = original(args, timeout, merge_stderr=merge_stderr)
            if len(args) > 1 and args[1] == "ps":
                result.stdout = "{not json\n" + result.stdout
            return result

        service = DockerService(runner=_with_junk)
        listing = service.list_containers()
        self.assertEqual(len(listing.containers), 3)
        self.assertEqual(listing.skipped, 1)

    def test_list_daemon_down_fails_closed(self):
        with self.assertRaises(DockerUnavailableError):
            _service(_FakeDocker(daemon_down=True)).list_containers()

    def test_list_serializes_to_json_safe_dict(self):
        listing = _service().list_containers()
        data = listing.to_dict()
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_resolve_exact_name(self):
        self.assertEqual(_service().resolve("web").name, "web")

    def test_resolve_name_with_leading_slash(self):
        self.assertEqual(_service().resolve("/db").name, "db")

    def test_resolve_exact_id(self):
        self.assertEqual(_service().resolve(_WEB_ID).name, "web")

    def test_resolve_unique_id_prefix(self):
        self.assertEqual(_service().resolve("a1b2").name, "web")
        self.assertEqual(_service().resolve("a1b2c3d4e5f6").name, "web")

    def test_resolve_short_prefix_is_unknown(self):
        with self.assertRaises(UnknownContainerError):
            _service().resolve("a1b")

    def test_resolve_ambiguous_prefix_is_refused(self):
        rows = [
            _ps_row(_AMBIGUOUS_A_ID, "alpha", "img:1", "Up 1 hour", ""),
            _ps_row(_AMBIGUOUS_B_ID, "beta", "img:1", "Up 1 hour", ""),
        ]
        service = _service(_FakeDocker(ps_rows=rows))
        with self.assertRaises(AmbiguousContainerError) as ctx:
            service.resolve("abcd")
        self.assertIn("alpha", str(ctx.exception))
        self.assertIn("beta", str(ctx.exception))

    def test_resolve_unknown_is_refused(self):
        with self.assertRaises(UnknownContainerError):
            _service().resolve("nope")

    def test_resolve_empty_is_refused(self):
        with self.assertRaises(UnknownContainerError):
            _service().resolve("  ")

    def test_inspect_parses_detail(self):
        detail = _service().inspect_container("web")
        self.assertIsInstance(detail, ContainerDetail)
        self.assertEqual(detail.name, "web")
        self.assertEqual(detail.state, "running")
        self.assertTrue(detail.running)
        self.assertEqual(detail.health, "healthy")
        self.assertEqual(detail.image, "nginx:latest")
        self.assertEqual(detail.ports, ("0.0.0.0:8080->80/tcp",))
        self.assertEqual(
            detail.mounts, ("/srv/web -> /usr/share/nginx/html (bind)",)
        )

    def test_inspect_unpublished_port_is_marked(self):
        detail = _service().inspect_container("db")
        self.assertEqual(detail.ports, ("5432/tcp (not published)",))

    def test_inspect_unknown_is_refused(self):
        with self.assertRaises(UnknownContainerError):
            _service().inspect_container("nope")

    def test_inspect_daemon_down_fails_closed(self):
        with self.assertRaises(DockerUnavailableError):
            _service(_FakeDocker(daemon_down=True)).inspect_container("web")

    def test_inspect_serializes_to_json_safe_dict(self):
        detail = _service().inspect_container("web")
        data = detail.to_dict()
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_control_stop_verifies(self):
        outcome = _service().control("stop", "web")
        self.assertTrue(outcome.applied)
        self.assertTrue(outcome.verified)
        self.assertEqual(outcome.state_before, "running")
        self.assertEqual(outcome.state_after, "exited")
        self.assertIn("web stopped", outcome.detail)

    def test_control_start_verifies(self):
        outcome = _service().control("start", "worker")
        self.assertTrue(outcome.applied)
        self.assertTrue(outcome.verified)
        self.assertEqual(outcome.state_before, "exited")
        self.assertEqual(outcome.state_after, "running")

    def test_control_restart_verifies(self):
        outcome = _service().control("restart", "web")
        self.assertTrue(outcome.applied)
        self.assertTrue(outcome.verified)
        self.assertEqual(outcome.state_after, "running")

    def test_control_start_already_running_is_verified(self):
        outcome = _service().control("start", "web")
        self.assertTrue(outcome.applied)
        self.assertTrue(outcome.verified)
        self.assertIn("already running", outcome.detail)

    def test_control_stop_already_stopped_is_verified(self):
        outcome = _service().control("stop", "worker")
        self.assertTrue(outcome.applied)
        self.assertTrue(outcome.verified)
        self.assertIn("already stopped", outcome.detail)

    def test_control_unknown_is_refused(self):
        with self.assertRaises(UnknownContainerError):
            _service().control("stop", "nope")

    def test_control_daemon_down_fails_closed(self):
        with self.assertRaises(DockerUnavailableError):
            _service(_FakeDocker(daemon_down=True)).control("stop", "web")

    def test_control_invalid_action_is_rejected(self):
        with self.assertRaises(ValueError):
            _service().control("pause", "web")

    def test_control_command_failure_is_not_applied(self):
        service = _service(_FakeDocker(fail_actions={"stop"}))
        outcome = service.control("stop", "web")
        self.assertFalse(outcome.applied)
        self.assertFalse(outcome.verified)
        self.assertEqual(outcome.state_after, "running")

    def test_control_unverified_when_state_does_not_change(self):
        service = _service(_FakeDocker(sticky_state=True))
        outcome = service.control("stop", "web")
        self.assertTrue(outcome.applied)
        self.assertFalse(outcome.verified)
        self.assertIn("expected exited", outcome.detail)

    def test_control_serializes_to_json_safe_dict(self):
        outcome = _service().control("stop", "web")
        data = outcome.to_dict()
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_health_summary_counts(self):
        summary = _service().health_summary()
        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.running, 2)
        self.assertEqual(summary.exited, 1)
        self.assertEqual(summary.healthy, 1)
        self.assertEqual(summary.unhealthy, 1)
        self.assertEqual(summary.starting, 0)
        self.assertEqual(summary.no_healthcheck, 1)
        self.assertEqual(summary.overall, "error")

    def test_health_summary_ok_when_all_healthy(self):
        fake = _FakeDocker(ps_rows=[_PS_ROWS[0]])
        summary = _service(fake).health_summary()
        self.assertEqual(summary.overall, "ok")
        self.assertEqual(summary.healthy, 1)

    def test_health_summary_warning_when_starting(self):
        rows = [
            _ps_row(_WEB_ID, "web", "nginx:latest", "Up 5 seconds (health: starting)", ""),
        ]
        summary = _service(_FakeDocker(ps_rows=rows)).health_summary()
        self.assertEqual(summary.overall, "warning")
        self.assertEqual(summary.starting, 1)

    def test_health_summary_empty_is_ok(self):
        summary = _service(_FakeDocker(ps_rows=[])).health_summary()
        self.assertEqual(summary.total, 0)
        self.assertEqual(summary.overall, "ok")

    def test_health_summary_daemon_down_fails_closed(self):
        with self.assertRaises(DockerUnavailableError):
            _service(_FakeDocker(daemon_down=True)).health_summary()

    def test_health_summary_serializes_to_json_safe_dict(self):
        data = _service().health_summary().to_dict()
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_container_logs_returns_lines(self):
        fake = _FakeDocker(log_lines=["booted", "listening on :80"])
        result = _service(fake).container_logs("web")
        self.assertEqual(result.name, "web")
        self.assertEqual(result.lines, ("booted", "listening on :80"))
        self.assertFalse(result.truncated)
        self.assertEqual(result.tail, 50)

    def test_container_logs_respects_tail(self):
        lines = [f"line-{i}" for i in range(10)]
        result = _service(_FakeDocker(log_lines=lines)).container_logs("web", tail=3)
        self.assertEqual(result.lines, ("line-7", "line-8", "line-9"))
        self.assertEqual(result.tail, 3)

    def test_container_logs_clamps_tail_to_max(self):
        lines = [f"line-{i}" for i in range(10)]
        result = _service(_FakeDocker(log_lines=lines)).container_logs("web", tail=10000)
        self.assertEqual(result.tail, 500)

    def test_container_logs_clamps_tail_to_minimum(self):
        lines = [f"line-{i}" for i in range(10)]
        result = _service(_FakeDocker(log_lines=lines)).container_logs("web", tail=0)
        self.assertEqual(result.tail, 1)
        self.assertEqual(result.lines, ("line-9",))

    def test_container_logs_non_integer_tail_is_rejected(self):
        with self.assertRaises(ValueError):
            _service().container_logs("web", tail="abc")

    def test_container_logs_caps_total_chars_keeping_newest(self):
        lines = [f"line-{i:03d}-" + "x" * 991 for i in range(30)]
        result = _service(_FakeDocker(log_lines=lines)).container_logs("web")
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.lines), 19)
        self.assertTrue(result.lines[0].startswith("line-011-"))
        self.assertTrue(result.lines[-1].startswith("line-029-"))
        total = sum(len(line) + 1 for line in result.lines)
        self.assertLessEqual(total, 20_000)

    def test_container_logs_unknown_is_refused(self):
        with self.assertRaises(UnknownContainerError):
            _service().container_logs("nope")

    def test_container_logs_daemon_down_fails_closed(self):
        with self.assertRaises(DockerUnavailableError):
            _service(_FakeDocker(daemon_down=True)).container_logs("web")

    def test_container_logs_serializes_to_json_safe_dict(self):
        fake = _FakeDocker(log_lines=["a", "b"])
        data = _service(fake).container_logs("web").to_dict()
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_default_runner_merges_stderr_without_capture_conflict(self):
        # Regression test: _default_runner used to pass capture_output=True
        # together with stderr=STDOUT, which subprocess rejects with
        # "stdout and stderr arguments may not be used with capture_output".
        # Exercise the real runner (no Docker needed) with a command that
        # writes to both streams.
        from orion.application.observability.docker import _default_runner

        result = _default_runner(
            [
                sys.executable,
                "-c",
                "import sys; print('out'); print('err', file=sys.stderr)",
            ],
            timeout=10,
            merge_stderr=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("out", result.stdout)
        self.assertIn("err", result.stdout)

    def test_inspect_reports_published_ports(self):
        detail = _service().inspect_container("web")
        self.assertEqual(
            detail.published_ports,
            (PublishedPort("0.0.0.0", 8080, "80/tcp"),),
        )

    def test_inspect_exposed_but_unpublished_is_not_published(self):
        detail = _service().inspect_container("db")
        self.assertEqual(detail.published_ports, ())
        self.assertIn("5432/tcp (not published)", detail.ports)

    def test_diagnose_stopped_container_names_exit_code(self):
        diagnosis = _service().diagnose_container("worker")
        self.assertEqual(diagnosis.verdict, "not-running")
        self.assertFalse(diagnosis.running)
        self.assertEqual(diagnosis.exit_code, 0)
        self.assertIn("exit code 0", diagnosis.cause)
        self.assertIn("docker start worker", diagnosis.suggestion)
        self.assertEqual(diagnosis.port_probes, ())

    def test_diagnose_stopped_container_scans_logs_for_errors(self):
        fake = _FakeDocker(log_lines=["boot ok", "panic: runtime error", "done"])
        diagnosis = _service(fake).diagnose_container("worker")
        self.assertEqual(diagnosis.verdict, "not-running")
        self.assertEqual(diagnosis.error_count, 1)
        self.assertEqual(diagnosis.error_lines, ("panic: runtime error",))

    def test_diagnose_running_port_ok_is_ok(self):
        diagnosis = _service(port_probe=_probe_ok).diagnose_container("web")
        self.assertEqual(diagnosis.verdict, "ok")
        self.assertTrue(diagnosis.running)
        self.assertEqual(len(diagnosis.port_probes), 1)
        self.assertTrue(diagnosis.port_probes[0].tcp_ok)
        self.assertEqual(diagnosis.port_probes[0].http_status, 200)

    def test_diagnose_running_port_dead_is_port_unreachable(self):
        diagnosis = _service(port_probe=_probe_dead).diagnose_container("web")
        self.assertEqual(diagnosis.verdict, "port-unreachable")
        self.assertIn("docker restart web", diagnosis.suggestion)

    def test_diagnose_running_http_error_is_http_error(self):
        diagnosis = _service(port_probe=_probe_http_error).diagnose_container("web")
        self.assertEqual(diagnosis.verdict, "http-error")
        self.assertIn("HTTP 500", diagnosis.cause)

    def test_diagnose_running_non_http_port_is_ok(self):
        diagnosis = _service(port_probe=_probe_not_http).diagnose_container("web")
        self.assertEqual(diagnosis.verdict, "ok")

    def test_diagnose_running_no_published_ports(self):
        diagnosis = _service(port_probe=_probe_ok).diagnose_container("db")
        self.assertEqual(diagnosis.verdict, "no-published-ports")
        self.assertEqual(diagnosis.port_probes, ())

    def test_diagnose_unknown_is_refused(self):
        with self.assertRaises(UnknownContainerError):
            _service().diagnose_container("nope")

    def test_diagnose_daemon_down_fails_closed(self):
        with self.assertRaises(DockerUnavailableError):
            _service(_FakeDocker(daemon_down=True)).diagnose_container("web")

    def test_diagnose_probe_crash_is_a_finding_not_a_crash(self):
        def _exploding_probe(host_ip, host_port, container_port):
            raise RuntimeError("boom")

        diagnosis = _service(port_probe=_exploding_probe).diagnose_container("web")
        self.assertEqual(diagnosis.verdict, "port-unreachable")
        self.assertFalse(diagnosis.port_probes[0].tcp_ok)

    def test_diagnose_log_scan_caps_and_truncates(self):
        lines = [
            f"line {i} ERROR something went wrong " + "x" * 300
            for i in range(8)
        ]
        lines.append("all good here")
        fake = _FakeDocker(log_lines=lines)
        diagnosis = _service(fake, _probe_ok).diagnose_container("web")
        self.assertEqual(diagnosis.error_count, 8)
        self.assertEqual(len(diagnosis.error_lines), 5)
        # Newest first: line 7 is the most recent error.
        self.assertTrue(diagnosis.error_lines[0].startswith("line 7 ERROR"))
        for line in diagnosis.error_lines:
            self.assertLessEqual(len(line), 200)

    def test_diagnose_clean_logs_report_no_errors(self):
        fake = _FakeDocker(log_lines=["started", "listening on :80"])
        diagnosis = _service(fake, _probe_ok).diagnose_container("web")
        self.assertEqual(diagnosis.error_count, 0)
        self.assertEqual(diagnosis.error_lines, ())

    def test_diagnose_serializes_to_json_safe_dict(self):
        fake = _FakeDocker(log_lines=["ERROR boom"])
        data = _service(fake, _probe_ok).diagnose_container("web").to_dict()
        self.assertEqual(json.loads(json.dumps(data)), data)
        self.assertEqual(data["verdict"], "ok")
        self.assertEqual(data["error_count"], 1)

    def test_real_port_probe_refused_port_is_tcp_failure(self):
        from orion.application.observability.docker import _probe_port

        result = _probe_port("0.0.0.0", 1, "1/tcp")
        self.assertFalse(result.tcp_ok)
        self.assertIsNone(result.http_status)

    def test_real_port_probe_http_server_returns_status(self):
        from orion.application.observability.docker import _probe_port

        server = _http_server(200)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            result = _probe_port("0.0.0.0", port, f"{port}/tcp")
        finally:
            server.shutdown()
            server.server_close()
        self.assertTrue(result.tcp_ok)
        self.assertEqual(result.http_status, 200)

    def test_real_port_probe_http_error_reports_status(self):
        from orion.application.observability.docker import _probe_port

        server = _http_server(500)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            result = _probe_port("0.0.0.0", port, f"{port}/tcp")
        finally:
            server.shutdown()
            server.server_close()
        self.assertTrue(result.tcp_ok)
        self.assertEqual(result.http_status, 500)


def _http_server(status):
    """A tiny local HTTP server answering one status (for probe tests)."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", 0), _Handler)


class DockerHandlerTests(TestCase):
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
        self.fake = _FakeDocker()
        self.handler = _handler(self.paths, self.fake)

    def test_docker_list_returns_success_with_data(self):
        result = self.handler.docker_list()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["total"], 3)
        self.assertEqual(
            [item["name"] for item in result.data["containers"]],
            ["web", "db", "worker"],
        )
        self.assertIn("Docker Containers (3)", result.message)
        self.assertIn("web", result.message)

    def test_docker_list_empty_is_success(self):
        handler = _handler(self.paths, _FakeDocker(ps_rows=[]))
        result = handler.docker_list()
        self.assertTrue(result.ok)
        self.assertIn("No containers found", result.message)

    def test_docker_list_daemon_down_is_failure(self):
        handler = _handler(self.paths, _FakeDocker(daemon_down=True))
        result = handler.docker_list()
        self.assertFalse(result.ok)
        self.assertIn("Docker is not available", result.message)
        self.assertFalse(result.data["docker_available"])

    def test_docker_list_cli_missing_is_failure(self):
        handler = _handler(self.paths, _FakeDocker(cli_missing=True))
        result = handler.docker_list()
        self.assertFalse(result.ok)
        self.assertIn("not found", result.message)

    def test_docker_inspect_returns_detail(self):
        result = self.handler.docker_inspect("web")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["name"], "web")
        self.assertEqual(result.data["health"], "healthy")
        self.assertIn("Container: web", result.message)

    def test_docker_inspect_unknown_is_failure(self):
        result = self.handler.docker_inspect("nope")
        self.assertFalse(result.ok)
        self.assertIn("No container matches", result.message)
        self.assertIn("docker list", result.next_actions)

    def test_docker_inspect_daemon_down_is_failure(self):
        handler = _handler(self.paths, _FakeDocker(daemon_down=True))
        result = handler.docker_inspect("web")
        self.assertFalse(result.ok)
        self.assertIn("Docker is not available", result.message)

    def test_docker_control_without_confirm_is_refused(self):
        result = self.handler.docker_control("stop", "web")
        self.assertFalse(result.ok)
        self.assertIn("needs interactive approval", result.message)
        self.assertEqual(self.fake.calls, [])

    def test_docker_control_with_confirm_stops_and_records(self):
        result = self.handler.docker_control("stop", "web", confirm=True)
        self.assertTrue(result.ok)
        self.assertTrue(result.data["applied"])
        self.assertTrue(result.data["verified"])
        history = self.handler.recovery_list(kind="docker")
        self.assertTrue(history.ok)
        self.assertEqual(history.data["total"], 1)
        record = history.data["records"][0]
        self.assertEqual(record["kind"], "docker")
        self.assertEqual(record["target"], "web")
        self.assertEqual(record["status"], "success")

    def test_docker_control_unknown_with_confirm_is_refused(self):
        result = self.handler.docker_control("stop", "nope", confirm=True)
        self.assertFalse(result.ok)
        self.assertIn("No container matches", result.message)
        history = self.handler.recovery_list(kind="docker")
        self.assertEqual(history.data["total"], 0)

    def test_docker_control_daemon_down_with_confirm_is_failure(self):
        handler = _handler(self.paths, _FakeDocker(daemon_down=True))
        result = handler.docker_control("stop", "web", confirm=True)
        self.assertFalse(result.ok)
        self.assertIn("Docker is not available", result.message)

    def test_docker_control_unverified_is_recorded_as_failed(self):
        handler = _handler(self.paths, _FakeDocker(sticky_state=True))
        result = handler.docker_control("stop", "web", confirm=True)
        self.assertFalse(result.ok)
        self.assertTrue(result.data["applied"])
        self.assertFalse(result.data["verified"])
        history = handler.recovery_list(kind="docker")
        self.assertEqual(history.data["total"], 1)
        self.assertEqual(history.data["records"][0]["status"], "failed")

    def test_docker_control_failure_is_not_recorded(self):
        handler = _handler(self.paths, _FakeDocker(fail_actions={"stop"}))
        result = handler.docker_control("stop", "web", confirm=True)
        self.assertFalse(result.ok)
        self.assertFalse(result.data["applied"])
        history = handler.recovery_list(kind="docker")
        self.assertEqual(history.data["total"], 0)

    def test_docker_health_reports_unhealthy(self):
        result = self.handler.docker_health()
        self.assertFalse(result.ok)
        self.assertEqual(result.data["overall"], "error")
        self.assertEqual(result.data["unhealthy"], 1)
        self.assertIn("Docker Health: ERROR", result.message)
        self.assertIn("Unhealthy containers:", result.message)
        self.assertIn("db", result.message)

    def test_docker_health_ok_when_healthy(self):
        handler = _handler(self.paths, _FakeDocker(ps_rows=[_PS_ROWS[0]]))
        result = handler.docker_health()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["overall"], "ok")

    def test_docker_health_daemon_down_is_failure(self):
        handler = _handler(self.paths, _FakeDocker(daemon_down=True))
        result = handler.docker_health()
        self.assertFalse(result.ok)
        self.assertIn("Docker is not available", result.message)

    def test_docker_logs_returns_lines(self):
        handler = _handler(self.paths, _FakeDocker(log_lines=["booted", "ready"]))
        result = handler.docker_logs("web")
        self.assertTrue(result.ok)
        self.assertIn("booted", result.message)
        self.assertIn("ready", result.message)
        self.assertEqual(result.data["line_count"], 2)

    def test_docker_logs_empty_is_success(self):
        result = self.handler.docker_logs("web")
        self.assertTrue(result.ok)
        self.assertIn("(no log output)", result.message)

    def test_docker_logs_unknown_is_failure(self):
        result = self.handler.docker_logs("nope")
        self.assertFalse(result.ok)
        self.assertIn("No container matches", result.message)

    def test_docker_logs_daemon_down_is_failure(self):
        handler = _handler(self.paths, _FakeDocker(daemon_down=True))
        result = handler.docker_logs("web")
        self.assertFalse(result.ok)
        self.assertIn("Docker is not available", result.message)

    def test_docker_logs_truncated_note(self):
        lines = [f"line-{i:03d}-" + "x" * 991 for i in range(30)]
        handler = _handler(self.paths, _FakeDocker(log_lines=lines))
        result = handler.docker_logs("web")
        self.assertTrue(result.ok)
        self.assertTrue(result.data["truncated"])
        self.assertIn("output capped", result.message)

    def test_docker_diagnose_ok_renders_verdict(self):
        handler = _handler(self.paths, self.fake, _probe_ok)
        result = handler.docker_diagnose("web")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["verdict"], "ok")
        self.assertIn("Docker Diagnosis: web", result.message)
        self.assertIn("Verdict   : ok", result.message)

    def test_docker_diagnose_stopped_suggests_start(self):
        result = self.handler.docker_diagnose("worker")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["verdict"], "not-running")
        self.assertIn("docker start worker", result.next_actions)
        self.assertIn("Likely cause", result.message)

    def test_docker_diagnose_unknown_is_failure(self):
        result = self.handler.docker_diagnose("nope")
        self.assertFalse(result.ok)
        self.assertIn("No container matches", result.message)

    def test_docker_diagnose_daemon_down_is_failure(self):
        handler = _handler(self.paths, _FakeDocker(daemon_down=True))
        result = handler.docker_diagnose("web")
        self.assertFalse(result.ok)
        self.assertIn("Docker is not available", result.message)


class DockerCliTests(TestCase):
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
        self.fake = _FakeDocker()
        self.handler = _handler(self.paths, self.fake)
        self.runtime = SimpleNamespace(observability_application=self.handler)
        self.output = StringIO()
        self.answers: list[str] = []
        self.adapter = ObservabilityCliAdapter(
            self.runtime,
            output_provider=self.output.write,
            input_provider=lambda prompt: self.answers.pop(0),
        )

    def _run(self, raw):
        self.output.truncate(0)
        self.output.seek(0)
        return self.adapter.handle(raw)

    def test_docker_list_renders(self):
        result = self._run("docker list")
        self.assertTrue(result.ok)
        self.assertIn("Docker Containers (3)", self.output.getvalue())

    def test_docker_ps_is_an_alias_for_list(self):
        result = self._run("docker ps")
        self.assertTrue(result.ok)
        self.assertIn("Docker Containers (3)", self.output.getvalue())

    def test_docker_list_json_parses(self):
        result = self._run("docker list --json")
        self.assertTrue(result.ok)
        parsed = json.loads(self.output.getvalue())
        self.assertEqual(parsed["data"]["total"], 3)

    def test_docker_list_rejects_extra_args(self):
        self.assertFalse(self._run("docker list extra").ok)

    def test_docker_inspect_renders(self):
        result = self._run("docker inspect web")
        self.assertTrue(result.ok)
        self.assertIn("Container: web", self.output.getvalue())

    def test_docker_inspect_needs_a_name(self):
        self.assertFalse(self._run("docker inspect").ok)

    def test_docker_inspect_unknown_is_failure(self):
        result = self._run("docker inspect nope")
        self.assertFalse(result.ok)
        self.assertIn("No container matches", result.message)

    def test_docker_unknown_subcommand_is_usage_failure(self):
        result = self._run("docker frobnicate")
        self.assertFalse(result.ok)
        self.assertIn("Unknown docker subcommand", result.message)

    def test_docker_bare_is_usage_failure(self):
        result = self._run("docker")
        self.assertFalse(result.ok)
        self.assertIn("Usage: docker list", result.message)

    def test_docker_rejects_unknown_option(self):
        self.assertFalse(self._run("docker list --nope").ok)

    def test_dispatch_recognizes_docker(self):
        with patch("builtins.print"):
            self.assertTrue(dispatch_observability(self.runtime, "docker list"))
            self.assertTrue(dispatch_observability(self.runtime, "  DOCKER inspect web "))
        self.assertFalse(dispatch_observability(self.runtime, "dockers"))

    def test_router_handles_docker_list(self):
        router = CommandRouter(self.runtime)
        with patch("builtins.print") as output:
            self.assertTrue(router.handle("docker list"))
        rendered = "\n".join(
            str(call.args[0]) for call in output.call_args_list if call.args
        )
        self.assertIn("Docker Containers (3)", rendered)

    def test_docker_stop_approved_applies(self):
        self.answers = ["y"]
        result = self._run("docker stop web")
        self.assertTrue(result.ok)
        self.assertIn("Docker stop: web", self.output.getvalue())
        self.assertEqual(self.fake.states[_WEB_ID], "exited")

    def test_docker_stop_yes_word_applies(self):
        self.answers = ["yes"]
        result = self._run("docker stop web")
        self.assertTrue(result.ok)

    def test_docker_stop_shows_plan_and_warning_first(self):
        self.answers = ["y"]
        self._run("docker stop web")
        rendered = self.output.getvalue()
        self.assertIn("Container: web", rendered)
        self.assertIn("Sensitive action: docker stop 'web'", rendered)

    def test_docker_stop_declined_cancels(self):
        self.answers = ["n"]
        result = self._run("docker stop web")
        self.assertFalse(result.ok)
        self.assertIn("Cancelled", result.message)
        self.assertEqual(self.fake.states[_WEB_ID], "running")
        self.assertFalse(
            any(call[1] == "stop" for call in self.fake.calls if len(call) > 1)
        )

    def test_docker_stop_empty_answer_cancels(self):
        self.answers = [""]
        result = self._run("docker stop web")
        self.assertFalse(result.ok)
        self.assertIn("Cancelled", result.message)
        self.assertEqual(self.fake.states[_WEB_ID], "running")

    def test_docker_stop_unknown_never_prompts(self):
        self.answers = ["y"]
        result = self._run("docker stop nope")
        self.assertFalse(result.ok)
        self.assertIn("No container matches", result.message)
        self.assertEqual(self.answers, ["y"])

    def test_docker_stop_daemon_down_never_prompts(self):
        down_handler = _handler(self.paths, _FakeDocker(daemon_down=True))
        down_runtime = SimpleNamespace(observability_application=down_handler)
        answers = ["y"]
        output = StringIO()
        adapter = ObservabilityCliAdapter(
            down_runtime,
            output_provider=output.write,
            input_provider=lambda prompt: answers.pop(0),
        )
        result = adapter.handle("docker stop web")
        self.assertFalse(result.ok)
        self.assertIn("Docker is not available", result.message)
        self.assertEqual(answers, ["y"])

    def test_docker_start_needs_a_name(self):
        self.assertFalse(self._run("docker start").ok)

    def test_docker_health_renders(self):
        result = self._run("docker health")
        self.assertFalse(result.ok)
        self.assertIn("Docker Health: ERROR", self.output.getvalue())

    def test_docker_health_json_parses(self):
        healthy_handler = _handler(self.paths, _FakeDocker(ps_rows=[_PS_ROWS[0]]))
        healthy_runtime = SimpleNamespace(observability_application=healthy_handler)
        output = StringIO()
        adapter = ObservabilityCliAdapter(
            healthy_runtime,
            output_provider=output.write,
            input_provider=lambda prompt: "",
        )
        result = adapter.handle("docker health --json")
        self.assertTrue(result.ok)
        parsed = json.loads(output.getvalue())
        self.assertEqual(parsed["data"]["overall"], "ok")

    def test_docker_health_failure_with_json_renders_text(self):
        result = self._run("docker health --json")
        self.assertFalse(result.ok)
        self.assertIn("Docker Health: ERROR", self.output.getvalue())

    def test_docker_health_rejects_extra_args(self):
        self.assertFalse(self._run("docker health web").ok)

    def test_docker_logs_renders(self):
        self.fake.log_lines = ["booted", "listening on :80"]
        result = self._run("docker logs web")
        self.assertTrue(result.ok)
        rendered = self.output.getvalue()
        self.assertIn("Logs: web", rendered)
        self.assertIn("booted", rendered)
        self.assertIn("listening on :80", rendered)

    def test_docker_logs_tail_option(self):
        self.fake.log_lines = [f"line-{i}" for i in range(5)]
        result = self._run("docker logs web --tail 2")
        self.assertTrue(result.ok)
        rendered = self.output.getvalue()
        self.assertIn("line-4", rendered)
        self.assertNotIn("line-0", rendered)

    def test_docker_logs_tail_missing_value(self):
        self.assertFalse(self._run("docker logs web --tail").ok)

    def test_docker_logs_tail_non_integer(self):
        self.assertFalse(self._run("docker logs web --tail abc").ok)

    def test_docker_logs_needs_a_name(self):
        self.assertFalse(self._run("docker logs").ok)

    def test_docker_diagnose_renders(self):
        handler = _handler(self.paths, self.fake, _probe_ok)
        runtime = SimpleNamespace(observability_application=handler)
        adapter = ObservabilityCliAdapter(
            runtime,
            output_provider=self.output.write,
            input_provider=lambda prompt: self.answers.pop(0),
        )
        self.output.truncate(0)
        self.output.seek(0)
        result = adapter.handle("docker diagnose web")
        self.assertTrue(result.ok)
        rendered = self.output.getvalue()
        self.assertIn("Docker Diagnosis: web", rendered)
        self.assertIn("Verdict   : ok", rendered)

    def test_docker_diagnose_needs_a_name(self):
        self.assertFalse(self._run("docker diagnose").ok)

    def test_docker_diagnose_rejects_tail(self):
        self.assertFalse(self._run("docker diagnose web --tail 5").ok)

    def test_docker_list_rejects_tail(self):
        self.assertFalse(self._run("docker list --tail 5").ok)


class DockerCoverageTests(TestCase):
    def test_docker_in_router_help_base_commands_and_guide(self):
        router = CommandRouter(SimpleNamespace(plugin_manager=SimpleNamespace(help_lines=lambda: ())))
        with patch("builtins.print") as output:
            router.show_help()
        help_text = "\n".join(
            str(call.args[0]) for call in output.call_args_list if call.args
        )
        for command in (
            "docker list",
            "docker inspect",
            "docker start",
            "docker stop",
            "docker restart",
            "docker health",
            "docker logs",
            "docker diagnose",
        ):
            self.assertIn(command, help_text)
        for command in (
            "docker",
            "docker list",
            "docker ps",
            "docker inspect",
            "docker start",
            "docker stop",
            "docker restart",
            "docker health",
            "docker logs",
            "docker diagnose",
        ):
            self.assertIn(command, BASE_COMMANDS)
        guide = Path(__file__).resolve().parents[1] / "docs" / "USER_GUIDE.md"
        guide_text = guide.read_text(encoding="utf-8")
        for command in (
            "docker list",
            "docker inspect",
            "docker start",
            "docker stop",
            "docker restart",
            "docker health",
            "docker logs",
        ):
            self.assertIn(command, guide_text)

    def test_docker_feature_page_exists(self):
        page = Path(__file__).resolve().parents[1] / "docs" / "DOCKER.md"
        text = page.read_text(encoding="utf-8")
        self.assertIn("docker health", text)
        self.assertIn("Approve? [y/N]", text)
        self.assertIn("recovery --kind docker", text)

    def test_roadmap_checks_off_docker(self):
        roadmap = Path(__file__).resolve().parents[1] / "docs" / "ROADMAP.md"
        text = roadmap.read_text(encoding="utf-8")
        self.assertIn("- [x] **Docker:**", text)
