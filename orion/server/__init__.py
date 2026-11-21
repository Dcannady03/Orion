"""Headless Orion server: loopback-only typed HTTP API (Phase B).

Authenticated since Phase B: every endpoint needs a session cookie
(password login, scrypt hash, server-side sessions, CSRF tokens), and
remote access goes through Tailscale Serve. See docs/SERVER.md and the
§3.3 invariant in the server design doc.
"""
from orion.server.api import ApiRequest, ApiResponse, LocalApi, ROUTES
from orion.server.auth import Authenticator
from orion.server.config import ServerConfig
from orion.server.errors import ApiError
from orion.server.executor import DetachedExecutor
from orion.server.aiohttp_server import LocalApiServer
from orion.server.logging import LOGGER_NAME, setup_server_logging
from orion.server.reconcile import (
    ReconciliationReport,
    UncertainJobLaunch,
    UncertainMissionOperation,
    reconcile_startup,
)

__all__ = [
    "ApiError",
    "ApiRequest",
    "ApiResponse",
    "Authenticator",
    "DetachedExecutor",
    "LOGGER_NAME",
    "LocalApi",
    "LocalApiServer",
    "ROUTES",
    "ReconciliationReport",
    "ServerConfig",
    "UncertainJobLaunch",
    "UncertainMissionOperation",
    "reconcile_startup",
    "setup_server_logging",
]
