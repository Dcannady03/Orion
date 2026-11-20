"""Docker container discovery, control, and health checks.

Talks to the ``docker`` CLI via subprocess (no new dependencies). Every
call fails closed when the CLI is missing or the daemon is unreachable:
callers get DockerUnavailableError, never a partial answer.

Container names resolve the way Docker resolves them: an exact name, an
exact id, or a unique id prefix (at least four characters). An unknown
name or an ambiguous prefix is refused, never guessed.
"""
from __future__ import annotations

import json
import socket
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

AVAILABILITY_TIMEOUT_SECONDS = 15.0
DEFAULT_TIMEOUT_SECONDS = 30.0
CONTROL_TIMEOUT_SECONDS = 60.0
PORT_PROBE_TIMEOUT_SECONDS = 3.0
MIN_ID_PREFIX = 4

CONTROL_ACTIONS = ("start", "stop", "restart")
DEFAULT_LOG_TAIL = 50
MAX_LOG_TAIL = 500
MAX_LOG_CHARS = 20_000
DIAGNOSE_LOG_TAIL = 100
DIAGNOSE_MAX_ERROR_LINES = 5
DIAGNOSE_MAX_LINE_CHARS = 200
_LOG_ERROR_MARKERS = ("error", "exception", "fail", "traceback", "panic")

_DAEMON_ERROR_MARKERS = (
    "cannot connect to the docker daemon",
    "error during connect",
    "is the docker daemon running",
    "daemon is not running",
    "pipe/docker",
)


class DockerUnavailableError(Exception):
    """Docker cannot be reached. Fail closed; never guess at an answer."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class UnknownContainerError(ValueError):
    """No container matches the given name or id."""


class AmbiguousContainerError(ValueError):
    """An id prefix matches more than one container."""


class DockerCommandError(Exception):
    """A docker command failed for a reason other than availability."""


def _plain(value: Any) -> Any:
    """Return a plain JSON-compatible copy of a nested value."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _default_runner(
    args: list[str], timeout: float, *, merge_stderr: bool = False
) -> subprocess.CompletedProcess[str]:
    if merge_stderr:
        # capture_output=True conflicts with an explicit stderr argument,
        # so merge stderr into stdout with explicit pipes instead.
        return subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _health_from_status(status: str) -> str:
    lowered = (status or "").lower()
    if "(healthy)" in lowered:
        return "healthy"
    if "(unhealthy)" in lowered:
        return "unhealthy"
    if "(health: starting)" in lowered or "(starting)" in lowered:
        return "starting"
    return "none"


def _state_from_status(status: str) -> str:
    lowered = (status or "").strip().lower()
    if lowered.startswith("up"):
        return "running"
    for state in ("exited", "created", "paused", "restarting", "removing", "dead"):
        if lowered.startswith(state):
            return state
    return lowered.split(" ", 1)[0] if lowered else "unknown"


@dataclass(frozen=True)
class ContainerSummary:
    """One container as listed by `docker ps`."""

    container_id: str
    name: str
    image: str
    status: str
    state: str
    health: str
    ports: tuple[str, ...] = ()
    created: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.container_id,
            "name": self.name,
            "image": self.image,
            "status": self.status,
            "state": self.state,
            "health": self.health,
            "ports": list(self.ports),
            "created": self.created,
        }


@dataclass(frozen=True)
class ContainerList:
    """Listed containers, plus malformed lines that were skipped."""

    containers: tuple[ContainerSummary, ...] = ()
    skipped: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "containers": [item.to_dict() for item in self.containers],
            "skipped": self.skipped,
            "total": len(self.containers),
        }


@dataclass(frozen=True)
class PublishedPort:
    """One host port published by a container (a real host binding).

    This is the authoritative "reachable from the host" signal: it only
    exists when inspect reports a host binding for the port. A port the
    image merely exposes (null bindings) is not published.
    """

    host_ip: str
    host_port: int
    container_port: str

    def to_dict(self) -> dict[str, object]:
        return {
            "host_ip": self.host_ip,
            "host_port": self.host_port,
            "container_port": self.container_port,
        }


