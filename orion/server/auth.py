"""Server authentication: password, sessions, CSRF, lockout (Phase B).

The auth boundary for the headless server, behind the tailnet (the
second of the two independent gates). One user, one password:

- The password is verified against a ``hashlib.scrypt`` hash (stdlib)
  in ``~/.orion/server/auth.json`` (owner-only permissions). No password
  in plaintext anywhere — not in config, not in the Vault, not in logs.
- Login issues a random session token as an HttpOnly cookie; sessions
  are server-side records under ``~/.orion/server/sessions/`` with
  ONLY the SHA-256 hash of each token on disk, so a stolen session file
  yields no usable token.
- Mutating requests additionally require a CSRF token HMAC-bound to
  the authenticated session, plus strict Origin/Host validation.
- 5 failed logins in 15 minutes from one source locks that source out
  for 15 minutes (in-memory counters; a restart clears attack state).
- Every auth event is recorded in the recovery history (kind
  ``server-auth``) with source + tailnet identity when available.

The local-only setup command lives in ``orion/server/setup.py``; the
HTTP endpoints and the request gate live in ``orion/server/api.py``
via the Authenticator below.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from orion.application.observability.recovery import RecoveryHistoryStore

MIN_PASSWORD_LENGTH = 12
SESSION_COOKIE_NAME = "orion_session"
CSRF_HEADER_NAME = "X-CSRF-Token"
SESSION_LIFETIME = timedelta(days=7)
SESSION_TOUCH_INTERVAL = timedelta(seconds=60)
MAX_SESSIONS = 20

LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW = timedelta(minutes=15)
LOCKOUT_DURATION = timedelta(minutes=15)

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16
CSRF_SECRET_BYTES = 32

AUTH_FILE_VERSION = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically: unique temp file + fsync + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex[:8]}.tmp"
    payload = json.dumps(data, sort_keys=True).encode("utf-8")
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _owner_only(path: Path) -> None:
    """Best-effort owner-only permissions (as with the Vault on Windows)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def record_auth_event(
    paths: object,
    event: str,
    target: str,
    *,
    detail: str = "",
    status: str = "success",
    logger: logging.Logger | None = None,
) -> None:
    """Append one server-auth audit event. Never raises into the caller."""
    try:
        observability = getattr(paths, "observability", None)
        if observability is None:
            return
        store = RecoveryHistoryStore(observability)
        store.record(
            "server-auth",
            str(target),
            status=status,
            detail=f"{event}: {detail}".strip() if detail else str(event),
        )
    except Exception as exc:  # noqa: BLE001 - audit must never break auth
        log = logger or logging.getLogger("orion.server.auth")
        log.warning("could not record server-auth event %s: %s", event, exc)


class CredentialStore:
    """The scrypt password hash + CSRF server secret in auth.json."""

    def __init__(self, paths: object) -> None:
        self._path = Path(getattr(paths, "server_auth"))
        self._sessions_dir = Path(getattr(paths, "server_sessions"))
        self._lock = threading.RLock()
        self._logger = logging.getLogger("orion.server.auth")

    @property
    def path(self) -> Path:
        return self._path

    @property
    def exists(self) -> bool:
        return self._path.is_file()

    def _read(self) -> dict | None:
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def set_password(self, password: str) -> bool:
        """Hash and store a new password.

        Returns True when a password already existed (a change) and
        False for the first setup. A change revokes every session and
        rotates the CSRF secret: a password change implies the old
        credential may be compromised, so no bearer token issued under
        it survives. Raises ValueError for a too-short password.
        """
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
            )
        with self._lock:
            existed = self.exists
            salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
            digest = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=SCRYPT_N,
                r=SCRYPT_R,
                p=SCRYPT_P,
                dklen=SCRYPT_DKLEN,
            )
            data = {
                "version": AUTH_FILE_VERSION,
                "scrypt": {
                    "n": SCRYPT_N,
                    "r": SCRYPT_R,
                    "p": SCRYPT_P,
                    "dklen": SCRYPT_DKLEN,
                    "salt": salt.hex(),
                    "hash": digest.hex(),
                },
                "csrf_secret": secrets.token_hex(CSRF_SECRET_BYTES),
            }
            _atomic_write_json(self._path, data)
            _owner_only(self._path)
            if existed:
                self._revoke_all_sessions()
            return existed

    def verify_password(self, password: str) -> bool:
        """Verify a password against the stored hash. False when unset."""
        with self._lock:
            data = self._read()
        if data is None:
            return False
        try:
            params = data["scrypt"]
            salt = bytes.fromhex(params["salt"])
            expected = params["hash"]
            digest = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=int(params["n"]),
                r=int(params["r"]),
                p=int(params["p"]),
                dklen=int(params["dklen"]),
            )
        except (KeyError, TypeError, ValueError):
            return False
        return hmac.compare_digest(digest.hex(), str(expected))

    @property
    def csrf_secret(self) -> str:
        """The CSRF HMAC secret (empty when no password is set)."""
        with self._lock:
            data = self._read()
        if data is None:
            return ""
        secret = data.get("csrf_secret", "")
        return str(secret) if isinstance(secret, str) else ""

    def rotate_csrf_secret(self) -> None:
        """Regenerate the CSRF secret, preserving the password hash."""
        with self._lock:
            data = self._read()
            if data is None:
                return
            data["csrf_secret"] = secrets.token_hex(CSRF_SECRET_BYTES)
            _atomic_write_json(self._path, data)
            _owner_only(self._path)

    def _revoke_all_sessions(self) -> int:
        """Delete every session file. Caller holds the lock."""
        if not self._sessions_dir.is_dir():
            return 0
        removed = 0
        for path in self._sessions_dir.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                self._logger.warning("could not remove session file %s: %s", path, exc)
        return removed


