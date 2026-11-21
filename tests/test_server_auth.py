"""Auth tests for the headless server (Phase B).

Credential store, sessions, CSRF binding, lockout, and the local-only
setup command. All tests use temp user roots with throwaway passwords;
nothing here touches a real ~/.orion.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orion.application.observability.recovery import (
    RECOVERY_KINDS,
    RecoveryHistoryStore,
)
from orion.core.paths import OrionPaths
from orion.server.auth import (
    MAX_SESSIONS,
    MIN_PASSWORD_LENGTH,
    SESSION_LIFETIME,
    CredentialStore,
    LockoutTracker,
    SessionStore,
    csrf_token_for,
    verify_csrf_token,
)
from orion.server.setup import run_server_setup

PASSWORD = "correct-horse-battery-staple"
OTHER_PASSWORD = "another-long-test-password"


class FakeClock:
    """A mutable clock for expiry/lockout tests."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class AuthFixture(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.paths = OrionPaths(user_root=self._temp.name)
        self.clock = FakeClock()


class CredentialStoreTests(AuthFixture):
    def test_set_and_verify_round_trip(self) -> None:
        store = CredentialStore(self.paths)
        self.assertFalse(store.exists)
        changed = store.set_password(PASSWORD)
        self.assertFalse(changed)
        self.assertTrue(store.exists)
        self.assertTrue(store.verify_password(PASSWORD))
        self.assertFalse(store.verify_password(OTHER_PASSWORD))
        self.assertFalse(store.verify_password(""))

    def test_verify_without_a_password_is_false(self) -> None:
        self.assertFalse(CredentialStore(self.paths).verify_password(PASSWORD))

    def test_short_password_is_refused(self) -> None:
        store = CredentialStore(self.paths)
        with self.assertRaises(ValueError):
            store.set_password("short")
        self.assertFalse(store.exists)

    def test_password_is_never_stored_in_plaintext(self) -> None:
        store = CredentialStore(self.paths)
        store.set_password(PASSWORD)
        raw = self.paths.server_auth.read_text(encoding="utf-8")
        self.assertNotIn(PASSWORD, raw)
        data = json.loads(raw)
        self.assertIn("scrypt", data)
        self.assertIn("salt", data["scrypt"])
        self.assertIn("hash", data["scrypt"])
        self.assertNotEqual(data["scrypt"]["hash"], PASSWORD)

    def test_csrf_secret_exists_and_rotates(self) -> None:
        store = CredentialStore(self.paths)
        self.assertEqual(store.csrf_secret, "")
        store.set_password(PASSWORD)
        first = store.csrf_secret
        self.assertTrue(first)
        store.rotate_csrf_secret()
        second = store.csrf_secret
        self.assertTrue(second)
        self.assertNotEqual(first, second)
        # Rotation preserves the password.
        self.assertTrue(store.verify_password(PASSWORD))

    def test_password_change_revokes_all_sessions(self) -> None:
        credentials = CredentialStore(self.paths)
        sessions = SessionStore(self.paths, clock=self.clock)
        credentials.set_password(PASSWORD)
        session, token = sessions.create()
        self.assertIsNotNone(sessions.verify(token))
        changed = credentials.set_password(OTHER_PASSWORD)
        self.assertTrue(changed)
        self.assertIsNone(sessions.verify(token))
        self.assertEqual(sessions.list_sessions(), [])
        self.assertTrue(credentials.verify_password(OTHER_PASSWORD))
        self.assertFalse(credentials.verify_password(PASSWORD))


class CsrfTests(unittest.TestCase):
    def test_token_is_bound_to_the_session(self) -> None:
        token = csrf_token_for("session-1", "secret")
        self.assertTrue(verify_csrf_token(token, "session-1", "secret"))
        self.assertFalse(verify_csrf_token(token, "session-2", "secret"))
        self.assertFalse(verify_csrf_token(token, "session-1", "other-secret"))
        self.assertFalse(verify_csrf_token("", "session-1", "secret"))
        self.assertFalse(verify_csrf_token(token, "session-1", ""))


