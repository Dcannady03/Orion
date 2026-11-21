"""Typed local API for the headless Orion server (Phase A).

Transport-independent: handlers take an ApiRequest and return an
ApiResponse, so the Phase B aiohttp transport can reuse the dispatch
table and every handler unchanged. Every handler calls a typed
service/application method directly; the ``cc`` handler's ``shlex``
parser is never invoked.

Response envelope: every body is an ApplicationResult dict
(``status`` / ``message`` / ``data`` / ``warnings`` / ``errors`` /
``next_actions``), serialized directly. Long-running mutations return
202 with the operation ID immediately; the durable record (reserved
mission audit, linked PLANNING job) exists before the 202 is returned,
and execution runs detached on the executor.

Phase B authenticates every endpoint (§3.3 invariant): LocalApi takes
an Authenticator, and handle() verifies the session cookie before
routing anything except POST /api/auth/login. With no authenticator
(dev mode, ORION_SERVER_AUTH=off) the gate is skipped and the auth
endpoints answer 503.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from orion.application.commands.ai_team_commands import (
    TeamApprovalRequest,
    team_run_lifecycle_data,
    team_task_lifecycle_data,
)
from orion.application.missions import (
    MissionAdvanceRequest,
    MissionCoordinationError,
    MissionCoordinator,
    MissionDispatchUncertainError,
    MissionResolveRequest,
    MissionStalePreviewError,
)
from orion.application.missions.handler import (
    mission_data,
    mission_next_actions,
)
from orion.application.missions.models import MissionStatus
from orion.application.observability.logs import LogSearchRequest
from orion.application.results import ApplicationResult
from orion.command_center.integrations import LaunchValidationError
from orion.command_center.models import JobPriority, JobTeamIntegration
from orion.command_center.views import job_view
from orion.server.auth import Authenticator, Session
from orion.server.errors import ApiError
from orion.server.executor import DetachedExecutor
from orion.server.reconcile import ReconciliationReport
from orion.services.codex_bridge import PlanSnapshot


@dataclass(frozen=True)
class ApiRequest:
    """One API call, transport-independent.

    Header keys are lowercased by the transport. ``session`` is set by
    LocalApi.handle after the auth gate verifies the session cookie
    (None for the login endpoint and in dev mode).
    """

    method: str
    path: str
    path_params: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)
    body: Mapping[str, Any] | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    remote_addr: str = ""
    session: Session | None = None


@dataclass(frozen=True)
class ApiResponse:
    """One API reply: HTTP status plus the ApplicationResult envelope."""

    status: int
    body: Mapping[str, Any]
    headers: Mapping[str, str] = field(default_factory=dict)


# Explicit dispatch table (no reflection): (method, path template, handler).
ROUTES: tuple[tuple[str, str, str], ...] = (
    ("GET", "/api/healthz", "healthz"),
    ("GET", "/api/snapshot", "snapshot"),
    ("GET", "/api/jobs", "list_jobs"),
    ("GET", "/api/jobs/{job_id}", "get_job"),
    ("GET", "/api/activity", "activity"),
    ("GET", "/api/doctor", "doctor"),
    ("GET", "/api/health", "health"),
    ("GET", "/api/logs", "logs"),
    ("GET", "/api/missions", "list_missions"),
    ("GET", "/api/missions/{mission_id}", "get_mission"),
    ("GET", "/api/missions/{mission_id}/history", "mission_history"),
    ("GET", "/api/team/tasks/{task_id}", "get_team_task"),
    ("GET", "/api/team/runs/{run_id}", "get_team_run"),
    ("POST", "/api/jobs", "create_job"),
    ("POST", "/api/jobs/{job_id}/launch/preview", "launch_preview"),
    ("POST", "/api/jobs/{job_id}/launch", "launch_job"),
    ("POST", "/api/jobs/{job_id}/sync", "sync_job"),
    ("POST", "/api/jobs/{job_id}/cancel", "cancel_job"),
    ("POST", "/api/missions/{mission_id}/next", "mission_next"),
    ("POST", "/api/missions/{mission_id}/advance", "mission_advance"),
    ("POST", "/api/missions/{mission_id}/resolve/preview", "mission_resolve_preview"),
    ("POST", "/api/missions/{mission_id}/resolve", "mission_resolve"),
    ("POST", "/api/team/tasks/{task_id}/approve", "approve_team_task"),
    ("POST", "/api/auth/login", "auth_login"),
    ("POST", "/api/auth/logout", "auth_logout"),
    ("GET", "/api/auth/me", "auth_me"),
    ("GET", "/api/auth/sessions", "auth_sessions"),
    ("POST", "/api/auth/sessions/{session_id}/revoke", "auth_revoke_session"),
    ("POST", "/api/auth/revoke-all", "auth_revoke_all"),
)

_MAX_LIST_LIMIT = 100
_MAX_HISTORY_LIMIT = 1000


def _segments(path: str) -> tuple[str, ...]:
    return tuple(part for part in path.split("/") if part)


def _match_route(
    method: str, path: str
) -> tuple[str, dict[str, str]]:
    """Match (method, path) against ROUTES; 404/405 as ApiError."""
    wanted = _segments(path)
    path_matches = False
    for route_method, template, handler_name in ROUTES:
        parts = _segments(template)
        if len(parts) != len(wanted):
            continue
        params: dict[str, str] = {}
        matched = True
        for template_part, actual in zip(parts, wanted):
            if template_part.startswith("{") and template_part.endswith("}"):
                params[template_part[1:-1]] = actual
            elif template_part != actual:
                matched = False
                break
        if not matched:
            continue
        path_matches = True
        if route_method == method:
            return handler_name, params
    if path_matches:
        raise ApiError(405, f"Method {method} is not allowed for {path}.",
                       code="method_not_allowed")
    raise ApiError.not_found(f"Unknown route: {method} {path}")


class LocalApi:
    """The typed local API, bound to one Orion runtime."""

    def __init__(
        self,
        runtime: Any,
        *,
        executor: DetachedExecutor,
        version: str,
        started_at: datetime | None = None,
        reconcile_report: ReconciliationReport | None = None,
        logger: logging.Logger | None = None,
        authenticator: Authenticator | None = None,
    ) -> None:
        self._runtime = runtime
        self._executor = executor
        self._version = str(version)
        self._started_at = started_at or datetime.now(timezone.utc)
        self._reconcile_report = reconcile_report or ReconciliationReport()
        self._logger = logger or logging.getLogger("orion.server.api")
        # None in dev mode (ORION_SERVER_AUTH=off): the gate is skipped.
        self._authenticator = authenticator

    @property
    def runtime(self) -> Any:
        """The Orion runtime this API is bound to."""
        return self._runtime

    @property
    def reconcile_report(self) -> ReconciliationReport:
        return self._reconcile_report

    @reconcile_report.setter
    def reconcile_report(self, report: ReconciliationReport) -> None:
        self._reconcile_report = report

    # -- dispatch -----------------------------------------------------

    def handle(self, request: ApiRequest) -> ApiResponse:
        """Route one request and map every failure to an ApiResponse.

        The auth gate runs before routing: with an authenticator, every
        request except POST /api/auth/login needs a valid session, and
        with no password set every request answers 503 setup_required.
        """
        try:
            session = None
            if self._authenticator is not None:
                session = self._authenticator.authenticate(request)
            handler_name, params = _match_route(request.method, request.path)
            handler = getattr(self, handler_name)
            bound = ApiRequest(
                method=request.method,
                path=request.path,
                path_params=params,
                query=request.query,
                body=request.body,
                headers=request.headers,
                remote_addr=request.remote_addr,
                session=session,
            )
            return handler(bound)
        except ApiError as exc:
            return ApiResponse(exc.status, exc.to_dict())
        except MissionStalePreviewError as exc:
            error = ApiError.conflict(str(exc) or "Mission preview is stale.")
            return ApiResponse(error.status, error.to_dict())
        except MissionDispatchUncertainError as exc:
            error = ApiError.conflict(
                str(exc) or "Mission dispatch outcome is uncertain."
            )
            return ApiResponse(error.status, error.to_dict())
        except MissionCoordinationError as exc:
            error = ApiError.unprocessable(str(exc) or "Mission cannot advance.")
            return ApiResponse(error.status, error.to_dict())
        except LaunchValidationError as exc:
            error = ApiError.unprocessable(
                str(exc) or "Job launch validation failed.",
                details={"preview": exc.preview.to_dict()},
            )
            return ApiResponse(error.status, error.to_dict())
        except FileNotFoundError as exc:
            error = ApiError.not_found(self._safe_message(exc, "Record not found."))
            return ApiResponse(error.status, error.to_dict())
        except PermissionError as exc:
            error = ApiError(
                403, self._safe_message(exc, "Operation is not permitted."),
                code="forbidden",
            )
            return ApiResponse(error.status, error.to_dict())
        except TimeoutError as exc:
            error = ApiError.timeout(self._safe_message(exc, "Operation timed out."))
            return ApiResponse(error.status, error.to_dict())
        except ValueError as exc:
            error = ApiError.bad_request(self._safe_message(exc, "Invalid request."))
            return ApiResponse(error.status, error.to_dict())
        except Exception as exc:  # noqa: BLE001 - the transport must never leak a traceback
            self._logger.exception("API handler failed: %s %s", request.method, request.path)
            error = ApiError.internal(
                f"Internal error handling {request.method} {request.path}."
            )
            return ApiResponse(error.status, error.to_dict())

    @staticmethod
    def _safe_message(exc: BaseException, fallback: str) -> str:
        text = " ".join(str(exc).split())[:300]
        return text or fallback

    # -- envelope helpers ----------------------------------------------

    @staticmethod
    def _ok(
        message: str,
        data: Mapping[str, Any],
        *,
        status: int = 200,
        warnings: tuple[str, ...] = (),
        next_actions: tuple[str, ...] = (),
        headers: Mapping[str, str] = (),
    ) -> ApiResponse:
        result = ApplicationResult.success(
            message,
            data=dict(data),
            warnings=warnings,
            next_actions=next_actions,
        )
        return ApiResponse(status, result.to_dict(), headers=dict(headers))

    @staticmethod
    def _from_result(
        result: ApplicationResult,
        *,
        failure_status: int,
    ) -> ApiResponse:
        if result.ok:
            return ApiResponse(200, result.to_dict())
        return ApiResponse(failure_status, result.to_dict())

    # -- request helpers ------------------------------------------------

    @staticmethod
    def _body(request: ApiRequest) -> dict[str, Any]:
        body = request.body
        if body is None:
            return {}
        if not isinstance(body, Mapping):
            raise ApiError.bad_request("Request body must be a JSON object.")
        return dict(body)

    @staticmethod
    def _limit(
        request: ApiRequest,
        *,
        default: int,
        maximum: int,
        label: str = "limit",
        name: str = "limit",
    ) -> int:
        raw = request.query.get(name)
        if raw is None or raw == "":
            return default
        try:
            value = int(raw, 10)
        except ValueError:
            raise ApiError.bad_request(f"{label} must be an integer.") from None
        if isinstance(value, bool) or not 1 <= value <= maximum:
            raise ApiError.bad_request(
                f"{label} must be between 1 and {maximum}."
            )
        return value

    @staticmethod
    def _page(
        items: list[dict[str, Any]],
        *,
        cursor: str,
        limit: int,
        id_key: str,
    ) -> tuple[list[dict[str, Any]], str]:
        """Simple cursor pagination over an already-ordered list."""
        start = 0
        if cursor:
            positions = [
                index for index, item in enumerate(items)
                if str(item.get(id_key, "")) == cursor
            ]
            if not positions:
                raise ApiError.bad_request(f"Unknown cursor: {cursor}")
            start = positions[0] + 1
        page = items[start:start + limit]
        next_cursor = ""
        if start + limit < len(items) and page:
            next_cursor = str(page[-1].get(id_key, ""))
        return page, next_cursor

    @staticmethod
    def _required_str(body: Mapping[str, Any], name: str) -> str:
        value = body.get(name)
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ApiError.bad_request(f"{name} is required.")
        return text

    @staticmethod
    def _optional_str(body: Mapping[str, Any], name: str, default: str = "") -> str:
        value = body.get(name)
        if value is None:
            return default
        return str(value).strip()

    @staticmethod
    def _required_true(body: Mapping[str, Any], name: str, label: str) -> None:
        if body.get(name) is not True:
            raise ApiError.bad_request(f"{label} requires explicit confirmation.")

    # -- runtime services ----------------------------------------------

    @property
    def _command_center(self) -> Any:
        return self._runtime.command_center

    @property
    def _integration(self) -> Any:
        return self._runtime.command_center_team

    @property
    def _missions(self) -> Any:
        return self._runtime.missions

    @property
    def _coordinator(self) -> Any:
        return self._runtime.mission_coordinator

    @property
    def _coordination_repository(self) -> Any:
        return self._runtime.mission_coordination_repository

    @property
    def _team(self) -> Any:
        return self._runtime.team

    @property
    def _team_application(self) -> Any:
        return self._runtime.team_application

    @property
    def _bridge(self) -> Any:
        return self._runtime.codex_bridge

    @property
    def _observability(self) -> Any:
        return self._runtime.observability_application

    # -- liveness --------------------------------------------------------

    def healthz(self, request: ApiRequest) -> ApiResponse:
        """Liveness for the supervisor: no service calls, always fast."""
        uptime = (datetime.now(timezone.utc) - self._started_at).total_seconds()
        return self._ok(
            "Orion server is running.",
            {
                "status": "ok",
                "version": self._version,
                "uptime_seconds": round(uptime, 1),
                "started_at": self._started_at.isoformat().replace("+00:00", "Z"),
                "in_flight_operations": list(self._executor.in_flight),
                "reconciliation": self._reconcile_report.to_dict(),
            },
        )

    # -- auth endpoints -------------------------------------------------

    def _require_authenticator(self) -> Authenticator:
        if self._authenticator is None:
            # Dev mode (ORION_SERVER_AUTH=off): the auth endpoints have
            # nothing to authenticate against.
            raise ApiError(
                503,
                "Server auth is disabled for development "
                "(ORION_SERVER_AUTH=off).",
                code="auth_disabled",
            )
        return self._authenticator

    def auth_login(self, request: ApiRequest) -> ApiResponse:
        result = self._require_authenticator().login(request)
        return self._ok(
            "Logged in.", result.data, status=result.status,
            headers=result.headers,
        )

    def auth_logout(self, request: ApiRequest) -> ApiResponse:
        result = self._require_authenticator().logout(request)
        return self._ok(
            "Logged out.", result.data, status=result.status,
            headers=result.headers,
        )

    def auth_me(self, request: ApiRequest) -> ApiResponse:
        result = self._require_authenticator().me(request)
        return self._ok(
            "Session info.", result.data, status=result.status,
            headers=result.headers,
        )

    def auth_sessions(self, request: ApiRequest) -> ApiResponse:
        result = self._require_authenticator().list_sessions(request)
        return self._ok(
            "Sessions.", result.data, status=result.status,
            headers=result.headers,
        )

    def auth_revoke_session(self, request: ApiRequest) -> ApiResponse:
        result = self._require_authenticator().revoke_session(request)
        return self._ok(
            "Session revoked.", result.data, status=result.status,
            headers=result.headers,
        )

    def auth_revoke_all(self, request: ApiRequest) -> ApiResponse:
        result = self._require_authenticator().revoke_all(request)
        return self._ok(
            "All sessions revoked.", result.data, status=result.status,
            headers=result.headers,
        )

    # -- reads: Command Center -------------------------------------------

    def snapshot(self, request: ApiRequest) -> ApiResponse:
        activity_limit = self._limit(
            request, default=20, maximum=_MAX_LIST_LIMIT,
            label="activity_limit", name="activity_limit",
        )
        completed_limit = self._limit(
            request, default=10, maximum=_MAX_LIST_LIMIT,
            label="completed_limit", name="completed_limit",
        )
        data = self._command_center.snapshot(
            activity_limit=activity_limit,
            completed_limit=completed_limit,
        )
        return self._ok("Command Center snapshot.", data)

    def list_jobs(self, request: ApiRequest) -> ApiResponse:
        limit = self._limit(request, default=20, maximum=_MAX_LIST_LIMIT)
        cursor = str(request.query.get("cursor", "")).strip()
        views = [
            job_view(self._command_center, job)
            for job in self._command_center.jobs()
        ]
        page, next_cursor = self._page(
            views, cursor=cursor, limit=limit, id_key="id"
        )
        return self._ok(
            "Command Center jobs.",
            {"jobs": page, "next_cursor": next_cursor, "count": len(page)},
        )

    def get_job(self, request: ApiRequest) -> ApiResponse:
        job_id = request.path_params["job_id"]
        job = self._command_center.job(job_id)
        return self._ok(
            f"Command Center job {job_id}.",
            job_view(self._command_center, job),
        )

    def activity(self, request: ApiRequest) -> ApiResponse:
        limit = self._limit(request, default=20, maximum=_MAX_LIST_LIMIT)
        cursor = str(request.query.get("cursor", "")).strip()
        events = [
            event.to_dict()
            for event in reversed(self._command_center.activity(1_000))
        ]
        page, next_cursor = self._page(
            events, cursor=cursor, limit=limit, id_key="id"
        )
        return self._ok(
            "Command Center activity.",
            {"events": page, "next_cursor": next_cursor, "count": len(page)},
        )

    def doctor(self, request: ApiRequest) -> ApiResponse:
        data = self._command_center.doctor()
        return self._ok("Command Center diagnostics.", data)

    # -- reads: observability --------------------------------------------

    def health(self, request: ApiRequest) -> ApiResponse:
        section = str(request.query.get("section", "")).strip() or None
        result = self._observability.health(section)
        return self._from_result(result, failure_status=400)

    def logs(self, request: ApiRequest) -> ApiResponse:
        query = request.query
        limit = self._limit(request, default=50, maximum=500)
        search = LogSearchRequest(
            severity=str(query.get("severity", "")).strip() or None,
            event_type=str(query.get("event_type", "")).strip() or None,
            search=str(query.get("search", "")).strip() or None,
            start_time=str(query.get("start_time", "")).strip() or None,
            end_time=str(query.get("end_time", "")).strip() or None,
            limit=limit,
            source=str(query.get("source", "all")).strip() or "all",
        )
        result = self._observability.logs(search)
        return self._from_result(result, failure_status=400)

    # -- reads: missions ---------------------------------------------------

    def list_missions(self, request: ApiRequest) -> ApiResponse:
        limit = self._limit(request, default=20, maximum=_MAX_LIST_LIMIT)
        cursor = str(request.query.get("cursor", "")).strip()
        status_raw = str(request.query.get("status", "")).strip()
        status: MissionStatus | None = None
        if status_raw:
            try:
                status = MissionStatus.parse(status_raw)
            except ValueError as exc:
                raise ApiError.bad_request(str(exc)) from None
        missions = self._missions.list(
            status=status,
            goal_id=str(request.query.get("goal_id", "")).strip(),
            proposal_id=str(request.query.get("proposal_id", "")).strip(),
            limit=500,
        )
        views = [mission_data(item) for item in missions]
        page, next_cursor = self._page(
            views, cursor=cursor, limit=limit, id_key="mission_id"
        )
        return self._ok(
            "Missions.",
            {"missions": page, "next_cursor": next_cursor, "count": len(page)},
        )

    def get_mission(self, request: ApiRequest) -> ApiResponse:
        mission_id = request.path_params["mission_id"]
        mission = self._missions.get(mission_id)
        data = mission_data(mission)
        data["next_actions"] = list(mission_next_actions(mission))
        audit = self._coordination_repository.get(mission_id)
        data["coordination_audit"] = (
            audit.to_dict() if audit is not None else None
        )
        return self._ok(f"Mission {mission_id}.", data)

    def mission_history(self, request: ApiRequest) -> ApiResponse:
        mission_id = request.path_params["mission_id"]
        limit = self._limit(
            request, default=100, maximum=_MAX_HISTORY_LIMIT
        )
        events, warnings = self._missions.history(mission_id, limit=limit)
        attempts = self._coordination_repository.read_attempt_history(mission_id)
        return self._ok(
            f"Mission {mission_id} history.",
            {
                "mission_id": mission_id,
                "events": [event.to_dict() for event in events],
                "attempt_history": [
                    entry.to_dict() for entry in attempts[-limit:]
                ],
            },
            warnings=tuple(warnings),
        )

    # -- reads: AI Team ------------------------------------------------------

    def get_team_task(self, request: ApiRequest) -> ApiResponse:
        task_id = request.path_params["task_id"]
        task = self._team.task(task_id)
        data = team_task_lifecycle_data(task)
        data["plan_sha256"] = self._plan_hash(task)
        return self._ok(f"AI Team task {task_id}.", data)

    @staticmethod
    def _plan_hash(task: Any) -> str:
        try:
            return str(PlanSnapshot.from_team_task(task).hash or "")
        except Exception:  # noqa: BLE001 - a task without a final plan has no hash
            return ""

    def get_team_run(self, request: ApiRequest) -> ApiResponse:
        run_id = request.path_params["run_id"]
        run = self._bridge.run(run_id)
        return self._ok(
            f"AI Team run {run_id}.",
            team_run_lifecycle_data(run),
        )

    # -- mutations: jobs ---------------------------------------------------

    def create_job(self, request: ApiRequest) -> ApiResponse:
        body = self._body(request)
        title = self._required_str(body, "title")
        goal = self._required_str(body, "goal")
        priority_raw = self._optional_str(body, "priority", "normal")
        try:
            priority = JobPriority.parse(priority_raw)
        except ValueError as exc:
            raise ApiError.bad_request(str(exc)) from None
        agents = body.get("agents", body.get("assigned_agents", ()))
        if isinstance(agents, str):
            agents = (agents,)
        if not isinstance(agents, (list, tuple)) or any(
            not isinstance(item, str) for item in agents
        ):
            raise ApiError.bad_request("agents must be a list of agent IDs.")
        try:
            job = self._command_center.create_job(
                title=title,
                goal=goal,
                priority=priority,
                department=self._optional_str(body, "department"),
                assigned_agents=tuple(agents),
                workspace_reference=self._optional_str(
                    body, "workspace", self._optional_str(body, "workspace_reference")
                ),
                created_by=self._optional_str(body, "created_by", "user"),
            )
        except FileNotFoundError as exc:
            # A body reference (department, agent) that does not exist is
            # a client error, not a missing path resource.
            raise ApiError.bad_request(
                self._safe_message(exc, "Referenced record not found.")
            ) from None
        return self._ok(
            f"Command Center job {job.job_id} created.",
            job_view(self._command_center, job),
            status=201,
        )

    def launch_preview(self, request: ApiRequest) -> ApiResponse:
        job_id = request.path_params["job_id"]
        self._command_center.job(job_id)  # 404 when the job does not exist
        body = self._body(request)
        preview = self._integration.preview_launch(
            job_id,
            workflow=self._optional_str(body, "workflow"),
            workspace=self._optional_str(body, "workspace"),
        )
        return self._ok(
            f"Launch preview for job {job_id}.",
            preview.to_dict(),
        )

    def launch_job(self, request: ApiRequest) -> ApiResponse:
        job_id = request.path_params["job_id"]
        self._command_center.job(job_id)  # 404 when the job does not exist
        body = self._body(request)
        # prepare_launch durably links the job (PLANNING) before this
        # returns; only then is the 202 safe to send.
        preparation = self._integration.prepare_launch(
            job_id,
            workflow=self._optional_str(body, "workflow"),
            workspace=self._optional_str(body, "workspace"),
        )
        self._executor.submit(
            f"launch:{preparation.team_task_id}",
            "job_launch",
            self._integration.execute_launch,
            preparation,
        )
        return self._ok(
            f"Job {job_id} launch accepted; planning runs detached.",
            {
                "job_id": job_id,
                "team_task_id": preparation.team_task_id,
                "operation_id": preparation.team_task_id,
                "status": "planning",
                "poll_url": f"/api/jobs/{job_id}",
                "preparation": preparation.to_dict(),
            },
            status=202,
        )

    def sync_job(self, request: ApiRequest) -> ApiResponse:
        job_id = request.path_params["job_id"]
        job = self._command_center.job(job_id)
        link = JobTeamIntegration.from_job(job).active_link
        if link is None:
            raise ApiError.conflict(
                f"Job {job_id} has no active AI Team link to sync."
            )
        result = self._integration.sync_job(job_id)
        return self._ok(
            f"Job {job_id} synced from its AI Team record.",
            result.to_dict(),
            warnings=tuple(result.warnings),
        )

    def cancel_job(self, request: ApiRequest) -> ApiResponse:
        job_id = request.path_params["job_id"]
        self._command_center.job(job_id)  # 404 when the job does not exist
        try:
            job = self._integration.cancel(job_id)
        except FileNotFoundError as exc:
            # The job exists (checked above); the missing record is the
            # linked Team task, a state conflict, not a 404.
            raise ApiError.conflict(
                self._safe_message(exc, "Linked Team record is missing.")
            ) from None
        except ValueError as exc:
            raise ApiError.conflict(
                self._safe_message(exc, "Job cannot be cancelled.")
            ) from None
        return self._ok(
            f"Job {job_id} cancelled.",
            job_view(self._command_center, job),
        )

    # -- mutations: missions -------------------------------------------------

    def mission_next(self, request: ApiRequest) -> ApiResponse:
        mission_id = request.path_params["mission_id"]
        body = self._body(request)
        preview = self._coordinator.preview(
            mission_id,
            decision=self._optional_str(body, "decision"),
            note=self._optional_str(body, "note"),
        )
        return self._ok(
            f"Mission {mission_id} next operation.",
            preview.to_dict(),
        )

    def mission_advance(self, request: ApiRequest) -> ApiResponse:
        mission_id = request.path_params["mission_id"]
        body = self._body(request)
        token = self._required_str(body, "advance_token")
        self._required_true(body, "confirmed", "Mission advance")
        advance_request = MissionAdvanceRequest(
            mission_id,
            token,
            confirmed=True,
            actor="user",
            decision=self._optional_str(body, "decision"),
            note=self._optional_str(body, "note"),
        )
        # dispatch() verifies the token and durably reserves the audit
        # before this returns; only then is a 202 safe to send.
        dispatch_record = self._coordinator.dispatch(advance_request)
        if MissionCoordinator.is_long_running(
            dispatch_record.audit.capability_id
        ):
            self._executor.submit(
                f"mission-advance:{dispatch_record.audit.attempt_id}",
                "mission_advance",
                self._coordinator.execute,
                dispatch_record,
            )
            return self._ok(
                f"Mission {mission_id} advance accepted; "
                f"{dispatch_record.audit.capability_id} runs detached.",
                {
                    "mission_id": mission_id,
                    "attempt_id": dispatch_record.audit.attempt_id,
                    "operation_id": dispatch_record.audit.attempt_id,
                    "capability_id": dispatch_record.audit.capability_id,
                    "subject_id": dispatch_record.audit.subject_id,
                    "poll_url": f"/api/missions/{mission_id}",
                    "dispatch": dispatch_record.to_dict(),
                },
                status=202,
            )
        result = self._coordinator.execute(dispatch_record)
        return self._ok(
            f"Mission {mission_id} advanced.",
            result.to_dict(),
            warnings=tuple(result.warnings),
        )

    def mission_resolve_preview(self, request: ApiRequest) -> ApiResponse:
        mission_id = request.path_params["mission_id"]
        preview = self._coordinator.resolve_preview(mission_id)
        return self._ok(
            f"Mission {mission_id} resolution preview.",
            preview.to_dict(),
        )

    def mission_resolve(self, request: ApiRequest) -> ApiResponse:
        mission_id = request.path_params["mission_id"]
        body = self._body(request)
        finding = self._required_str(body, "finding")
        self._required_true(body, "confirmed", "Mission resolution")
        resolve_request = MissionResolveRequest(
            mission_id,
            finding=finding,
            confirmed=True,
            actor="user",
        )
        result = self._coordinator.resolve(resolve_request)
        return self._ok(
            f"Mission {mission_id} resolved: {result.finding}.",
            result.to_dict(),
            warnings=tuple(result.warnings),
        )

    # -- mutations: AI Team ----------------------------------------------------

    def approve_team_task(self, request: ApiRequest) -> ApiResponse:
        task_id = request.path_params["task_id"]
        task = self._team.task(task_id)  # 404 when the task does not exist
        body = self._body(request)
        plan_sha256 = self._optional_str(body, "plan_sha256")
        if plan_sha256:
            actual = self._plan_hash(task)
            if not actual or actual.lower() != plan_sha256.strip().lower():
                raise ApiError.conflict(
                    "Plan SHA-256 does not match the persisted Team plan."
                )
        result = self._team_application.approve(TeamApprovalRequest(
            team_task_id=task_id,
            actor="user",
            plan_sha256=plan_sha256 or None,
        ))
        return self._from_result(result, failure_status=422)
