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
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping

AVAILABILITY_TIMEOUT_SECONDS = 15.0
DEFAULT_TIMEOUT_SECONDS = 30.0
CONTROL_TIMEOUT_SECONDS = 60.0
MIN_ID_PREFIX = 4

CONTROL_ACTIONS = ("start", "stop", "restart")

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
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stderr=subprocess.STDOUT if merge_stderr else None,
    )


def _health_from_status(status: str) -> str:
    lowered = (status or "").lower()
    if "(healthy)" in lowered:
        return "healthy"
    if "(unhealthy)" in lowered:
        return "unhealthy"
    if "(starting)" in lowered:
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
    ) -> None:
        self._run = runner or _default_runner
        self._docker = docker_bin

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