class SessionStoreTests(AuthFixture):
    def test_create_and_verify_round_trip(self) -> None:
        store = SessionStore(self.paths, clock=self.clock)
        session, token = store.create(user_agent="test-agent")
        found = store.verify(token)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.session_id, session.session_id)
        self.assertEqual(found.user_agent, "test-agent")
        self.assertEqual(found.expires_at, self.clock.now + SESSION_LIFETIME)

    def test_only_the_token_hash_is_on_disk(self) -> None:
        store = SessionStore(self.paths, clock=self.clock)
        session, token = store.create()
        record_path = self.paths.server_sessions / f"{session.session_id}.json"
        raw = record_path.read_text(encoding="utf-8")
        self.assertNotIn(token, raw)
        data = json.loads(raw)
        self.assertIn("token_hash", data)
        self.assertNotEqual(data["token_hash"], token)

    def test_unknown_token_does_not_verify(self) -> None:
        store = SessionStore(self.paths, clock=self.clock)
        store.create()
        self.assertIsNone(store.verify("not-a-real-token"))
        self.assertIsNone(store.verify(""))

    def test_expired_session_does_not_verify_and_is_purged(self) -> None:
        store = SessionStore(self.paths, clock=self.clock)
        session, token = store.create()
        self.clock.advance(SESSION_LIFETIME + timedelta(seconds=1))
        self.assertIsNone(store.verify(token))
        self.assertEqual(store.list_sessions(), [])
        record_path = self.paths.server_sessions / f"{session.session_id}.json"
        self.assertFalse(record_path.exists())

    def test_touch_renews_sliding_expiry_with_throttle(self) -> None:
        store = SessionStore(self.paths, clock=self.clock)
        session, _ = store.create()
        first_expiry = session.expires_at
        # A touch within the throttle window does not rewrite.
        self.clock.advance(timedelta(seconds=30))
        untouched = store.touch(session.session_id)
        assert untouched is not None
        self.assertEqual(untouched.expires_at, first_expiry)
        # Past the window, the expiry slides forward.
        self.clock.advance(timedelta(seconds=61))
        touched = store.touch(session.session_id)
        assert touched is not None
        self.assertGreater(touched.expires_at, first_expiry)
        self.assertEqual(touched.expires_at, self.clock.now + SESSION_LIFETIME)

    def test_revoke_and_revoke_all(self) -> None:
        store = SessionStore(self.paths, clock=self.clock)
        first, first_token = store.create()
        second, second_token = store.create()
        self.assertTrue(store.revoke(first.session_id))
        self.assertFalse(store.revoke(first.session_id))
        self.assertIsNone(store.verify(first_token))
        self.assertIsNotNone(store.verify(second_token))
        removed = store.revoke_all()
        self.assertEqual(removed, 1)
        self.assertIsNone(store.verify(second_token))
        self.assertEqual(store.list_sessions(), [])

    def test_sessions_are_bounded(self) -> None:
        store = SessionStore(self.paths, clock=self.clock)
        tokens = []
        for _ in range(MAX_SESSIONS + 3):
            _, token = store.create()
            tokens.append(token)
            self.clock.advance(timedelta(seconds=61))
        sessions = store.list_sessions()
        self.assertEqual(len(sessions), MAX_SESSIONS)
        # The oldest sessions were evicted; the newest survive.
        self.assertIsNone(store.verify(tokens[0]))
        self.assertIsNotNone(store.verify(tokens[-1]))

    def test_list_sessions_never_exposes_token_hashes(self) -> None:
        store = SessionStore(self.paths, clock=self.clock)
        store.create()
        for view in store.list_sessions():
            self.assertNotIn("token_hash", view.to_dict())


class LockoutTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.clock = FakeClock()
        self.tracker = LockoutTracker(clock=self.clock)

    def test_five_failures_trip_the_lockout(self) -> None:
        for _ in range(4):
            self.assertFalse(self.tracker.record_failure("127.0.0.1"))
            self.assertFalse(self.tracker.is_locked_out("127.0.0.1"))
        self.assertTrue(self.tracker.record_failure("127.0.0.1"))
        self.assertTrue(self.tracker.is_locked_out("127.0.0.1"))
        # Another source is unaffected.
        self.assertFalse(self.tracker.is_locked_out("10.0.0.9"))

    def test_lockout_expires(self) -> None:
        for _ in range(5):
            self.tracker.record_failure("127.0.0.1")
        self.assertTrue(self.tracker.is_locked_out("127.0.0.1"))
        self.clock.advance(timedelta(minutes=15, seconds=1))
        self.assertFalse(self.tracker.is_locked_out("127.0.0.1"))

    def test_success_clears_failures(self) -> None:
        for _ in range(4):
            self.tracker.record_failure("127.0.0.1")
        self.tracker.record_success("127.0.0.1")
        for _ in range(4):
            self.assertFalse(self.tracker.record_failure("127.0.0.1"))
        self.assertFalse(self.tracker.is_locked_out("127.0.0.1"))

    def test_old_failures_age_out_of_the_window(self) -> None:
        for _ in range(4):
            self.tracker.record_failure("127.0.0.1")
        self.clock.advance(timedelta(minutes=15, seconds=1))
        self.assertFalse(self.tracker.record_failure("127.0.0.1"))
        self.assertFalse(self.tracker.is_locked_out("127.0.0.1"))