@dataclass(frozen=True)
class ContainerDetail:
    """One container in full, as reported by `docker inspect`."""

    container_id: str
    name: str
    image: str
    created: str
    status: str
    state: str
    running: bool
    health: str
    exit_code: int
    started_at: str
    finished_at: str
    ports: tuple[str, ...] = ()
    mounts: tuple[str, ...] = ()
    published_ports: tuple[PublishedPort, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.container_id,
            "name": self.name,
            "image": self.image,
            "created": self.created,
            "status": self.status,
            "state": self.state,
            "running": self.running,
            "health": self.health,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ports": list(self.ports),
            "mounts": list(self.mounts),
            "published_ports": [port.to_dict() for port in self.published_ports],
        }


@dataclass(frozen=True)
class DockerControlOutcome:
    """What happened when a control action ran (or was refused)."""

    action: str
    name: str
    applied: bool
    verified: bool
    detail: str
    state_before: str = ""
    state_after: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "name": self.name,
            "applied": self.applied,
            "verified": self.verified,
            "detail": self.detail,
            "state_before": self.state_before,
            "state_after": self.state_after,
        }


@dataclass(frozen=True)
class DockerHealthSummary:
    """Container health counts across every container."""

    total: int
    running: int
    exited: int
    healthy: int
    unhealthy: int
    starting: int
    no_healthcheck: int
    overall: str
    containers: tuple[ContainerSummary, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "running": self.running,
            "exited": self.exited,
            "healthy": self.healthy,
            "unhealthy": self.unhealthy,
            "starting": self.starting,
            "no_healthcheck": self.no_healthcheck,
            "overall": self.overall,
            "containers": [item.to_dict() for item in self.containers],
        }


@dataclass(frozen=True)
class ContainerLogResult:
    """Recent log lines for one container, bounded and capped."""

    name: str
    lines: tuple[str, ...] = ()
    truncated: bool = False
    tail: int = DEFAULT_LOG_TAIL

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "lines": list(self.lines),
            "truncated": self.truncated,
            "tail": self.tail,
            "line_count": len(self.lines),
        }


@dataclass(frozen=True)
class PortProbeResult:
    """What a TCP + HTTP probe of one published port found."""

    host_ip: str
    host_port: int
    container_port: str
    tcp_ok: bool
    http_status: int | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "host_ip": self.host_ip,
            "host_port": self.host_port,
            "container_port": self.container_port,
            "tcp_ok": self.tcp_ok,
            "http_status": self.http_status,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DiagnoseFinding:
    """One check in a container diagnosis."""

    check: str
    status: str  # "ok" | "problem" | "info"
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "check": self.check,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ContainerDiagnosis:
    """The full result of diagnosing one container (read-only)."""

    name: str
    container_id: str
    state: str
    running: bool
    exit_code: int
    finished_at: str
    verdict: str
    cause: str
    suggestion: str
    findings: tuple[DiagnoseFinding, ...] = ()
    port_probes: tuple[PortProbeResult, ...] = ()
    error_lines: tuple[str, ...] = ()
    error_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "container_id": self.container_id,
            "state": self.state,
            "running": self.running,
            "exit_code": self.exit_code,
            "finished_at": self.finished_at,
            "verdict": self.verdict,
            "cause": self.cause,
            "suggestion": self.suggestion,
            "findings": [item.to_dict() for item in self.findings],
            "port_probes": [item.to_dict() for item in self.port_probes],
            "error_lines": list(self.error_lines),
            "error_count": self.error_count,
        }


def _probe_host(host_ip: str) -> str:
    """Map a bind address to the address worth probing from this machine."""
    if (host_ip or "").strip() in {"", "0.0.0.0", "::"}:
        return "127.0.0.1"
    return host_ip


