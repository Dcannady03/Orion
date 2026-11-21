"""HTTP transport for the local API (Phase B: aiohttp).

aiohttp in a background daemon thread with its own asyncio event loop
(the Discord precedent: own loop, blocking work via ``asyncio.to_thread``).
This module is a thin adapter: it parses the request, hands an
ApiRequest to LocalApi, and serializes the ApiResponse. The dispatch
table and typed handlers are unchanged from Phase A; only the transport
moved — the stdlib docs advise against ``http.server`` for production,
and a maintained HTTP implementation is the right call for a
continuously-running authenticated control interface.

Bounds (from ServerConfig): concurrent requests (asyncio semaphore →
429), request bytes (client_max_size → 413), response bytes (→ 500,
never a truncated JSON body), per-request timeout (→ 504; the work
continues detached and the persistent record stays authoritative — a
timeout never cancels a mutation). Every request is logged as one
structured JSON access line; bodies and tokens are never logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from typing import Any

from aiohttp import web

from orion.server.api import ApiRequest, ApiResponse, LocalApi
from orion.server.auth import Authenticator
from orion.server.config import ServerConfig
from orion.server.errors import ApiError
from orion.server.executor import DetachedExecutor
from orion.server.reconcile import reconcile_startup


class LocalApiServer:
    """Loopback-only HTTP server hosting the LocalApi, on aiohttp."""

    def __init__(
        self,
        runtime: Any,
        config: ServerConfig,
        *,
        executor: DetachedExecutor | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("orion.server.http")
        self.executor = executor or DetachedExecutor(logger=self.logger)
        authenticator = None
        if config.auth_required and not config.auth_off_for_development:
            paths = getattr(runtime, "paths", None)
            if paths is None:
                raise RuntimeError(
                    "Server auth requires the runtime paths; "
                    "the runtime has no .paths attribute."
                )
            authenticator = Authenticator(paths, config, logger=self.logger)
        self.authenticator = authenticator
        self.api = LocalApi(
            runtime,
            executor=self.executor,
            version=str(getattr(runtime, "version", "unknown")),
            logger=self.logger,
            authenticator=authenticator,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._bound_address: tuple[str, int] | None = None
        self._started = False

    # -- lifecycle -------------------------------------------------------

    def start(
        self,
        *,
        bind_attempts: int = 5,
        bind_backoff_seconds: float = 0.5,
    ) -> None:
        """Bind (with retry), reconcile, and serve on a background loop.

        Raises RuntimeError when the server cannot start (auth is
        required without the development opt-out, or the bind fails).
        """
        self.config.require_startable()
        if self._started:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="orion-http-serve",
            daemon=True,
        )
        self._thread.start()
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._start_site(bind_attempts, bind_backoff_seconds),
                self._loop,
            )
            future.result(
                timeout=bind_attempts * (bind_backoff_seconds + 2.0) + 10.0
            )
        except Exception:
            self._teardown_loop()
            raise
        self.executor.start()
        self._reconcile()
        self._started = True
        if self.config.auth_off_for_development:
            self.logger.warning(
                "server running UNAUTHENTICATED on %s:%d "
                "(ORION_SERVER_AUTH=off; development only, never deploy)",
                self.config.host, self.config.port,
            )
        elif self.authenticator is not None and self.authenticator.setup_mode:
            self.logger.warning(
                "server in SETUP MODE on %s:%d: no server password set; "
                "every endpoint returns 503 until "
                "`python -m orion.main --server-setup` is run on the server",
                self.config.host, self.config.port,
            )
        else:
            self.logger.info(
                "server listening on %s:%d",
                self.config.host, self.config.port,
            )
        if self.authenticator is not None and not self.config.secure_cookies:
            self.logger.warning(
                "server.secure_cookies is false: session cookies will be "
                "sent over plain HTTP (development only, never deploy)"
            )

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _start_site(
        self,
        bind_attempts: int,
        bind_backoff_seconds: float,
    ) -> None:
        self._app = web.Application(
            client_max_size=self.config.max_request_bytes
        )
        # One handler for every path and method: LocalApi's dispatch
        # table owns routing (and its 404/405 envelope), so unknown
        # paths get the JSON envelope, not aiohttp's default error page.
        self._app.router.add_route("*", "/", self._handle_request)
        self._app.router.add_route("*", "/{tail:.*}", self._handle_request)
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        self._semaphore = asyncio.Semaphore(
            self.config.max_concurrent_requests
        )
        last_error: OSError | None = None
        for attempt in range(1, bind_attempts + 1):
            try:
                # reuse_address=False: on Windows, SO_REUSEADDR lets a
                # second socket bind an actively listening port, so a
                # second server could steal the port. Without it, a
                # live port refuses a second bind. The listening socket
                # does not enter TIME_WAIT on close, so a quick restart
                # still rebinds.
                self._site = web.TCPSite(
                    self._runner,
                    self.config.host,
                    self.config.port,
                    reuse_address=False,
                )
                await self._site.start()
                break
            except OSError as exc:
                last_error = exc
                self.logger.warning(
                    "bind attempt %d/%d failed on %s:%d: %s",
                    attempt, bind_attempts,
                    self.config.host, self.config.port, exc,
                )
                if attempt < bind_attempts:
                    await asyncio.sleep(bind_backoff_seconds)
        else:
            await self._runner.cleanup()
            raise RuntimeError(
                f"Could not bind the Orion server to {self.config.host}:"
                f"{self.config.port} after {bind_attempts} attempts: "
                f"{last_error}"
            )
        addresses = self._runner.addresses
        if addresses:
            self._bound_address = (str(addresses[0][0]), int(addresses[0][1]))
        else:  # pragma: no cover - the site always reports an address
            self._bound_address = (self.config.host, self.config.port)

    def _reconcile(self) -> None:
        runtime = self.api.runtime
        report = reconcile_startup(
            missions=runtime.missions,
            coordination_repository=runtime.mission_coordination_repository,
            command_center=runtime.command_center,
            team=runtime.team,
            logger=self.logger,
        )
        self.api.reconcile_report = report

    def stop(self) -> tuple[str, ...]:
        """Stop serving, drain detached work within the bound, release."""
        if not self._started:
            return ()
        if self._loop is not None and self._runner is not None:
            future = asyncio.run_coroutine_threadsafe(
                self._runner.cleanup(), self._loop
            )
            try:
                future.result(timeout=10.0)
            except Exception as exc:  # noqa: BLE001 - stop must not hang
                self.logger.warning("HTTP runner cleanup failed: %s", exc)
        self._teardown_loop()
        unfinished = self.executor.stop(
            drain_seconds=self.config.shutdown_drain_seconds
        )
        self._started = False
        if unfinished:
            self.logger.warning(
                "shutdown left %d detached operation(s) unfinished; "
                "their durable records carry the uncertainty",
                len(unfinished),
            )
        else:
            self.logger.info("server stopped cleanly")
        return unfinished

    def _teardown_loop(self) -> None:
        loop, thread = self._loop, self._thread
        self._loop = None
        self._thread = None
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        try:
            loop.close()
        except Exception:  # noqa: BLE001 - teardown must not raise
            pass

    @property
    def started(self) -> bool:
        return self._started

    @property
    def address(self) -> tuple[str, int]:
        return (self.config.host, self.config.port)

    @property
    def server_address(self) -> tuple[str, int]:
        """The address the server actually bound (port 0 reads back)."""
        return self._bound_address or (self.config.host, self.config.port)

    # -- request handling ----------------------------------------------

    async def _handle_request(self, request: web.Request) -> web.Response:
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        status = 500
        try:
            response = await self._handle_bounded(request, request_id)
            status = response.status
            return self._to_web_response(response)
        except ApiError as exc:
            status = exc.status
            return self._to_web_response(
                ApiResponse(exc.status, exc.to_dict())
            )
        except Exception:  # noqa: BLE001 - the socket must always get an answer
            self.logger.exception(
                "unhandled error serving request", extra={"request_id": request_id}
            )
            error = ApiError.internal("Internal server error.")
            status = 500
            return self._to_web_response(
                ApiResponse(error.status, error.to_dict())
            )
        finally:
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            self.logger.info(
                json.dumps(
                    {
                        "type": "access",
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.path,
                        "status": status,
                        "duration_ms": duration_ms,
                    },
                    sort_keys=True,
                )
            )

    async def _handle_bounded(
        self, request: web.Request, request_id: str
    ) -> ApiResponse:
        assert self._semaphore is not None
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=1.0)
        except asyncio.TimeoutError:
            error = ApiError.too_many_requests(
                "Server is busy; retry the request."
            )
            return ApiResponse(error.status, error.to_dict())
        try:
            api_request = await self._read_request(request)
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self.api.handle, api_request),
                    timeout=self.config.request_timeout_seconds,
                )
            except asyncio.TimeoutError:
                self.logger.warning(
                    "request timed out after %ds; the work continues "
                    "detached and the persistent record stays authoritative",
                    self.config.request_timeout_seconds,
                    extra={"request_id": request_id},
                )
                error = ApiError.timeout(
                    "Request timed out; check the persistent record for "
                    "the outcome instead of retrying."
                )
                return ApiResponse(error.status, error.to_dict())
        finally:
            self._semaphore.release()

    async def _read_request(self, request: web.Request) -> ApiRequest:
        path = request.path
        if len(path) > 2048:
            raise ApiError.bad_request("Request path is too long.")
        query = dict(request.query)
        body: dict | None = None
        if request.method in {"POST", "PUT", "PATCH"}:
            try:
                raw = await request.read()
            except web.HTTPRequestEntityTooLarge:
                raise ApiError.too_large(
                    f"Request body exceeds {self.config.max_request_bytes} bytes."
                ) from None
            if len(raw) > self.config.max_request_bytes:
                raise ApiError.too_large(
                    f"Request body exceeds {self.config.max_request_bytes} bytes."
                )
            if raw:
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ApiError.bad_request(
                        f"Request body is not valid JSON: {exc}"
                    ) from None
                if not isinstance(parsed, dict):
                    raise ApiError.bad_request(
                        "Request body must be a JSON object."
                    )
                body = parsed
        return ApiRequest(
            method=request.method,
            path=path,
            query=query,
            body=body,
            headers={
                key.lower(): value for key, value in request.headers.items()
            },
            remote_addr=request.remote or "",
        )

    def _to_web_response(self, response: ApiResponse) -> web.Response:
        payload = json.dumps(
            dict(response.body),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode("utf-8")
        if len(payload) > self.config.max_response_bytes:
            error = ApiError.internal(
                "Response exceeds the configured size limit."
            )
            payload = json.dumps(
                error.to_dict(), sort_keys=True, ensure_ascii=False
            ).encode("utf-8")
            response = ApiResponse(error.status, error.to_dict())
        return web.Response(
            body=payload,
            status=response.status,
            content_type="application/json",
            charset="utf-8",
            headers=dict(response.headers),
        )