class ServerAuthRecoveryKindTests(AuthFixture):
    def test_server_auth_is_a_recovery_kind(self) -> None:
        self.assertIn("server-auth", RECOVERY_KINDS)
        store = RecoveryHistoryStore(self.paths.observability)
        record = store.record(
            "server-auth", "server password", detail="setup: test"
        )
        self.assertEqual(record.kind, "server-auth")
        found = store.query(kind="server-auth")
        self.assertEqual(found.total, 1)
        self.assertEqual(found.records[0].record_id, record.record_id)


class SetupCommandTests(AuthFixture):
    def _run(self, answers: list[str]) -> tuple[int, list[str]]:
        iterator = iter(answers)
        printed: list[str] = []
        result = run_server_setup(
            self.paths,
            input_provider=lambda prompt: next(iterator),
            output=printed.append,
        )
        return result, printed

    def test_setup_sets_the_password_and_audits_it(self) -> None:
        result, _ = self._run([PASSWORD, PASSWORD])
        self.assertEqual(result, 0)
        store = CredentialStore(self.paths)
        self.assertTrue(store.verify_password(PASSWORD))
        history = RecoveryHistoryStore(self.paths.observability)
        found = history.query(kind="server-auth")
        self.assertEqual(found.total, 1)
        self.assertIn("setup", found.records[0].detail)

    def test_mismatched_passwords_fail_without_writing(self) -> None:
        result, _ = self._run([PASSWORD, OTHER_PASSWORD])
        self.assertEqual(result, 1)
        self.assertFalse(CredentialStore(self.paths).exists)

    def test_short_password_fails_without_writing(self) -> None:
        result, _ = self._run(["short", "short"])
        self.assertEqual(result, 1)
        self.assertFalse(CredentialStore(self.paths).exists)

    def test_cancelled_setup_fails_without_writing(self) -> None:
        def _raise(_prompt: str) -> str:
            raise KeyboardInterrupt

        result = run_server_setup(
            self.paths, input_provider=_raise, output=lambda _text: None
        )
        self.assertEqual(result, 1)
        self.assertFalse(CredentialStore(self.paths).exists)

    def test_second_setup_changes_the_password_and_revokes_sessions(self) -> None:
        first, _ = self._run([PASSWORD, PASSWORD])
        self.assertEqual(first, 0)
        sessions = SessionStore(self.paths, clock=self.clock)
        _, token = sessions.create()
        self.assertIsNotNone(sessions.verify(token))
        second, printed = self._run([OTHER_PASSWORD, OTHER_PASSWORD])
        self.assertEqual(second, 0)
        self.assertTrue(any("already exists" in line for line in printed))
        self.assertIsNone(sessions.verify(token))
        store = CredentialStore(self.paths)
        self.assertTrue(store.verify_password(OTHER_PASSWORD))
        history = RecoveryHistoryStore(self.paths.observability)
        found = history.query(kind="server-auth")
        self.assertEqual(found.total, 2)
        self.assertIn("password-change", found.records[0].detail)

    def test_minimum_length_boundary(self) -> None:
        exact = "x" * MIN_PASSWORD_LENGTH
        result, _ = self._run([exact, exact])
        self.assertEqual(result, 0)


class PromptPasswordTests(unittest.TestCase):
    def test_reads_from_stdin_without_a_terminal(self) -> None:
        import io
        from unittest import mock

        from orion.server.setup import _prompt_password

        with mock.patch("sys.stdin", io.StringIO("piped-password\n")):
            self.assertEqual(_prompt_password("Password: "), "piped-password")

    def test_uses_getpass_on_a_terminal(self) -> None:
        import io
        from unittest import mock

        from orion.server import setup as setup_module

        class TtyStdin(io.StringIO):
            def isatty(self) -> bool:  # noqa: D102
                return True

        with (
            mock.patch("sys.stdin", TtyStdin()),
            mock.patch.object(
                setup_module, "getpass", return_value="hidden-password"
            ),
        ):
            self.assertEqual(
                setup_module._prompt_password("Password: "), "hidden-password"
            )


if __name__ == "__main__":
    unittest.main()
