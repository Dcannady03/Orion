"""HTTP transport for the local API (Phase A: stdlib only).

``ThreadingHTTPServer`` in a background daemon thread, loopback-only.
This module is a thin adapter: it parses the request, hands an
ApiRequest to LocalApi, and serializes the ApiResponse. Phase B swaps
this transport for aiohttp, keeping the dispatch table and typed
handlers unchanged.

Bounds (from ServerConfig): concurrent requests (semaphore → 429),
request bytes (→ 413), response bytes (→ 500, never a truncated JSON
body), per-request timeout (→ 504; the work continues detached and the
persistent record stays authoritative — a timeout never cancels a
mutation). Every request is logged as one structured JSON access line;
bodies and tokens are never logged.
"""
from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from orion.server.api import ApiRequest, ApiResponse, LocalApi
from orion.server.config import ServerConfig
from orion.server.errors import ApiError
from orion.server.executor import DetachedExecutor
from orion.server.reconcile import reconcile_startup


class _ApiHandler(BaseHTTPRequestHandler):
    """One handler instance per request; all state lives on the server."""

    protocol_version = "HTTP/1.1"
    server_version = "OrionServer/0.8"

    # -- plumbing ------------------------------------------------------

    def log_message(self, fmt: str, *args: object) -> None:
        # BaseHTTPRequestHandler logs to stderr by default; the server
        # writes structured access lines instead.
        pass

    @property
    def _server(self) -> "LocalApiServer":
        server = self.server
        assert isinstance(server, LocalApiServer)
        return server

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    # -- request handling ----------------------------------------------

    def _dispatch(self, method: str) -> None:
        server = self._server
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        status = 500
        try:
            response = self._handle_bounded(method, request_id)
            status = response.status
            self._send(response)
        except ApiError as exc:
            status = exc.status
            try:
                self._send(ApiResponse(exc.status, exc.to_dict()))
            except Exception:  # noqa: BLE001 - the client may have gone away
                pass
        except Exception:  # noqa: BLE001 - the socket must always get an answer
            server.logger.exception(
                "unhandled error serving request", extra={"request_id": request_id}
            )
            try:
                error = ApiError.internal("Internal server error.")
                self._send(ApiResponse(error.status, error.to_dict()))
                status = 500
            except Exception:  # noqa: BLE001 - the client may have gone away
                pass
        finally:
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            server.logger.info(
                json.dumps(
                    {
                        "type": "access",
                        "request_id": request_id,
                        "method": method,
                        "path": urlsplit(self.path).path,
                        "status": status,
                        "duration_ms": duration_ms,
                    },
                    sort_keys=True,
                )
            )

    def _handle_bounded(self, method: str, request_id: str) -> ApiResponse:
        server = self._server
        config = server.config
        acquired = server.semaphore.acquire(timeout=1.0)
        if not acquired:
            error = ApiError.too_many_requests(
                "Server is busy; retry the request."
            )
            return ApiResponse(error.status, error.to_dict())
        try:
            api_request = self._read_request(method)
            future = server.pool.submit(server.api.handle, api_request)
            try:
                return future.result(timeout=config.request_timeout_seconds)
            except FutureTimeout:
                server.logger.warning(
                    "request timed out after %ds; the work continues "
                    "detached and the persistent record stays authoritative",
                    config.request_timeout_seconds,
                    extra={"request_id": request_id},
                )
                error = ApiError.timeout(
                    "Request timed out; check the persistent record for "
                    "the outcome instead of retrying."
                )
                return ApiResponse(error.status, error.to_dict())
        finally:
            server.semaphore.release()

    def _read_request(self, method: str) -> ApiRequest:
        server = self._server
        config = server.config
        split = urlsplit(self.path)
        path = unquote(split.path)
        if len(path) > 2048:
            raise ApiError.bad_request("Request path is too long.")
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        body: dict | None = None
        if method in {"POST", "PUT", "PATCH"}:
            length = self._content_length()
            if length > config.max_request_bytes:
                raise ApiError.too_large(
                    f"Request body exceeds {config.max_request_bytes} bytes."
                )
            raw = self.rfile.read(length) if length else b""
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
            method=method,
            path=path,
            query=query,
            body=body,
        )

    def _content_length(self) -> int:
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0
        try:
            length = int(raw, 10)
        except ValueError as exc:
            raise ApiError.bad_request("Content-Length is not an integer.") from exc
        if length < 0:
            raise ApiError.bad_request("Content-Length must not be negative.")
        return length

    def _send(self, response: ApiResponse) -> None:
        server = self._server
        payload = json.dumps(
            dict(response.body),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode("utf-8")
        if len(payload) > server.config.max_response_bytes:
            error = ApiError.internal(
                "Response exceeds the configured size limit."
            )
            payload = json.dumps(
                error.to_dict(), sort_keys=True, ensure_ascii=False
            ).encode("utf-8")
            response = ApiResponse(error.status, error.to_dict())
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


class LocalApiServer(ThreadingHTTPServer):
    """Loopback-only HTTP server hosting the LocalApi."""

    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        if sys.platform == "win32":
            # Windows SO_REUSEADDR lets a second socket bind an actively
            # listening port (POSIX only allows TIME_WAIT reuse), so a
            # second server could steal the port. SO_EXCLUSIVEADDRUSE
            # restores the one-listener-per-port rule on Windows; the
            # listening socket does not enter TIME_WAIT on close, so a
            # quick restart still rebinds.
            self.socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
            )
            self.socket.bind(self.server_address)
            # Mirror TCPServer.server_bind: report the bound address
            # (with port 0 the OS picks the port; callers read it back).
            self.server_address = self.socket.getsockname()
            return
        super().server_bind()

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
        self.api = LocalApi(
            runtime,
            executor=self.executor,
            version=str(getattr(runtime, "version", "unknown")),
            logger=self.logger,
        )
        self.semaphore = threading.BoundedSemaphore(
            config.max_concurrent_requests
        )
        self.pool = ThreadPoolExecutor(
            max_workers=config.max_concurrent_requests,
            thread_name_prefix="orion-api",
        )
        self._started = False
        self._serve_thread: threading.Thread | None = None
        # Bind lazily in start() so bind failures can retry with backoff.
        super().__init__(
            (config.host, config.port), _ApiHandler, bind_and_activate=False
        )

    # -- lifecycle -------------------------------------------------------

    def start(
        self,
        *,
        bind_attempts: int = 5,
        bind_backoff_seconds: float = 0.5,
    ) -> None:
        """Bind (with retry), reconcile, and serve in a daemon thread.

        Raises RuntimeError when auth is required and the process did
        not explicitly opt into unauthenticated development mode.
        """
        self.config.require_startable()
        if self._started:
            return
        last_error: OSError | None = None
        for attempt in range(1, bind_attempts + 1):
            try:
                self.server_bind()
                self.server_activate()
                break
            except OSError as exc:
                last_error = exc
                self.logger.warning(
                    "bind attempt %d/%d failed on %s:%d: %s",
                    attempt, bind_attempts,
                    self.config.host, self.config.port, exc,
                )
                if attempt < bind_attempts:
                    time.sleep(bind_backoff_seconds)
        else:
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.server_close()
            raise RuntimeError(
                f"Could not bind the Orion server to {self.config.host}:"
                f"{self.config.port} after {bind_attempts} attempts: "
                f"{last_error}"
            )
        self.executor.start()
        self._reconcile()
        self._serve_thread = threading.Thread(
            target=self.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="orion-http-serve",
            daemon=True,
        )
        self._serve_thread.start()
        self._started = True
        if self.config.auth_off_for_development:
            self.logger.warning(
                "server running UNAUTHENTICATED on %s:%d "
                "(ORION_SERVER_AUTH=off; development only, never deploy)",
                self.config.host, self.config.port,
            )
        else:
            self.logger.info(
                "server listening on %s:%d",
                self.config.host, self.config.port,
            )

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
        self.shutdown()
        self.server_close()
        unfinished = self.executor.stop(
            drain_seconds=self.config.shutdown_drain_seconds
        )
        self.pool.shutdown(wait=False, cancel_futures=True)
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

    @property
    def started(self) -> bool:
        return self._started

    @property
    def address(self) -> tuple[str, int]:
        return (self.config.host, self.config.port)
