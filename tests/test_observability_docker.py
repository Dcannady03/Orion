"""Tests for Orion Docker integration (discovery, control, health, logs)."""
from __future__ import annotations

import json
import os
import subprocess
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
    ContainerList,
    ContainerSummary,
    DockerService,
    DockerUnavailableError,
    ObservabilityApplicationHandler,
    UnknownContainerError,
)
from orion.core.paths import OrionPaths
from orion.core.router import CommandRouter

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
    ):
        self.daemon_down = daemon_down
        self.cli_missing = cli_missing
        self.timeout = timeout
        self.calls: list[list[str]] = []
        self.ps_rows = list(_PS_ROWS if ps_rows is None else ps_rows)
        self.inspect_data = dict(_INSPECT if inspect_data is None else inspect_data)
        self.log_lines = list(log_lines if log_lines is not None else [])
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


def _service(fake=None):
    return DockerService(runner=fake or _FakeDocker())


class _Clock:
    def __call__(self):
        from datetime import datetime, timezone

        return datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _handler(paths, fake=None):
    return ObservabilityApplicationHandler(
        paths=paths,
        docker_service=_service(fake),
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
