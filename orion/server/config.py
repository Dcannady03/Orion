"""Validated server configuration for the headless runtime.

Built from the ``server:`` config root (``config/default.yaml`` plus the
user's ``~/.orion/config.yaml`` overrides). The host is loopback-only
in Phase A: any non-loopback host is refused at construction, so a
misconfigured file can never expose the unauthenticated prototype API
to the network.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from orion.core.config import ConfigManager


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Dev/testing only, never for a deployed server (§3.3 invariant): with
# auth_required on (the default) the server refuses to start unless this
# is explicitly "off" for the process. Phase B implements real auth.
AUTH_OFF_ENV_VAR = "ORION_SERVER_AUTH"


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    auth_required: bool = True
    shutdown_drain_seconds: int = 30
    max_concurrent_requests: int = 8
    max_request_bytes: int = 1_048_576
    max_response_bytes: int = 4_194_304
    request_timeout_seconds: int = 60

    def __post_init__(self) -> None:
        host = str(self.host).strip().lower()
        if host not in LOOPBACK_HOSTS:
            raise ValueError(
                "The Phase A server binds loopback only; "
                f"refusing host {self.host!r}."
            )
        object.__setattr__(self, "host", host)
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("Server port must be between 1 and 65535.")
        for name in (
            "shutdown_drain_seconds",
            "max_concurrent_requests",
            "max_request_bytes",
            "max_response_bytes",
            "request_timeout_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"Server {name} must be a positive integer.")
        if not isinstance(self.auth_required, bool):
            raise ValueError("Server auth_required must be a boolean.")

    @classmethod
    def from_config(cls, config_manager: ConfigManager) -> "ServerConfig":
        """Read the server root, falling back to the built-in defaults."""
        get = config_manager.get
        return cls(
            host=str(get("server.host", "127.0.0.1")),
            port=int(get("server.port", 8000)),
            auth_required=bool(get("server.auth_required", True)),
            shutdown_drain_seconds=int(get("server.shutdown_drain_seconds", 30)),
            max_concurrent_requests=int(get("server.max_concurrent_requests", 8)),
            max_request_bytes=int(get("server.max_request_bytes", 1_048_576)),
            max_response_bytes=int(get("server.max_response_bytes", 4_194_304)),
            request_timeout_seconds=int(get("server.request_timeout_seconds", 60)),
        )

    @property
    def auth_off_for_development(self) -> bool:
        """Whether the process explicitly opted into unauthenticated dev mode."""
        return os.environ.get(AUTH_OFF_ENV_VAR, "").strip().lower() == "off"

    def require_startable(self) -> None:
        """Refuse to start an unauthenticated server by accident.

        Phase A has no auth implementation, so a deployed server (auth
        required, no dev opt-out) cannot start at all. Local testing
        opts in per-process with ORION_SERVER_AUTH=off.
        """
        if self.auth_required and not self.auth_off_for_development:
            raise RuntimeError(
                "Server auth is required but no authentication is implemented "
                "in this build. Set ORION_SERVER_AUTH=off for local "
                "development testing only; never for a deployed server."
            )
