"""Tests for the detached executor (server Phase A).

Submit never blocks, a failed operation never kills a worker, drain is
bounded, and stop reports unfinished work instead of hanging.
"""
from __future__ import annotations

import threading
import time
import unittest

from orion.server.executor import DetachedExecutor


class DetachedExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = DetachedExecutor(max_workers=2)
        self.executor.start()
        self.addCleanup(self.executor.stop, drain_seconds=0.0)

    def test_submit_runs_and_drain_empties(self) -> None:
        done: list[str] = []
        self.executor.submit("op-1", "test", done.append, "op-1")
        self.assertIn("op-1", self.executor.in_flight)
        self.assertEqual(self.executor.drain(5.0), ())
        self.assertEqual(done, ["op-1"])
        self.assertEqual(self.executor.in_flight, ())

    def test_failed_operation_does_not_kill_worker(self) -> None:
        done: list[str] = []

        def boom() -> None:
            raise RuntimeError("detached boom")

        self.executor.submit("op-boom", "test", boom)
        self.executor.submit("op-ok", "test", done.append, "op-ok")
        self.assertEqual(self.executor.drain(5.0), ())
        self.assertEqual(done, ["op-ok"])

    def test_duplicate_operation_id_is_refused(self) -> None:
        release = threading.Event()
        self.executor.submit(
            "op-dup", "test", release.wait, 5.0
        )
        with self.assertRaises(ValueError):
            self.executor.submit("op-dup", "test", lambda: None)
        release.set()
        self.assertEqual(self.executor.drain(5.0), ())

    def test_drain_is_bounded_and_reports_unfinished(self) -> None:
        release = threading.Event()
        self.executor.submit(
            "op-slow", "test", release.wait, 30.0
        )
        started = time.monotonic()
        unfinished = self.executor.drain(0.2)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(unfinished, ("op-slow",))
        release.set()
        self.assertEqual(self.executor.drain(5.0), ())

    def test_stop_reports_unfinished_without_hanging(self) -> None:
        release = threading.Event()
        self.executor.submit("op-stuck", "test", release.wait, 30.0)
        started = time.monotonic()
        unfinished = self.executor.stop(drain_seconds=0.1)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(unfinished, ("op-stuck",))
        release.set()

    def test_submit_after_stop_is_refused(self) -> None:
        self.executor.stop(drain_seconds=0.0)
        with self.assertRaises(RuntimeError):
            self.executor.submit("op-late", "test", lambda: None)


if __name__ == "__main__":
    unittest.main()