def _probe_port(
    host_ip: str,
    host_port: int,
    container_port: str,
    timeout: float = PORT_PROBE_TIMEOUT_SECONDS,
) -> PortProbeResult:
    """TCP-connect to a published port, then try a plain HTTP GET.

    Never raises: every failure is reported in the result. A port that
    answers TCP but is not HTTP (a database, for example) is reported
    as TCP-ok with no HTTP status, not as a failure.
    """
    probe_host = _probe_host(host_ip)
    try:
        with socket.create_connection((probe_host, host_port), timeout=timeout):
            pass
    except Exception as exc:  # noqa: BLE001 - a probe must report, never crash
        return PortProbeResult(
            host_ip,
            host_port,
            container_port,
            False,
            None,
            f"TCP connect to {probe_host}:{host_port} failed: {exc}",
        )
    url = f"http://{probe_host}:{host_port}/"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        return PortProbeResult(
            host_ip,
            host_port,
            container_port,
            True,
            int(exc.code),
            f"HTTP {exc.code} from {probe_host}:{host_port}",
        )
    except Exception as exc:  # noqa: BLE001 - not HTTP, or not answering HTTP
        return PortProbeResult(
            host_ip,
            host_port,
            container_port,
            True,
            None,
            f"Port answers TCP but not HTTP: {exc}",
        )
    return PortProbeResult(
        host_ip,
        host_port,
        container_port,
        True,
        status,
        f"HTTP {status} from {probe_host}:{host_port}",
    )


def _scan_log_errors(lines) -> tuple[tuple[str, ...], int]:
    """Find error-looking log lines: newest first, capped and truncated."""
    matches = [
        line.strip()[:DIAGNOSE_MAX_LINE_CHARS]
        for line in lines
        if any(marker in line.lower() for marker in _LOG_ERROR_MARKERS)
    ]
    recent = tuple(reversed(matches[-DIAGNOSE_MAX_ERROR_LINES:]))
    return recent, len(matches)


