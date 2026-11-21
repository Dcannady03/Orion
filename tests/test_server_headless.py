"""Headless-mode and entry-point tests (server Phase B).

main() takes the singleton lock in both modes, refuses a second
process, and passes --server through; Orion.start(server=True) serves
the API with no console render and no REPL.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from orion.core.orion import Orion
from orion.core.paths import OrionPaths
from orion.core.singleton import SingletonLock
from orion.main import build_parser, main
from tests.test_mission_coordinator import MissionCoordinatorFixture
from tests.test_server_api import ApiFixture


class FakeServerConfigManager:
    def __init__(self, port: int = 0) -> None:
        self._values = {
            "server.host": "127.0.0.1",
            "server.port": port,
            "server.auth_required": True,
            "server.shutdown_drain_seconds": 5,
            "server.max_concurrent_requests": 4,
            "server.max_request_bytes": 1_048_576,
            "server.max_response_bytes": 4_194_304,
            "server.request_timeout_seconds": 30,
        }

    def get(self, key, default=None):
        return self._values.get(key, default)


class MainEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self._env = patch.dict(
            os.environ, {"ORION_USER_DATA": self._temp.name}
        )
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_parser_has_server_flag(self) -> None:
        args = build_parser().parse_args(["--server"])
        self.assertTrue(args.server)
        self.assertFalse(args.discord)
        self.assertFalse(args.first_contact)

    def test_second_process_refuses_with_exit_code_2(self) -> None:
        lock = SingletonLock(
            OrionPaths().server_lock, mode="cli"
        ).acquire()
        self.addCleanup(lock.release)
        with (
            patch("orion.main.FirstContact"),
            patch("orion.main.Orion"),
        ):
            self.assertEqual(main(["--server"]), 2)
            self.assertEqual(main([]), 2)

    def test_server_flag_reaches_orion_start_and_lock_releases(self) -> None:
        fake_onboarding = Mock()
        fake_onboarding.is_required = False
        fake_orion = Mock()
        fake_orion.config_manager.get.return_value = False
        with (
            patch("orion.main.FirstContact", return_value=fake_onboarding),
            patch("orion.main.Orion", return_value=fake_orion),
        ):
            self.assertEqual(main(["--server"]), 0)
        fake_orion.start.assert_called_once_with(discord=False, server=True)
        # The lock was released when main() returned.
        lock = SingletonLock(OrionPaths().server_lock, mode="cli")
        lock.acquire()
        lock.release()

    def test_cli_mode_passes_server_false(self) -> None:
        fake_onboarding = Mock()
        fake_onboarding.is_required = False
        fake_orion = Mock()
        fake_orion.config_manager.get.return_value = False
        with (
            patch("orion.main.FirstContact", return_value=fake_onboarding),
            patch("orion.main.Orion", return_value=fake_orion),
        ):
            self.assertEqual(main([]), 0)
        fake_orion.start.assert_called_once_with(discord=False, server=False)

    def test_server_and_first_contact_together_is_an_error(self) -> None:
        with (
            patch("orion.main.FirstContact") as first_contact,
            patch("orion.main.Orion") as orion_cls,
        ):
            self.assertEqual(main(["--server", "--first-contact"]), 1)
        first_contact.assert_not_called()
        orion_cls.assert_not_called()

    def test_server_mode_requires_completed_setup(self) -> None:
        fake_onboarding = Mock()
        fake_onboarding.is_required = True
        with (
            patch("orion.main.FirstContact", return_value=fake_onboarding),
            patch("orion.main.Orion") as orion_cls,
        ):
            self.assertEqual(main(["--server"]), 1)
        orion_cls.assert_not_called()

    def test_server_start_failure_returns_1(self) -> None:
        fake_onboarding = Mock()
        fake_onboarding.is_required = False
        fake_orion = Mock()
        fake_orion.config_manager.get.return_value = False
        fake_orion.start.side_effect = RuntimeError("bind failed")
        with (
            patch("orion.main.FirstContact", return_value=fake_onboarding),
            patch("orion.main.Orion", return_value=fake_orion),
        ):
            self.assertEqual(main(["--server"]), 1)


class HeadlessServerTests(MissionCoordinatorFixture, ApiFixture):
    def setUp(self) -> None:
        super().setUp()
        self.build_api()
        self._user_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._user_temp.cleanup)
        self.runtime.paths = OrionPaths(user_root=self._user_temp.name)
        self.runtime.config_manager = FakeServerConfigManager(port=0)
        self._env = patch.dict(os.environ, {"ORION_SERVER_AUTH": "off"})
        self._env.start()
        self.addCleanup(self._env.stop)
        self._thread: threading.Thread | None = None
        self._thread_error: list[BaseException] = []

    def _run_server(self) -> None:
        try:
            Orion.start_server_interface(self.runtime)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            self._thread_error.append(exc)

    def tearDown(self) -> None:
        server = getattr(self.runtime, "server_interface", None)
        if server is not None and server.started:
            server.stop()
        if self._thread is not None:
            self._thread.join(timeout=10)
        # Release the log file handle before the temp dir is removed
        # (Windows cannot delete an open file).
        from orion.server.logging import close_server_logging

        close_server_logging()
        super().tearDown()

    def _wait_for_server(self, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            server = getattr(self.runtime, "server_interface", None)
            if server is not None and server.started:
                return server
            if self._thread_error:
                raise AssertionError(
                    f"server thread failed: {self._thread_error[0]!r}"
                )
            time.sleep(0.05)
        raise AssertionError("server did not start in time")

    def test_headless_server_serves_api_without_console_or_repl(self) -> None:
        # The fake runtime has no console or router attributes: any
        # console render or REPL access would fail loudly in the thread.
        self.assertFalse(hasattr(self.runtime, "console"))
        self.assertFalse(hasattr(self.runtime, "router"))
        self._thread = threading.Thread(
            target=self._run_server, daemon=True
        )
        self._thread.start()
        server = self._wait_for_server()
        port = server.server_address[1]
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/healthz", timeout=5
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["data"]["status"], "ok")
        self.assertFalse(self._thread_error)
        # The server log was created under the temp user root.
        log_path = Path(self._user_temp.name) / "logs" / "server.log"
        self.assertTrue(log_path.is_file())
        server.stop()
        self._thread.join(timeout=10)
        self.assertFalse(self._thread.is_alive())
        self.assertFalse(server.started)

    def test_server_starts_in_setup_mode_with_auth_required_and_no_opt_out(
        self,
    ) -> None:
        # Phase B implements auth: auth required + no opt-out is the
        # normal deployed configuration. With no password set yet the
        # server starts in setup mode (503 everywhere) instead of
        # refusing.
        import urllib.error

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORION_SERVER_AUTH", None)
            self._thread = threading.Thread(
                target=self._run_server, daemon=True
            )
            self._thread.start()
            server = self._wait_for_server()
            assert server.authenticator is not None
            self.assertTrue(server.authenticator.setup_mode)
            port = server.server_address[1]
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/healthz", timeout=5
                ) as response:
                    self.fail(f"expected 503, got {response.status}")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 503)
                payload = json.loads(exc.read().decode("utf-8"))
                self.assertEqual(
                    payload["data"]["error_code"], "setup_required"
                )
            self.assertFalse(self._thread_error)
            server.stop()
            self._thread.join(timeout=10)
        self.assertFalse(self._thread.is_alive())
        self.assertFalse(server.started)

    def test_server_refuses_to_start_with_auth_disabled_and_no_opt_out(
        self,
    ) -> None:
        import signal

        self.runtime.config_manager._values["server.auth_required"] = False
        previous_int = signal.getsignal(signal.SIGINT)
        previous_term = signal.getsignal(signal.SIGTERM)
        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ORION_SERVER_AUTH", None)
                with self.assertRaises(RuntimeError) as caught:
                    Orion.start_server_interface(self.runtime)
            self.assertIn("ORION_SERVER_AUTH=off", str(caught.exception))
            # The refused start installed no signal handlers.
            self.assertIs(signal.getsignal(signal.SIGINT), previous_int)
            self.assertIs(signal.getsignal(signal.SIGTERM), previous_term)
        finally:
            signal.signal(signal.SIGINT, previous_int)
            signal.signal(signal.SIGTERM, previous_term)


class LazyConsoleTests(unittest.TestCase):
    """Orion() must not need a console: prompt_toolkit raises
    NoConsoleScreenBufferError on Windows when the process has no console
    attached, which would kill a headless --server start from SSH or a
    scheduled task before it serves anything."""

    def test_console_is_built_lazily_not_in_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "config.yaml").write_text('version: "0.8.0"\n')
            Path(tmp, "profile.yaml").write_text(
                "preferred_name: LazyTest\nname: Lazy Test\n"
            )
            with patch.dict(os.environ, {"ORION_USER_DATA": tmp}), patch(
                "orion.core.orion.Console"
            ) as fake_console:
                runtime = Orion()
                fake_console.assert_not_called()
                self.assertIsNone(runtime._console)
                first = runtime.console
                fake_console.assert_called_once_with(runtime)
                self.assertIs(runtime.console, first)
                fake_console.assert_called_once_with(runtime)


if __name__ == "__main__":
    unittest.main()
