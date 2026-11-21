"""Auth tests for the headless server (Phase B).

Credential store, sessions, CSRF binding, lockout, and the local-only
setup command. All tests use temp user roots with throwaway passwords;
nothing here touches a real ~/.orion.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orion.application.observability.recovery import (
    RECOVERY_KINDS,
    RecoveryHistoryStore,
)
from orion.core.paths import OrionPaths
from orion.server.aiohttp_server import LocalApiServer
from orion.server.api import ROUTES, ApiRequest
from orion.server.auth import (
    LOCKOUT_THRESHOLD,
    MAX_SESSIONS,
    MIN_PASSWORD_LENGTH,
    SESSION_LIFETIME,
    Authenticator,
    CredentialStore,
    LockoutTracker,
    SessionStore,
    TailnetIdentity,
    csrf_token_for,
    verify_csrf_token,
)
from orion.server.config import ServerConfig
from orion.server.errors import ApiError
from orion.server.logging import close_server_logging, setup_server_logging
from orion.server.setup import run_server_setup
from tests.test_mission_coordinator import MissionCoordinatorFixture
from tests.test_server_api import ApiFixture

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



TAILNET_STATUS = {
    "Self": {
        "HostName": "daniels-server",
        "UserID": 1,
        "TailscaleIPs": ["100.80.66.37"],
    },
    "Peer": {
        "node-muse": {
            "HostName": "muse",
            "UserID": 1,
            "TailscaleIPs": ["100.116.33.108"],
        },
        "node-phone": {
            "HostName": "phone",
            "UserID": 2,
            "TailscaleIPs": ["100.99.99.99"],
        },
    },
    "User": {
        "1": {"LoginName": "daniel@example.com"},
        "2": {"LoginName": "jess@example.com"},
    },
}


class TailnetIdentityTests(unittest.TestCase):
    def test_lookup_returns_user_at_device(self) -> None:
        identity = TailnetIdentity(runner=lambda: TAILNET_STATUS)
        self.assertEqual(
            identity.lookup("100.80.66.37"),
            "daniel@example.com@daniels-server",
        )
        self.assertEqual(
            identity.lookup("100.116.33.108"),
            "daniel@example.com@muse",
        )
        self.assertEqual(
            identity.lookup("100.99.99.99"), "jess@example.com@phone"
        )

    def test_unknown_ip_returns_empty(self) -> None:
        identity = TailnetIdentity(runner=lambda: TAILNET_STATUS)
        self.assertEqual(identity.lookup("100.64.0.1"), "")
        self.assertEqual(identity.lookup(""), "")

    def test_runner_failure_returns_empty(self) -> None:
        def _boom() -> dict:
            raise OSError("tailscale is not installed")

        identity = TailnetIdentity(runner=_boom)
        self.assertEqual(identity.lookup("100.80.66.37"), "")

    def test_results_are_cached(self) -> None:
        calls: list[None] = []

        def _runner() -> dict:
            calls.append(None)
            return TAILNET_STATUS

        identity = TailnetIdentity(runner=_runner)
        identity.lookup("100.80.66.37")
        identity.lookup("100.116.33.108")
        self.assertEqual(len(calls), 1)

    def test_cache_refreshes_after_ttl(self) -> None:
        calls: list[None] = []

        def _runner() -> dict:
            calls.append(None)
            return TAILNET_STATUS

        identity = TailnetIdentity(
            ttl=timedelta(0), runner=_runner
        )
        identity.lookup("100.80.66.37")
        identity.lookup("100.80.66.37")
        self.assertEqual(len(calls), 2)


class AuthenticatorLoginTests(AuthFixture):
    """Login lockout, audit events, and tailnet identity."""

    def setUp(self) -> None:
        super().setUp()
        CredentialStore(self.paths).set_password(PASSWORD)
        config = SimpleNamespace(secure_cookies=True, tailscale_host="")
        self.tailnet = TailnetIdentity(runner=lambda: TAILNET_STATUS)
        self.authenticator = Authenticator(
            self.paths,
            config,
            clock=self.clock,
            tailnet=self.tailnet,
        )
        self.sessions = SessionStore(self.paths, clock=self.clock)

    def _login_request(
        self,
        password: str,
        *,
        remote_addr: str = "127.0.0.1",
        headers: dict | None = None,
    ) -> ApiRequest:
        all_headers = {"host": "127.0.0.1:8000"}
        if headers:
            all_headers.update(headers)
        return ApiRequest(
            method="POST",
            path="/api/auth/login",
            headers=all_headers,
            body={"password": password},
            remote_addr=remote_addr,
        )

    def _attempt(self, password: str, **kwargs) -> ApiError | None:
        try:
            self.authenticator.login(self._login_request(password, **kwargs))
        except ApiError as exc:
            return exc
        return None

    def _auth_events(self) -> list:
        history = RecoveryHistoryStore(self.paths.observability)
        return history.query(kind="server-auth").records

    def test_five_failures_lock_out_the_sixth(self) -> None:
        for _ in range(LOCKOUT_THRESHOLD):
            error = self._attempt("wrong-password-here")
            assert error is not None
            self.assertEqual(error.status, 401)
            self.assertEqual(error.code, "invalid_credentials")
        error = self._attempt(PASSWORD)
        assert error is not None
        self.assertEqual(error.status, 429)
        self.assertEqual(error.code, "locked_out")

    def test_lockout_expires(self) -> None:
        for _ in range(LOCKOUT_THRESHOLD):
            self._attempt("wrong-password-here")
        self.assertIsNotNone(self._attempt(PASSWORD))
        self.clock.advance(timedelta(minutes=15, seconds=1))
        self.assertIsNone(self._attempt(PASSWORD))

    def test_success_clears_failures(self) -> None:
        for _ in range(LOCKOUT_THRESHOLD - 1):
            self._attempt("wrong-password-here")
        self.assertIsNone(self._attempt(PASSWORD))
        for _ in range(LOCKOUT_THRESHOLD - 1):
            error = self._attempt("wrong-password-here")
            assert error is not None
            self.assertEqual(error.status, 401)
        error = self._attempt("wrong-password-here")
        assert error is not None
        self.assertEqual(error.status, 401)
        error = self._attempt(PASSWORD)
        assert error is not None
        self.assertEqual(error.status, 429)

    def test_login_success_is_audited(self) -> None:
        self.assertIsNone(self._attempt(PASSWORD))
        events = self._auth_events()
        self.assertEqual(len(events), 1)
        self.assertIn("login", events[0].detail)
        self.assertIn("Login succeeded", events[0].detail)
        self.assertIn("127.0.0.1", events[0].detail)
        self.assertEqual(events[0].status, "success")

    def test_login_failures_and_lockout_are_audited(self) -> None:
        for _ in range(LOCKOUT_THRESHOLD):
            self._attempt("wrong-password-here")
        events = self._auth_events()
        self.assertEqual(len(events), LOCKOUT_THRESHOLD + 1)
        failures = [e for e in events if "Login failed" in e.detail]
        self.assertEqual(len(failures), LOCKOUT_THRESHOLD)
        self.assertTrue(all(e.status == "failed" for e in failures))
        lockouts = [e for e in events if e.detail.startswith("lockout:")]
        self.assertEqual(len(lockouts), 1)
        self.assertIn("locked out for 15 minutes", lockouts[0].detail)

    def test_logout_revoke_and_revoke_all_are_audited(self) -> None:
        self.assertIsNone(self._attempt(PASSWORD))
        self.assertIsNone(self._attempt(PASSWORD))
        sessions = self.sessions.list_sessions()
        self.assertEqual(len(sessions), 2)
        current = sessions[0]
        base = {
            "headers": {"host": "127.0.0.1:8000"},
            "remote_addr": "127.0.0.1",
            "session": current,
        }
        self.authenticator.logout(
            ApiRequest(method="POST", path="/api/auth/logout", **base)
        )
        other = sessions[1]
        self.authenticator.revoke_session(
            ApiRequest(
                method="POST",
                path=f"/api/auth/sessions/{other.session_id}/revoke",
                path_params={"session_id": other.session_id},
                **base,
            )
        )
        self.assertIsNone(self._attempt(PASSWORD))
        sessions = self.sessions.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.authenticator.revoke_all(
            ApiRequest(
                method="POST",
                path="/api/auth/revoke-all",
                session=sessions[0],
                headers={"host": "127.0.0.1:8000"},
                remote_addr="127.0.0.1",
            )
        )
        events = self._auth_events()
        details = [event.detail for event in events]
        self.assertTrue(
            any(detail.startswith("logout:") for detail in details)
        )
        self.assertTrue(
            any(detail.startswith("revoke:") for detail in details)
        )
        self.assertTrue(
            any(detail.startswith("revoke-all:") for detail in details)
        )

    def test_tailnet_identity_from_forwarded_for(self) -> None:
        self.assertIsNone(
            self._attempt(
                PASSWORD,
                headers={"x-forwarded-for": "100.116.33.108"},
            )
        )
        sessions = self.sessions.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(
            sessions[0].tailnet_identity, "daniel@example.com@muse"
        )

    def test_non_tailnet_forwarded_for_is_ignored(self) -> None:
        self.assertIsNone(
            self._attempt(
                PASSWORD,
                headers={"x-forwarded-for": "8.8.8.8"},
            )
        )
        sessions = self.sessions.list_sessions()
        self.assertEqual(sessions[0].tailnet_identity, "")

    def test_direct_tailnet_remote_addr(self) -> None:
        self.assertIsNone(
            self._attempt(PASSWORD, remote_addr="100.99.99.99")
        )
        sessions = self.sessions.list_sessions()
        self.assertEqual(
            sessions[0].tailnet_identity, "jess@example.com@phone"
        )

    def test_last_forwarded_for_entry_wins(self) -> None:
        # A client-supplied first entry must not spoof the identity:
        # Serve appends the real client IP last.
        self.assertIsNone(
            self._attempt(
                PASSWORD,
                headers={
                    "x-forwarded-for": "100.99.99.99, 100.116.33.108"
                },
            )
        )
        sessions = self.sessions.list_sessions()
        self.assertEqual(
            sessions[0].tailnet_identity, "daniel@example.com@muse"
        )


class AuthenticatorGateTests(AuthFixture):
    """CSRF + Origin/Host validation at the Authenticator level."""
    SERVE_HOST = "daniels-server.test.ts.net"

    def setUp(self) -> None:
        super().setUp()
        CredentialStore(self.paths).set_password(PASSWORD)
        config = SimpleNamespace(
            secure_cookies=True, tailscale_host=self.SERVE_HOST
        )
        self.authenticator = Authenticator(
            self.paths, config, clock=self.clock
        )
        self.sessions = SessionStore(self.paths, clock=self.clock)
        self.session, self.token = self.sessions.create()
        self.csrf = csrf_token_for(
            self.session.session_id,
            CredentialStore(self.paths).csrf_secret,
        )

    def _request(
        self,
        method: str = "GET",
        path: str = "/api/healthz",
        *,
        headers: dict | None = None,
        body: dict | None = None,
    ) -> ApiRequest:
        all_headers = {"host": "127.0.0.1:8000"}
        if headers:
            all_headers.update(headers)
        return ApiRequest(
            method=method, path=path, headers=all_headers, body=body
        )

    def _authed(
        self,
        method: str = "GET",
        path: str = "/api/healthz",
        *,
        headers: dict | None = None,
        body: dict | None = None,
    ) -> ApiRequest:
        all_headers = {"cookie": f"orion_session={self.token}"}
        if headers:
            all_headers.update(headers)
        return self._request(
            method, path, headers=all_headers, body=body
        )

    def test_get_with_valid_session_passes(self) -> None:
        session = self.authenticator.authenticate(self._authed())
        assert session is not None
        self.assertEqual(session.session_id, self.session.session_id)

    def test_post_without_csrf_is_403(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.authenticator.authenticate(
                self._authed("POST", "/api/auth/logout", body={})
            )
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(ctx.exception.code, "csrf_required")

    def test_post_with_bound_csrf_passes(self) -> None:
        session = self.authenticator.authenticate(
            self._authed(
                "POST", "/api/auth/logout", body={},
                headers={"x-csrf-token": self.csrf},
            )
        )
        assert session is not None
        self.assertEqual(session.session_id, self.session.session_id)

    def test_post_with_unbound_csrf_is_403(self) -> None:
        other, _ = self.sessions.create()
        other_csrf = csrf_token_for(
            other.session_id, CredentialStore(self.paths).csrf_secret
        )
        with self.assertRaises(ApiError) as ctx:
            self.authenticator.authenticate(
                self._authed(
                    "POST", "/api/auth/logout", body={},
                    headers={"x-csrf-token": other_csrf},
                )
            )
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(ctx.exception.code, "csrf_invalid")

    def test_missing_host_is_403(self) -> None:
        request = ApiRequest(
            method="GET",
            path="/api/healthz",
            headers={"cookie": f"orion_session={self.token}"},
        )
        with self.assertRaises(ApiError) as ctx:
            self.authenticator.authenticate(request)
        self.assertEqual(ctx.exception.code, "forbidden_host")

    def test_bad_host_is_403(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.authenticator.authenticate(
                self._authed(headers={"host": "evil.example.com"})
            )
        self.assertEqual(ctx.exception.code, "forbidden_host")

    def test_serve_host_passes(self) -> None:
        session = self.authenticator.authenticate(
            self._authed(headers={"host": self.SERVE_HOST})
        )
        self.assertIsNotNone(session)

    def test_ipv6_loopback_host_passes(self) -> None:
        session = self.authenticator.authenticate(
            self._authed(headers={"host": "[::1]:8000"})
        )
        self.assertIsNotNone(session)

    def test_bad_origin_is_403(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.authenticator.authenticate(
                self._authed(
                    headers={"origin": "https://evil.example.com"}
                )
            )
        self.assertEqual(ctx.exception.code, "forbidden_origin")

    def test_serve_origin_passes(self) -> None:
        session = self.authenticator.authenticate(
            self._authed(
                headers={"origin": f"https://{self.SERVE_HOST}"}
            )
        )
        self.assertIsNotNone(session)

    def test_login_is_exempt_from_csrf_but_not_origin(self) -> None:
        result = self.authenticator.authenticate(
            self._request(
                "POST", "/api/auth/login", body={"password": PASSWORD}
            )
        )
        self.assertIsNone(result)
        with self.assertRaises(ApiError) as ctx:
            self.authenticator.authenticate(
                self._request(
                    "POST", "/api/auth/login",
                    body={"password": PASSWORD},
                    headers={"origin": "https://evil.example.com"},
                )
            )
        self.assertEqual(ctx.exception.code, "forbidden_origin")


# -- live-socket HTTP auth tests ---------------------------------------


class AuthHttpFixture(ApiFixture):
    """Starts a real authenticated LocalApiServer on an ephemeral port.

    Mirrors tests.test_server_http.HttpFixture, but with auth ON: a
    temp user root, a password set programmatically (the setup
    command's hidden prompt is unit-tested above), and the dev opt-out
    explicitly removed from the environment.
    """

    set_password = True
    server_kwargs: dict = {}

    def start_auth_server(self) -> None:
        self._auth_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._auth_temp.cleanup)
        self.paths = OrionPaths(user_root=self._auth_temp.name)
        if self.set_password:
            CredentialStore(self.paths).set_password(PASSWORD)
        self._env = patch.dict(os.environ, {})
        self._env.start()
        self.addCleanup(self._env.stop)
        os.environ.pop("ORION_SERVER_AUTH", None)
        self.runtime.paths = self.paths
        logger = setup_server_logging(self.paths)
        kwargs: dict = {
            "host": "127.0.0.1",
            "port": 0,
            "auth_required": True,
            "shutdown_drain_seconds": 5,
            "max_concurrent_requests": 4,
            "max_request_bytes": 1_048_576,
            "max_response_bytes": 4_194_304,
            "request_timeout_seconds": 30,
        }
        kwargs.update(self.server_kwargs)
        config = ServerConfig(**kwargs)
        self.server = LocalApiServer(self.runtime, config, logger=logger)
        self.server.start()
        self.addCleanup(self._stop_auth_server)
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"

    def _stop_auth_server(self) -> None:
        if self.server.started:
            self.server.stop()
        close_server_logging()

    def http(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict, dict]:
        data = None
        all_headers: dict[str, str] = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            all_headers["Content-Type"] = "application/json"
        if headers:
            all_headers.update(headers)
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=all_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload, dict(response.headers)
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
            return exc.code, payload, dict(exc.headers)

    def login(self, password: str = PASSWORD) -> tuple[int, dict, dict]:
        return self.http(
            "POST", "/api/auth/login", body={"password": password}
        )

    @staticmethod
    def cookie_from(headers: dict) -> str:
        """The `name=value` pair from a Set-Cookie header."""
        set_cookie = headers.get("Set-Cookie", "")
        return set_cookie.split(";")[0].strip()


class AuthHttpBase(MissionCoordinatorFixture, AuthHttpFixture):
    """Test base: mission services + an authenticated live server."""

    def setUp(self) -> None:
        super().setUp()
        self.build_api()
        self.start_auth_server()


class AuthHttpTests(AuthHttpBase):
    def test_login_success_issues_cookie_and_csrf(self) -> None:
        status, payload, headers = self.login()
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "success")
        data = payload["data"]
        self.assertTrue(data["session_id"])
        self.assertTrue(data["expires_at"])
        self.assertTrue(data["csrf_token"])
        set_cookie = headers.get("Set-Cookie", "")
        self.assertTrue(set_cookie.startswith("orion_session="))
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Lax", set_cookie)
        # Secure by default, even over loopback HTTP: the flag is
        # config, not socket inference (TLS terminates at Serve).
        self.assertIn("Secure", set_cookie)

    def test_login_wrong_password_is_401(self) -> None:
        status, payload, headers = self.login(password="wrong-password-here")
        self.assertEqual(status, 401)
        self.assertEqual(payload["data"]["error_code"], "invalid_credentials")
        self.assertNotIn("Set-Cookie", headers)

    def test_login_without_password_is_400(self) -> None:
        status, payload, _ = self.http("POST", "/api/auth/login", body={})
        self.assertEqual(status, 400)

    def test_login_lockout_over_http(self) -> None:
        for _ in range(5):
            status, payload, _ = self.login(password="wrong-password-here")
            self.assertEqual(status, 401)
            self.assertEqual(
                payload["data"]["error_code"], "invalid_credentials"
            )
        status, payload, _ = self.login(password="wrong-password-here")
        self.assertEqual(status, 429)
        self.assertEqual(payload["data"]["error_code"], "locked_out")
        status, _, _ = self.login()
        self.assertEqual(status, 429)

    def test_auth_events_are_audited(self) -> None:
        _, login_payload, headers = self.login()
        cookie = self.cookie_from(headers)
        csrf = login_payload["data"]["csrf_token"]
        status, _, _ = self.http(
            "POST", "/api/auth/logout", body={},
            headers={"Cookie": cookie, "X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 200)
        _, second_payload, second_headers = self.login()
        second_cookie = self.cookie_from(second_headers)
        second_csrf = second_payload["data"]["csrf_token"]
        status, _, _ = self.http(
            "POST", "/api/auth/revoke-all", body={},
            headers={"Cookie": second_cookie, "X-CSRF-Token": second_csrf},
        )
        self.assertEqual(status, 200)
        history = RecoveryHistoryStore(self.paths.observability)
        events = history.query(kind="server-auth").records
        details = [event.detail for event in events]
        self.assertEqual(len(events), 4)
        prefixes = sorted(detail.split(":")[0] for detail in details)
        self.assertEqual(
            prefixes, ["login", "login", "logout", "revoke-all"]
        )
        self.assertTrue(
            any("Login succeeded" in detail for detail in details)
        )

    def test_unauthenticated_requests_rejected_on_every_endpoint(self) -> None:
        for method, template, _ in ROUTES:
            if template == "/api/auth/login":
                continue
            path = re.sub(r"\{[^}]+\}", "x", template)
            status, payload, _ = self.http(
                method, path, body={} if method == "POST" else None
            )
            self.assertEqual(status, 401, f"{method} {path}")
            self.assertEqual(
                payload["data"]["error_code"], "unauthenticated"
            )

    def test_authenticated_read(self) -> None:
        _, _, headers = self.login()
        cookie = self.cookie_from(headers)
        status, payload, _ = self.http(
            "GET", "/api/healthz", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["status"], "ok")

    def test_unknown_cookie_is_401(self) -> None:
        status, _, _ = self.http(
            "GET", "/api/healthz",
            headers={"Cookie": "orion_session=not-a-real-token"},
        )
        self.assertEqual(status, 401)

    def test_mutating_post_without_csrf_is_403(self) -> None:
        _, _, headers = self.login()
        cookie = self.cookie_from(headers)
        status, payload, _ = self.http(
            "POST", "/api/auth/logout", body={}, headers={"Cookie": cookie}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["data"]["error_code"], "csrf_required")
        # The session survives the rejected logout.
        status, _, _ = self.http(
            "GET", "/api/healthz", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)

    def test_csrf_token_from_another_session_is_403(self) -> None:
        _, first_payload, first_headers = self.login()
        _, second_payload, _ = self.login()
        first_cookie = self.cookie_from(first_headers)
        other_csrf = second_payload["data"]["csrf_token"]
        status, payload, _ = self.http(
            "POST", "/api/auth/logout", body={},
            headers={"Cookie": first_cookie, "X-CSRF-Token": other_csrf},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["data"]["error_code"], "csrf_invalid")

    def test_bad_origin_is_403(self) -> None:
        _, _, headers = self.login()
        cookie = self.cookie_from(headers)
        status, payload, _ = self.http(
            "GET", "/api/healthz",
            headers={
                "Cookie": cookie,
                "Origin": "https://evil.example.com",
            },
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["data"]["error_code"], "forbidden_origin")

    def test_allowed_origin_passes(self) -> None:
        _, _, headers = self.login()
        cookie = self.cookie_from(headers)
        status, _, _ = self.http(
            "GET", "/api/healthz",
            headers={
                "Cookie": cookie,
                "Origin": f"http://127.0.0.1:{self.port}",
            },
        )
        self.assertEqual(status, 200)

    def test_bad_host_is_403(self) -> None:
        _, _, headers = self.login()
        cookie = self.cookie_from(headers)
        status, payload, _ = self.http(
            "GET", "/api/healthz",
            headers={"Cookie": cookie, "Host": "evil.example.com"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["data"]["error_code"], "forbidden_host")

    def test_me_returns_session_and_csrf(self) -> None:
        _, login_payload, headers = self.login()
        cookie = self.cookie_from(headers)
        status, payload, _ = self.http(
            "GET", "/api/auth/me", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        data = payload["data"]
        self.assertEqual(
            data["session_id"], login_payload["data"]["session_id"]
        )
        self.assertTrue(data["csrf_token"])
        self.assertNotIn("token_hash", data)

    def test_logout_revokes_the_session(self) -> None:
        _, login_payload, headers = self.login()
        cookie = self.cookie_from(headers)
        csrf = login_payload["data"]["csrf_token"]
        status, _, logout_headers = self.http(
            "POST", "/api/auth/logout", body={},
            headers={"Cookie": cookie, "X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", logout_headers.get("Set-Cookie", ""))
        status, _, _ = self.http(
            "GET", "/api/healthz", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 401)

    def test_sessions_list_and_revoke(self) -> None:
        _, first_payload, first_headers = self.login()
        _, second_payload, second_headers = self.login()
        first_cookie = self.cookie_from(first_headers)
        second_cookie = self.cookie_from(second_headers)
        status, payload, _ = self.http(
            "GET", "/api/auth/sessions", headers={"Cookie": first_cookie}
        )
        self.assertEqual(status, 200)
        sessions = payload["data"]["sessions"]
        self.assertEqual(len(sessions), 2)
        current = [item for item in sessions if item["current"]]
        self.assertEqual(len(current), 1)
        self.assertEqual(
            current[0]["session_id"], first_payload["data"]["session_id"]
        )
        other_id = second_payload["data"]["session_id"]
        first_csrf = first_payload["data"]["csrf_token"]
        status, _, _ = self.http(
            "POST",
            f"/api/auth/sessions/{other_id}/revoke",
            body={},
            headers={"Cookie": first_cookie, "X-CSRF-Token": first_csrf},
        )
        self.assertEqual(status, 200)
        status, _, _ = self.http(
            "GET", "/api/healthz", headers={"Cookie": second_cookie}
        )
        self.assertEqual(status, 401)
        status, payload, _ = self.http(
            "GET", "/api/auth/sessions", headers={"Cookie": first_cookie}
        )
        self.assertEqual(len(payload["data"]["sessions"]), 1)

    def test_revoke_unknown_session_is_404(self) -> None:
        _, login_payload, headers = self.login()
        cookie = self.cookie_from(headers)
        csrf = login_payload["data"]["csrf_token"]
        status, _, _ = self.http(
            "POST",
            "/api/auth/sessions/does-not-exist/revoke",
            body={},
            headers={"Cookie": cookie, "X-CSRF-Token": csrf},
        )
        self.assertEqual(status, 404)

    def test_revoke_all_signs_out_everywhere(self) -> None:
        _, first_payload, first_headers = self.login()
        _, _, second_headers = self.login()
        first_cookie = self.cookie_from(first_headers)
        second_cookie = self.cookie_from(second_headers)
        first_csrf = first_payload["data"]["csrf_token"]
        status, payload, _ = self.http(
            "POST", "/api/auth/revoke-all", body={},
            headers={"Cookie": first_cookie, "X-CSRF-Token": first_csrf},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["revoked_sessions"], 2)
        for cookie in (first_cookie, second_cookie):
            status, _, _ = self.http(
                "GET", "/api/healthz", headers={"Cookie": cookie}
            )
            self.assertEqual(status, 401)

    def test_expired_session_is_rejected(self) -> None:
        _, _, headers = self.login()
        cookie = self.cookie_from(headers)
        session_files = list(self.paths.server_sessions.glob("*.json"))
        self.assertEqual(len(session_files), 1)
        record = json.loads(session_files[0].read_text(encoding="utf-8"))
        record["expires_at"] = "2020-01-01T00:00:00Z"
        session_files[0].write_text(
            json.dumps(record), encoding="utf-8"
        )
        status, _, _ = self.http(
            "GET", "/api/healthz", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 401)

    def test_deleting_auth_file_returns_to_setup_mode(self) -> None:
        _, _, headers = self.login()
        cookie = self.cookie_from(headers)
        self.paths.server_auth.unlink()
        status, payload, _ = self.http(
            "GET", "/api/healthz", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 503)
        self.assertEqual(payload["data"]["error_code"], "setup_required")
        status, _, _ = self.login()
        self.assertEqual(status, 503)


class SetupModeHttpTests(AuthHttpBase):
    """The server with no password set: 503 everywhere."""

    set_password = False

    def test_setup_mode_refuses_every_endpoint(self) -> None:
        for method, template, _ in ROUTES:
            path = re.sub(r"\{[^}]+\}", "x", template)
            status, payload, _ = self.http(
                method, path, body={} if method == "POST" else None
            )
            self.assertEqual(status, 503, f"{method} {path}")
            self.assertEqual(
                payload["data"]["error_code"], "setup_required"
            )


class InsecureCookieHttpTests(AuthHttpBase):
    """secure_cookies=false: the login cookie has no Secure flag."""

    server_kwargs = {"secure_cookies": False}

    def test_login_cookie_has_no_secure_flag(self) -> None:
        _, _, headers = self.login()
        set_cookie = headers.get("Set-Cookie", "")
        self.assertTrue(set_cookie.startswith("orion_session="))
        self.assertNotIn("Secure", set_cookie)


if __name__ == "__main__":
    unittest.main()
