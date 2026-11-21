"""Headless Orion server: loopback-only typed HTTP API (Phase A).

Development-only prototype: unauthenticated, loopback-only. See
docs/SERVER.md and the §3.3 invariant in the server design doc.
"""
from orion.server.api import ApiRequest, ApiResponse, LocalApi, ROUTES
from orion.server.config import ServerConfig
from orion.server.errors import ApiError
from orion.server.executor import DetachedExecutor
from orion.server.http_server import LocalApiServer
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