class DockerService:
    """Drive the `docker` CLI for discovery, control, and health checks.

    The runner is injectable so tests can run without a daemon. Every
    method raises DockerUnavailableError when the CLI is missing or the
    daemon cannot be reached, and raises UnknownContainerError or
    AmbiguousContainerError instead of guessing at a container.
    """

    def __init__(
        self,
        *,
        runner: Callable[..., Any] | None = None,
        docker_bin: str = "docker",
        port_probe: Callable[[str, int, str], PortProbeResult] | None = None,
    ) -> None:
        self._run = runner or _default_runner
        self._docker = docker_bin
        self._port_probe = port_probe or _probe_port

    def require_available(self) -> str:
        """Return the daemon version, or raise DockerUnavailableError."""
        try:
            result = self._run(
                [self._docker, "version", "--format", "{{.Server.Version}}"],
                AVAILABILITY_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            raise DockerUnavailableError(
                "cli_missing",
                "The `docker` command was not found on this machine.",
            ) from None
        except subprocess.TimeoutExpired:
            raise DockerUnavailableError(
                "timeout",
                "Docker did not answer in time; the daemon may be busy.",
            ) from None
        if result.returncode != 0:
            raise DockerUnavailableError(
                "daemon_unreachable",
                _daemon_detail(result),
            )
        return str(result.stdout or "").strip()

    def _execute(
        self,
        args: list[str],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        merge_stderr: bool = False,
    ):
        """Run one docker command, mapping failures to typed errors."""
        try:
            result = self._run(
                [self._docker, *args], timeout, merge_stderr=merge_stderr
            )
        except FileNotFoundError:
            raise DockerUnavailableError(
                "cli_missing",
                "The `docker` command was not found on this machine.",
            ) from None
        except subprocess.TimeoutExpired:
            raise DockerUnavailableError(
                "timeout",
                f"Docker did not answer in time ({args[0] if args else 'command'}).",
            ) from None
        if result.returncode != 0:
            stderr = str(getattr(result, "stderr", "") or "")
            if _looks_like_daemon_error(stderr):
                raise DockerUnavailableError(
                    "daemon_unreachable", _daemon_detail(result)
                )
            raise DockerCommandError(_first_line(stderr) or "Docker command failed.")
        return result

    def list_containers(self) -> ContainerList:
        """List every container, running or stopped, with status and health."""
        result = self._execute(["ps", "-a", "--format", "{{json .}}"])
        containers: list[ContainerSummary] = []
        skipped = 0
        for line in str(result.stdout or "").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(data, dict):
                skipped += 1
                continue
            containers.append(_summary_from_ps(data))
        return ContainerList(tuple(containers), skipped)

    def resolve(self, name: str) -> ContainerSummary:
        """Resolve a name or id to one container, or refuse.

        Matches an exact name, an exact id, or a unique id prefix of at
        least four characters. Anything else raises instead of guessing.
        """
        query = (name or "").strip().lstrip("/")
        if not query:
            raise UnknownContainerError("A container name or id is required.")
        listing = self.list_containers()
        for item in listing.containers:
            if item.name == query:
                return item
        for item in listing.containers:
            if item.container_id == query:
                return item
        if len(query) >= MIN_ID_PREFIX:
            matches = [
                item
                for item in listing.containers
                if item.container_id.startswith(query)
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                names = ", ".join(item.name for item in matches)
                raise AmbiguousContainerError(
                    f"Id prefix {query!r} matches more than one container: {names}. "
                    "Use a longer id or the container name."
                )
        raise UnknownContainerError(f"No container matches: {name}")

    def inspect_container(self, name: str) -> ContainerDetail:
        """Return full detail for one container."""
        target = self.resolve(name)
        result = self._execute(
            ["inspect", "--format", "{{json .}}", target.container_id]
        )
        try:
            data = json.loads(str(result.stdout or ""))
        except json.JSONDecodeError:
            raise DockerCommandError(
                f"Could not read inspect output for {target.name}."
            ) from None
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            raise DockerCommandError(
                f"Could not read inspect output for {target.name}."
            )
        return _detail_from_inspect(data)

    def control(self, action: str, name: str) -> DockerControlOutcome:
        """Start, stop, or restart one container, then verify the result.

        Re-resolves the container first, so a stale name can never act on
        the wrong container, and re-inspects afterwards to verify the new
        state. Raises UnknownContainerError, AmbiguousContainerError, or
        DockerUnavailableError instead of acting on doubt.
        """
        normalized = (action or "").strip().lower()
        if normalized not in CONTROL_ACTIONS:
            raise ValueError(
                f"Unknown docker control action: {action}. "
                f"Expected one of: {', '.join(CONTROL_ACTIONS)}."
            )
        target = self.resolve(name)
        before = target.state
        try:
            self._execute(
                [normalized, target.container_id],
                timeout=CONTROL_TIMEOUT_SECONDS,
            )
        except DockerCommandError as exc:
            return DockerControlOutcome(
                action=normalized,
                name=target.name,
                applied=False,
                verified=False,
                detail=str(exc),
                state_before=before,
                state_after=before,
            )
        try:
            after = self.inspect_container(target.container_id)
            after_state = after.state
        except (UnknownContainerError, DockerCommandError, DockerUnavailableError):
            return DockerControlOutcome(
                action=normalized,
                name=target.name,
                applied=True,
                verified=False,
                detail=(
                    f"{normalized} ran, but the container could not be "
                    "re-inspected to verify the result."
                ),
                state_before=before,
                state_after="unknown",
            )
        verified = _control_verified(normalized, after_state)
        if normalized == "start" and before == "running":
            detail = f"{target.name} is already running."
        elif normalized == "stop" and before in {"exited", "created"}:
            detail = f"{target.name} is already stopped."
        elif verified:
            past = {"start": "started", "stop": "stopped", "restart": "restarted"}[
                normalized
            ]
            detail = f"{target.name} {past}: {before} -> {after_state}."
        else:
            detail = (
                f"{normalized} ran, but {target.name} is {after_state} "
                f"(expected {_expected_state(normalized)})."
            )
        return DockerControlOutcome(
            action=normalized,
            name=target.name,
            applied=True,
            verified=verified,
            detail=detail,
            state_before=before,
            state_after=after_state,
        )

    def health_summary(self) -> DockerHealthSummary:
        """Summarize container health across every container."""
        listing = self.list_containers()
        running = sum(1 for item in listing.containers if item.state == "running")
        exited = sum(1 for item in listing.containers if item.state == "exited")
        healthy = sum(1 for item in listing.containers if item.health == "healthy")
        unhealthy = sum(
            1 for item in listing.containers if item.health == "unhealthy"
        )
        starting = sum(1 for item in listing.containers if item.health == "starting")
        no_healthcheck = sum(
            1 for item in listing.containers if item.health == "none"
        )
        if unhealthy:
            overall = "error"
        elif starting:
            overall = "warning"
        else:
            overall = "ok"
        return DockerHealthSummary(
            total=len(listing.containers),
            running=running,
            exited=exited,
            healthy=healthy,
            unhealthy=unhealthy,
            starting=starting,
            no_healthcheck=no_healthcheck,
            overall=overall,
            containers=listing.containers,
        )

    def container_logs(
        self, name: str, tail: int = DEFAULT_LOG_TAIL
    ) -> ContainerLogResult:
        """Return recent log lines for one container, bounded and capped."""
        try:
            wanted = int(tail)
        except (TypeError, ValueError):
            raise ValueError("docker logs --tail must be an integer.") from None
        clamped = max(1, min(wanted, MAX_LOG_TAIL))
        target = self.resolve(name)
        result = self._execute(
            ["logs", "--tail", str(clamped), target.container_id],
            merge_stderr=True,
        )
        lines = str(result.stdout or "").splitlines()
        total_chars = sum(len(line) + 1 for line in lines)
        truncated = False
        if total_chars > MAX_LOG_CHARS:
            kept: list[str] = []
            budget = MAX_LOG_CHARS
            for line in reversed(lines):
                if budget - (len(line) + 1) < 0:
                    break
                kept.append(line)
                budget -= len(line) + 1
            lines = list(reversed(kept))
            truncated = True
        return ContainerLogResult(
            name=target.name,
            lines=tuple(lines),
            truncated=truncated,
            tail=clamped,
        )

    def diagnose_container(
        self,
        name: str,
        *,
        port_probe: Callable[[str, int, str], PortProbeResult] | None = None,
        log_tail: int = DIAGNOSE_LOG_TAIL,
    ) -> ContainerDiagnosis:
        """Diagnose why one container cannot be reached (read-only).

        Never starts, stops, or restarts anything. Checks the container
        state, probes each published host port, and scans recent logs
        for error signals, then names one likely cause. The port probe
        is injectable so tests can run without a network.
        """
        detail = self.inspect_container(name)
        probe = port_probe or self._port_probe
        findings: list[DiagnoseFinding] = []
        probes: list[PortProbeResult] = []
        if detail.running:
            findings.append(
                DiagnoseFinding(
                    "state", "ok", f"Container is running ({detail.state})."
                )
            )
        else:
            finished = detail.finished_at or "unknown time"
            findings.append(
                DiagnoseFinding(
                    "state",
                    "problem",
                    f"Container is {detail.state} (exit code "
                    f"{detail.exit_code}, finished {finished}).",
                )
            )
        if detail.running:
            if not detail.published_ports:
                findings.append(
                    DiagnoseFinding(
                        "ports",
                        "problem",
                        "No published ports: nothing on the host can reach "
                        "this container.",
                    )
                )
            for port in detail.published_ports:
                try:
                    result = probe(
                        port.host_ip, port.host_port, port.container_port
                    )
                except Exception as exc:  # noqa: BLE001 - a probe must report, never crash
                    result = PortProbeResult(
                        port.host_ip,
                        port.host_port,
                        port.container_port,
                        False,
                        None,
                        f"Port probe failed: {exc}",
                    )
                probes.append(result)
                if not result.tcp_ok:
                    findings.append(
                        DiagnoseFinding("port", "problem", result.detail)
                    )
                elif (
                    result.http_status is not None and result.http_status >= 400
                ):
                    findings.append(
                        DiagnoseFinding("port", "problem", result.detail)
                    )
                else:
                    findings.append(DiagnoseFinding("port", "ok", result.detail))
        try:
            logs = self.container_logs(detail.container_id, tail=log_tail)
            error_lines, error_count = _scan_log_errors(logs.lines)
        except Exception as exc:  # noqa: BLE001 - unreadable logs are a finding
            error_lines, error_count = (), 0
            findings.append(
                DiagnoseFinding("logs", "info", f"Could not read logs: {exc}")
            )
        else:
            if error_count:
                findings.append(
                    DiagnoseFinding(
                        "logs",
                        "problem",
                        f"{error_count} error-looking line(s) in the last "
                        f"{logs.tail} log line(s).",
                    )
                )
            else:
                findings.append(
                    DiagnoseFinding(
                        "logs",
                        "ok",
                        f"No error signals in the last {logs.tail} log line(s).",
                    )
                )
        if not detail.running:
            verdict = "not-running"
            cause = (
                f"Container '{detail.name}' is {detail.state} "
                f"(exit code {detail.exit_code}). A stopped container "
                "cannot answer."
            )
            suggestion = (
                f"Start it: docker start {detail.name} (asks for approval), "
                f"then run docker diagnose {detail.name} again if it still "
                "cannot be reached."
            )
        elif not detail.published_ports:
            verdict = "no-published-ports"
            cause = (
                f"Container '{detail.name}' is running but publishes no "
                "host port, so nothing on the host can reach it."
            )
            suggestion = (
                "Recreate the container with a published port "
                "(docker run -p <host>:<container>), or check its compose file."
            )
        elif any(not item.tcp_ok for item in probes):
            verdict = "port-unreachable"
            dead = ", ".join(
                str(item.host_port) for item in probes if not item.tcp_ok
            )
            cause = (
                f"Container '{detail.name}' is running but port(s) {dead} "
                "do not answer on the host."
            )
            suggestion = (
                "The app may have crashed or bound to the wrong address. "
                f"Check docker logs {detail.name}, then try "
                f"docker restart {detail.name} (asks for approval)."
            )
        elif any(
            item.http_status is not None and item.http_status >= 400
            for item in probes
        ):
            verdict = "http-error"
            bad = ", ".join(
                f"{item.host_port} (HTTP {item.http_status})"
                for item in probes
                if item.http_status is not None and item.http_status >= 400
            )
            cause = (
                f"Container '{detail.name}' answers, but returns HTTP "
                f"errors: {bad}."
            )
            suggestion = (
                "This looks like an app-level problem. Check "
                f"docker logs {detail.name} for the failing request."
            )
        else:
            verdict = "ok"
            cause = (
                f"Container '{detail.name}' is running and its published "
                "port(s) answer."
            )
            suggestion = (
                "Nothing wrong was found with the container itself; the "
                "problem may be elsewhere (network, firewall, or the client)."
            )
        return ContainerDiagnosis(
            name=detail.name,
            container_id=detail.container_id,
            state=detail.state,
            running=detail.running,
            exit_code=detail.exit_code,
            finished_at=detail.finished_at,
            verdict=verdict,
            cause=cause,
            suggestion=suggestion,
            findings=tuple(findings),
            port_probes=tuple(probes),
            error_lines=error_lines,
            error_count=error_count,
        )


def _summary_from_ps(data: Mapping[str, Any]) -> ContainerSummary:
    raw_names = str(data.get("Names", "") or "")
    name = raw_names.split(",")[0].strip().lstrip("/") if raw_names else ""
    status = str(data.get("Status", "") or "")
    raw_ports = str(data.get("Ports", "") or "")
    ports = tuple(
        part.strip() for part in raw_ports.split(",") if part.strip()
    )
    return ContainerSummary(
        container_id=str(data.get("ID", "") or ""),
        name=name,
        image=str(data.get("Image", "") or ""),
        status=status,
        state=_state_from_status(status),
        health=_health_from_status(status),
        ports=ports,
        created=str(data.get("CreatedAt", "") or ""),
    )


def _detail_from_inspect(data: Mapping[str, Any]) -> ContainerDetail:
    state = data.get("State", {}) if isinstance(data.get("State"), dict) else {}
    config = data.get("Config", {}) if isinstance(data.get("Config"), dict) else {}
    network = (
        data.get("NetworkSettings", {})
        if isinstance(data.get("NetworkSettings"), dict)
        else {}
    )
    health_data = state.get("Health", {})
    health = (
        str(health_data.get("Status", "none"))
        if isinstance(health_data, dict)
        else "none"
    )
    raw_status = str(state.get("Status", "") or "")
    running = bool(state.get("Running", False))
    ports: list[str] = []
    published: list[PublishedPort] = []
    port_map = network.get("Ports", {})
    if isinstance(port_map, dict):
        for container_port in sorted(port_map):
            bindings = port_map[container_port]
            if isinstance(bindings, list) and bindings:
                for binding in bindings:
                    if not isinstance(binding, dict):
                        continue
                    host_ip = str(binding.get("HostIp", "") or "")
                    host_port = str(binding.get("HostPort", "") or "")
                    ports.append(f"{host_ip}:{host_port}->{container_port}")
                    try:
                        published.append(
                            PublishedPort(
                                host_ip, int(host_port), str(container_port)
                            )
                        )
                    except (TypeError, ValueError):
                        continue
            else:
                ports.append(f"{container_port} (not published)")
    mounts: list[str] = []
    raw_mounts = data.get("Mounts", [])
    if isinstance(raw_mounts, list):
        for mount in raw_mounts:
            if not isinstance(mount, dict):
                continue
            source = str(mount.get("Source", "") or "")
            destination = str(mount.get("Destination", "") or "")
            kind = str(mount.get("Type", "") or "")
            mounts.append(f"{source} -> {destination} ({kind})")
    return ContainerDetail(
        container_id=str(data.get("Id", "") or ""),
        name=str(data.get("Name", "") or "").lstrip("/"),
        image=str(config.get("Image", "") or ""),
        created=str(data.get("Created", "") or ""),
        status=raw_status,
        state=raw_status.lower() or ("running" if running else "unknown"),
        running=running,
        health=health,
        exit_code=int(state.get("ExitCode", 0) or 0),
        started_at=str(state.get("StartedAt", "") or ""),
        finished_at=str(state.get("FinishedAt", "") or ""),
        ports=tuple(ports),
        mounts=tuple(mounts),
        published_ports=tuple(published),
    )


def _expected_state(action: str) -> str:
    if action == "stop":
        return "exited"
    return "running"


def _control_verified(action: str, state: str) -> bool:
    if action == "stop":
        return state in {"exited", "created"}
    return state == "running"


def _looks_like_daemon_error(stderr: str) -> bool:
    lowered = (stderr or "").lower()
    return any(marker in lowered for marker in _DAEMON_ERROR_MARKERS)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


def _daemon_detail(result) -> str:
    detail = _first_line(str(getattr(result, "stderr", "") or ""))
    if not detail:
        detail = _first_line(str(getattr(result, "stdout", "") or ""))
    if not detail:
        detail = "Docker daemon is not reachable."
    return detail