def csrf_token_for(session_id: str, secret: str) -> str:
    """The CSRF token bound to one session (HMAC, server secret)."""
    return hmac.new(
        secret.encode("utf-8"),
        session_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_csrf_token(token: str, session_id: str, secret: str) -> bool:
    """True only when the token is bound to this session by this secret."""
    if not token or not secret:
        return False
    return hmac.compare_digest(token, csrf_token_for(session_id, secret))


@dataclass(frozen=True)
class Session:
    """One authenticated session (server-side record)."""

    session_id: str
    token_hash: str
    created_at: datetime
    last_seen: datetime
    expires_at: datetime
    user_agent: str = ""
    tailnet_identity: str = ""

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or _utcnow()) >= self.expires_at

    def to_dict(self) -> dict[str, str]:
        """Public view for the sessions list — never the token hash."""
        return {
            "session_id": self.session_id,
            "created_at": _iso(self.created_at),
            "last_seen": _iso(self.last_seen),
            "expires_at": _iso(self.expires_at),
            "user_agent": self.user_agent,
            "tailnet_identity": self.tailnet_identity,
        }

    def _record(self) -> dict[str, str]:
        data = self.to_dict()
        data["token_hash"] = self.token_hash
        return data

    @classmethod
    def _from_record(cls, data: dict) -> "Session | None":
        try:
            return cls(
                session_id=str(data["session_id"]),
                token_hash=str(data["token_hash"]),
                created_at=_parse_iso(str(data["created_at"])),
                last_seen=_parse_iso(str(data["last_seen"])),
                expires_at=_parse_iso(str(data["expires_at"])),
                user_agent=str(data.get("user_agent", "")),
                tailnet_identity=str(data.get("tailnet_identity", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


class SessionStore:
    """Server-side sessions; only SHA-256(token) is ever on disk."""

    def __init__(
        self,
        paths: object,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._dir = Path(getattr(paths, "server_sessions"))
        self._clock = clock or _utcnow
        self._lock = threading.RLock()
        self._logger = logging.getLogger("orion.server.auth")

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    def _read(self, path: Path) -> Session | None:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        return Session._from_record(data) if isinstance(data, dict) else None

    def _write(self, session: Session) -> None:
        _atomic_write_json(self._path(session.session_id), session._record())
        _owner_only(self._path(session.session_id))

    def create(
        self,
        *,
        user_agent: str = "",
        tailnet_identity: str = "",
    ) -> tuple[Session, str]:
        """Create a session. Returns (session, bearer token) — the token
        is returned ONCE and never stored in a usable form."""
        now = self._clock()
        token = secrets.token_urlsafe(32)
        session = Session(
            session_id=secrets.token_hex(8),
            token_hash=_hash_token(token),
            created_at=now,
            last_seen=now,
            expires_at=now + SESSION_LIFETIME,
            user_agent=user_agent,
            tailnet_identity=tailnet_identity,
        )
        with self._lock:
            self.purge_expired()
            existing = self.list_sessions()
            if len(existing) >= MAX_SESSIONS:
                # At the cap: drop the least recently seen session.
                oldest = min(existing, key=lambda item: item.last_seen)
                self.revoke(oldest.session_id)
            self._write(session)
        return session, token

    def verify(self, token: str) -> Session | None:
        """Verify a presented bearer token. None when unknown/expired."""
        if not token:
            return None
        wanted = _hash_token(token)
        with self._lock:
            for path in sorted(self._dir.glob("*.json")) if self._dir.is_dir() else ():
                session = self._read(path)
                if session is None:
                    continue
                if not hmac.compare_digest(session.token_hash, wanted):
                    continue
                if session.is_expired(self._clock()):
                    self.revoke(session.session_id)
                    return None
                return session
        return None

    def touch(self, session_id: str) -> Session | None:
        """Sliding renewal: refresh last_seen + expiry, at most once
        per SESSION_TOUCH_INTERVAL per session (no disk write on every
        poll). Returns the updated session, or None when unknown."""
        now = self._clock()
        with self._lock:
            session = self._read(self._path(session_id))
            if session is None or session.is_expired(now):
                return None
            if now - session.last_seen < SESSION_TOUCH_INTERVAL:
                return session
            updated = Session(
                session_id=session.session_id,
                token_hash=session.token_hash,
                created_at=session.created_at,
                last_seen=now,
                expires_at=now + SESSION_LIFETIME,
                user_agent=session.user_agent,
                tailnet_identity=session.tailnet_identity,
            )
            self._write(updated)
            return updated

    def list_sessions(self) -> list[Session]:
        """All unexpired sessions, newest first."""
        now = self._clock()
        sessions: list[Session] = []
        with self._lock:
            if not self._dir.is_dir():
                return sessions
            for path in sorted(self._dir.glob("*.json")):
                session = self._read(path)
                if session is not None and not session.is_expired(now):
                    sessions.append(session)
        sessions.sort(key=lambda item: item.last_seen, reverse=True)
        return sessions

    def revoke(self, session_id: str) -> bool:
        """Delete one session. True when it existed."""
        path = self._path(session_id)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            self._logger.warning("could not revoke session %s: %s", session_id, exc)
            return False

    def revoke_all(self) -> int:
        """Delete every session. Returns the count removed."""
        if not self._dir.is_dir():
            return 0
        removed = 0
        for path in self._dir.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                self._logger.warning("could not remove session file %s: %s", path, exc)
        return removed

    def purge_expired(self) -> int:
        """Delete expired sessions. Returns the count removed."""
        now = self._clock()
        removed = 0
        with self._lock:
            if not self._dir.is_dir():
                return 0
            for path in self._dir.glob("*.json"):
                session = self._read(path)
                if session is not None and session.is_expired(now):
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        pass
        return removed


class LockoutTracker:
    """In-memory login lockout: 5 failures in 15 minutes from one
    source locks that source out for 15 minutes. A restart clears
    attack state (and the counters), which is fine for v1."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or _utcnow
        self._lock = threading.Lock()
        self._failures: dict[str, list[datetime]] = {}
        self._locked_until: dict[str, datetime] = {}

    def is_locked_out(self, source: str) -> bool:
        now = self._clock()
        with self._lock:
            until = self._locked_until.get(source)
            if until is None:
                return False
            if now >= until:
                del self._locked_until[source]
                self._failures.pop(source, None)
                return False
            return True

    def record_failure(self, source: str) -> bool:
        """Record one failure. Returns True when this failure trips the
        lockout for the source."""
        now = self._clock()
        with self._lock:
            recent = [
                moment
                for moment in self._failures.get(source, [])
                if now - moment < LOCKOUT_WINDOW
            ]
            recent.append(now)
            self._failures[source] = recent
            if len(recent) >= LOCKOUT_THRESHOLD:
                self._locked_until[source] = now + LOCKOUT_DURATION
                return True
            return False

    def record_success(self, source: str) -> None:
        """Clear the failure history for a source after a good login."""
        with self._lock:
            self._failures.pop(source, None)
            self._locked_until.pop(source, None)
