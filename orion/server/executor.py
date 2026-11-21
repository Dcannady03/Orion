"""Detached execution for accepted long-running operations.

A mutation that starts long work (job-launch planning, a mission
advance dispatching a Team operation) returns 202 after its durable
record exists; the work itself runs here, on daemon worker threads
pulling from a queue. Submit never blocks and never fails once the
durable record exists — a client holding a run/operation ID implies a
durable record, and the executor must not be a second failure mode.

Workers are daemon threads: if the process exits mid-operation, the
durable record (reserved mission audit, linked PLANNING job) carries
the uncertainty to restart reconciliation, which never auto-replays.
Shutdown drains with a bound: workers finish what they can within the
drain window, then the process exits anyway and the records speak.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class _DetachedOperation:
    operation_id: str
    kind: str
    func: Callable[..., Any]
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


class DetachedExecutor:
    """Queue-backed daemon workers for detached long-running work."""

    def __init__(
        self,
        *,
        max_workers: int = 4,
        logger: logging.Logger | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("Detached executor needs at least one worker.")
        self._max_workers = max_workers
        self._logger = logger or logging.getLogger("orion.server.executor")
        self._queue: queue.Queue[_DetachedOperation | None] = queue.Queue()
        self._lock = threading.Lock()
        self._in_flight: dict[str, _DetachedOperation] = {}
        self._workers: list[threading.Thread] = []
        self._started = False
        self._stopped = False

    def start(self) -> None:
        """Start the worker threads (idempotent)."""
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stopped = False
        for index in range(self._max_workers):
            worker = threading.Thread(
                target=self._worker_loop,
                name=f"orion-detached-{index}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)

    def submit(
        self,
        operation_id: str,
        kind: str,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Queue one operation for detached execution.

        Never blocks: the queue is unbounded because the durable record
        already exists by the time submit is called, and refusing an
        accepted operation would strand it.
        """
        operation_id = str(operation_id).strip()
        if not operation_id:
            raise ValueError("Detached operation requires an ID.")
        operation = _DetachedOperation(
            operation_id=operation_id,
            kind=str(kind),
            func=func,
            args=args,
            kwargs=kwargs,
        )
        with self._lock:
            if self._stopped:
                raise RuntimeError("Detached executor is stopped.")
            if operation_id in self._in_flight:
                raise ValueError(
                    f"Detached operation already in flight: {operation_id}"
                )
            self._in_flight[operation_id] = operation
        self._queue.put(operation)
        self._logger.info(
            "detached operation queued",
            extra={"operation_id": operation_id, "kind": kind},
        )

    @property
    def in_flight(self) -> tuple[str, ...]:
        """IDs of operations queued or running right now."""
        with self._lock:
            return tuple(self._in_flight)

    def drain(self, timeout: float) -> tuple[str, ...]:
        """Wait up to ``timeout`` seconds for in-flight work to finish.

        Returns the IDs still unfinished when the window closes. The
        caller exits anyway; the durable records carry the uncertainty.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            with self._lock:
                if not self._in_flight:
                    return ()
            time.sleep(0.05)
        with self._lock:
            return tuple(self._in_flight)

    def stop(self, *, drain_seconds: float = 0.0) -> tuple[str, ...]:
        """Drain briefly, then stop accepting work and release workers.

        Workers are daemon threads, so stop never blocks on a stuck
        operation: after the drain window the process may exit.
        """
        unfinished = self.drain(drain_seconds)
        with self._lock:
            self._stopped = True
        for _ in self._workers:
            self._queue.put(None)
        return unfinished

    def _worker_loop(self) -> None:
        while True:
            try:
                operation = self._queue.get(timeout=0.5)
            except queue.Empty:
                with self._lock:
                    if self._stopped:
                        return
                continue
            if operation is None:
                return
            try:
                operation.func(*operation.args, **operation.kwargs)
                self._logger.info(
                    "detached operation completed",
                    extra={
                        "operation_id": operation.operation_id,
                        "kind": operation.kind,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - a failed detached op must not kill the worker
                # The durable record already carries the outcome (the
                # coordinator marks the audit uncertain/failed; the
                # launch path syncs or fails the job). Log and move on.
                self._logger.warning(
                    "detached operation failed: %s: %s",
                    type(exc).__name__,
                    exc,
                    extra={
                        "operation_id": operation.operation_id,
                        "kind": operation.kind,
                    },
                )
            finally:
                with self._lock:
                    self._in_flight.pop(operation.operation_id, None)
                self._queue.task_done()
