"""Tests for the process-lifetime singleton lock (orion/core/singleton.py).

The lock is held by the operating system for the process lifetime, so
the cross-process properties (second process refused, abrupt kill
releases the lock, simultaneous starts yield exactly one holder) are
verified with real subprocesses, not mocks.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from orion.core.singleton import SingletonLock, SingletonLockHeld

REPO_ROOT = Path(__file__).resolve().parents[1]

# Child program: acquire the lock on the path given as argv[1], print
# LOCKED plus its own PID, then sleep until killed. The child reports
# its own PID because on this server the venv python.exe is a launcher:
# Popen's proc.pid is the launcher, not the interpreter running this
# code (verified 2026-11-20: proc.pid != child os.getpid()).
_HOLD_CHILD = (
    "import sys, time, os\n"
    "from orion.core.singleton import SingletonLock\n"
    "lock = SingletonLock(sys.argv[1], mode='server')\n"
    "lock.acquire()\n"
    "print(f'LOCKED {os.getpid()}', flush=True)\n"
    "time.sleep(60)\n"
)

# Child program: wait for the go-file in argv[2] to appear, then try to
# acquire the lock on argv[1] and print ACQUIRED or HELD. Used by the
# simultaneous-start race test.
_RACE_CHILD = (
    "import os, sys, time\n"
    "from orion.core.singleton import SingletonLock, SingletonLockHeld\n"
    "for _ in range(200):\n"
    "    if os.path.exists(sys.argv[2]):\n"
    "        break\n"
    "    time.sleep(0.01)\n"
    "try:\n"
    "    SingletonLock(sys.argv[1], mode='server').acquire()\n"
    "    print('ACQUIRED', flush=True)\n"
    "except SingletonLockHeld:\n"
    "    print('HELD', flush=True)\n"
    "time.sleep(2)\n"
)


class SingletonLockTest(unittest.TestCase):
    def test_acquire_writes_diagnostic_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.lock"
            lock = SingletonLock(path, mode="server")
            self.assertFalse(lock.held)
            lock.acquire()
            self.assertTrue(lock.held)
            try:
                # The lock file itself stays empty; metadata lives in the
                # sibling file (on Windows a locked byte range cannot be
                # read by another handle).
                self.assertEqual(path.stat().st_size, 0)
                metadata = json.loads(
                    lock.metadata_path.read_text(encoding="utf-8")
                )
                self.assertEqual(metadata["pid"], os.getpid())
                self.assertEqual(metadata["mode"], "server")
                self.assertTrue(metadata["started_at"])
                self.assertEqual(
                    SingletonLock.holder_info(path)["pid"], os.getpid()
                )
            finally:
                lock.release()
            self.assertFalse(lock.held)

    def test_second_acquire_in_process_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.lock"
            first = SingletonLock(path, mode="server")
            first.acquire()
            try:
                second = SingletonLock(path, mode="cli")
                with self.assertRaises(SingletonLockHeld) as caught:
                    second.acquire()
                self.assertIn(str(os.getpid()), str(caught.exception))
                self.assertIn("server", str(caught.exception))
                self.assertFalse(second.held)
            finally:
                first.release()

    def test_release_then_reacquire(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.lock"
            first = SingletonLock(path, mode="server")
            first.acquire()
            first.release()
            first.release()  # Idempotent.
            second = SingletonLock(path, mode="cli")
            second.acquire()
            try:
                self.assertEqual(
                    SingletonLock.holder_info(path)["mode"], "cli"
                )
            finally:
                second.release()

    def test_context_manager_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.lock"
            with SingletonLock(path, mode="server") as lock:
                self.assertTrue(lock.held)
            self.assertFalse(lock.held)
            with SingletonLock(path, mode="server"):
                pass

    def test_holder_info_tolerates_missing_or_malformed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.lock"
            self.assertEqual(SingletonLock.holder_info(missing), {})
            partial = Path(tmp) / "partial.lock"
            Path(f"{partial}.json").write_text('{"pid": 12', encoding="utf-8")
            self.assertEqual(SingletonLock.holder_info(partial), {})
            empty = Path(tmp) / "empty.lock"
            Path(f"{empty}.json").write_text("", encoding="utf-8")
            self.assertEqual(SingletonLock.holder_info(empty), {})

    def test_double_acquire_same_instance_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = SingletonLock(Path(tmp) / "server.lock", mode="server")
            lock.acquire()
            try:
                with self.assertRaises(RuntimeError):
                    lock.acquire()
            finally:
                lock.release()


class SingletonLockSubprocessTest(unittest.TestCase):
    """Cross-process properties verified with real child processes."""

    def _wait_for_locked(self, proc, timeout=15.0):
        """Wait for the child's LOCKED line; return its PID or None."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                return None
            if line.startswith("LOCKED"):
                parts = line.strip().split()
                return int(parts[1]) if len(parts) > 1 else proc.pid
        return None

    def _kill_child(self, proc, child_pid):
        """Kill the launcher and, when it differs, the real child PID."""
        try:
            proc.kill()
        except OSError:
            pass
        if (
            child_pid is not None
            and child_pid != proc.pid
            and sys.platform == "win32"
        ):
            subprocess.run(
                ["taskkill", "/F", "/PID", str(child_pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        proc.stdout.close()
        proc.stderr.close()

    def test_second_process_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.lock"
            proc = subprocess.Popen(
                [sys.executable, "-c", _HOLD_CHILD, str(path)],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_pid = None
            try:
                child_pid = self._wait_for_locked(proc)
                self.assertIsNotNone(
                    child_pid, "child did not report acquiring the lock"
                )
                with self.assertRaises(SingletonLockHeld) as caught:
                    SingletonLock(path, mode="cli").acquire()
                self.assertIn(str(child_pid), str(caught.exception))
                self.assertIn("server", str(caught.exception))
            finally:
                self._kill_child(proc, child_pid)

    def test_abrupt_kill_releases_lock_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.lock"
            proc = subprocess.Popen(
                [sys.executable, "-c", _HOLD_CHILD, str(path)],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_pid = self._wait_for_locked(proc)
            self.assertIsNotNone(
                child_pid, "child did not report acquiring the lock"
            )
            # TerminateProcess-equivalent: no cleanup runs in the child.
            self._kill_child(proc, child_pid)
            # No manual cleanup: the next start acquires immediately.
            # Poll briefly: the OS releases the lock as the killed
            # process exits, which may lag the kill by a moment.
            acquired = None
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                try:
                    acquired = SingletonLock(path, mode="server").acquire()
                    break
                except SingletonLockHeld:
                    time.sleep(0.1)
            self.assertIsNotNone(
                acquired, "lock was still held after the holder was killed"
            )
            try:
                self.assertTrue(acquired.held)
            finally:
                acquired.release()

    def test_simultaneous_start_exactly_one_holder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "server.lock"
            go = root / "go"
            procs = [
                subprocess.Popen(
                    [sys.executable, "-c", _RACE_CHILD, str(path), str(go)],
                    cwd=REPO_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(2)
            ]
            try:
                # Let both children reach the barrier before releasing them.
                time.sleep(1.0)
                go.write_text("go", encoding="utf-8")
                outcomes = []
                for proc in procs:
                    out, _ = proc.communicate(timeout=20)
                    outcomes.append(out.strip().splitlines()[0])
            finally:
                for proc in procs:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=10)
                    proc.stdout.close()
                    proc.stderr.close()
            self.assertEqual(sorted(outcomes), ["ACQUIRED", "HELD"])


if __name__ == "__main__":
    unittest.main()
