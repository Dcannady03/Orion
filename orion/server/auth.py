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
import ipaddress
import json
import logging
import os
import secrets
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping
from urllib.parse import urlparse

from orion.application.observability.recovery import RecoveryHistoryStore
from orion.server.errors import ApiError

if TYPE_CHECKING:
    from orion.server.api import ApiRequest

# Localhost is always an allowed Host/Origin (loopback dev + Serve
# proxying to loopback); the Tailscale Serve hostname joins it when
# configured (server.tailscale_host).
LOCALHOST_NAMES = frozenset({"127.0.0.1", "::1", "localhost"})
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

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


LOGIN_PATH = "/api/auth/login"


def _cookie_value(headers: Mapping[str, str], name: str) -> str:
    """One cookie's value from a Cookie header (keys are lowercased)."""
    raw = headers.get("cookie", "")
    for part in raw.split(";"):
        part = part.strip()
        if part.startswith(name + "="):
            return part[len(name) + 1:].strip().strip('"')
    return ""


def _hostname_of(host: str) -> str:
    """The hostname part of a Host header value (port stripped)."""
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host
    return host.split(":")[0]


# Tailscale assigns tailnet IPs from the CGNAT range.
TAILNET_NETWORK = ipaddress.ip_network("100.64.0.0/10")
LOOPBACK_IPS = frozenset({"127.0.0.1", "::1"})


def _is_tailnet_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value.strip()) in TAILNET_NETWORK
    except ValueError:
        return False


def _run_tailscale_status() -> dict:
    """`tailscale status --json`, parsed. {} on any failure."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class TailnetIdentity:
    """Best-effort tailnet identity for audit records.

    Maps tailnet IPs to ``user@device`` from the local Tailscale
    daemon (`tailscale status --json`), cached for 5 minutes. Never a
    hard dependency: any failure yields "" and the request proceeds
    with the IP alone. Identity comes from the local daemon ONLY —
    client-supplied identity headers are never accepted (§3.3
    proxy-trust rule), and the identity is audit context, never an
    auth decision.
    """

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(minutes=5),
        runner: Callable[[], dict] | None = None,
    ) -> None:
        self._ttl = ttl
        self._runner = runner or _run_tailscale_status
        self._lock = threading.Lock()
        self._cached_at: datetime | None = None
        self._by_ip: dict[str, str] = {}

    def lookup(self, ip: str) -> str:
        """``user@device`` for a tailnet IP, or "" when unknown."""
        if not ip:
            return ""
        with self._lock:
            now = _utcnow()
            if self._cached_at is None or now - self._cached_at >= self._ttl:
                self._refresh_locked()
            return self._by_ip.get(ip.strip(), "")

    def _refresh_locked(self) -> None:
        try:
            data = self._runner()
        except Exception:  # noqa: BLE001 - identity must never break auth
            data = {}
        by_ip: dict[str, str] = {}
        if isinstance(data, dict):
            peers = data.get("Peer", {})
            users = data.get("User", {})
            nodes = list(peers.values()) if isinstance(peers, dict) else []
            self_node = data.get("Self", {})
            if isinstance(self_node, dict):
                nodes.append(self_node)
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                hostname = str(node.get("HostName", ""))
                user = (
                    users.get(str(node.get("UserID", "")), {})
                    if isinstance(users, dict)
                    else {}
                )
                login = (
                    str(user.get("LoginName", ""))
                    if isinstance(user, dict)
                    else ""
                )
                identity = (
                    f"{login}@{hostname}"
                    if login and hostname
                    else (hostname or login)
                )
                if not identity:
                    continue
                for address in node.get("TailscaleIPs") or []:
                    by_ip[str(address)] = identity
        self._by_ip = by_ip
        self._cached_at = _utcnow()


@dataclass
class AuthResult:
    """What an auth endpoint returns: status, envelope data, headers."""

    status: int
    data: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


class Authenticator:
    """The HTTP auth gate: sessions, CSRF, lockout, audit.

    Owns the credential store, the session store, and the lockout
    tracker. LocalApi calls :meth:`authenticate` before routing every
    request (the login endpoint excepted) and delegates the six auth
    endpoints to the methods below. Raises ApiError for every failure;
    LocalApi maps those onto the standard envelope.
    """

    def __init__(
        self,
        paths: object,
        config: object,
        *,
        logger: logging.Logger | None = None,
        clock: Callable[[], datetime] | None = None,
        tailnet: TailnetIdentity | None = None,
    ) -> None:
        self._paths = paths
        self._credentials = CredentialStore(paths)
        self._sessions = SessionStore(paths, clock=clock)
        self._lockout = LockoutTracker(clock=clock)
        self._tailnet = tailnet or TailnetIdentity()
        # Behind Tailscale Serve Orion sees plain loopback HTTP (TLS
        # terminates at Serve), so the Secure flag cannot be inferred
        # from the socket — it is explicit config, safe by default.
        self._secure_cookies = bool(getattr(config, "secure_cookies", True))
        allowed = set(LOCALHOST_NAMES)
        tailscale_host = str(
            getattr(config, "tailscale_host", "") or ""
        ).strip().lower()
        if tailscale_host:
            allowed.add(tailscale_host)
        self._allowed_hostnames = frozenset(allowed)
        self._logger = logger or logging.getLogger("orion.server.auth")
        self._clock = clock or _utcnow

    @property
    def setup_mode(self) -> bool:
        """True when no password exists: every endpoint answers 503."""
        return not self._credentials.exists

    # -- request context ----------------------------------------------

    @staticmethod
    def _source(request: "ApiRequest") -> str:
        """The lockout/audit source: the direct peer (the socket).

        Behind Serve every request arrives from loopback, so a lockout
        covers all remote clients — acceptable for a single-user system
        and it stops brute force cold.
        """
        return request.remote_addr or "unknown"

    def _tailnet_identity(self, request: "ApiRequest") -> str:
        """Best-effort ``user@device`` for the real client, or "".

        Behind Serve the direct peer is loopback, so the client IP is
        the LAST X-Forwarded-For entry (the one Serve appended) when
        it is a tailnet IP; otherwise the remote address itself when
        it is a tailnet IP. Audit context only, never an auth input.
        """
        remote = (request.remote_addr or "").strip()
        candidate = ""
        if remote in LOOPBACK_IPS:
            forwarded = request.headers.get("x-forwarded-for", "")
            entries = [entry.strip() for entry in forwarded.split(",")]
            entries = [entry for entry in entries if entry]
            if entries and _is_tailnet_ip(entries[-1]):
                candidate = entries[-1]
        elif _is_tailnet_ip(remote):
            candidate = remote
        return self._tailnet.lookup(candidate) if candidate else ""

    def _audit(
        self,
        request: "ApiRequest",
        event: str,
        target: str,
        *,
        detail: str = "",
        status: str = "success",
    ) -> None:
        source = self._source(request)
        identity = self._tailnet_identity(request)
        where = f"{source} ({identity})" if identity else source
        record_auth_event(
            self._paths,
            event,
            target,
            detail=f"{detail} [from {where}]" if detail else f"from {where}",
            status=status,
            logger=self._logger,
        )

    # -- the gate ----------------------------------------------------

    def authenticate(self, request: "ApiRequest") -> Session | None:
        """Verify one request. Returns the session (None for login).

        Raises ApiError: 503 setup_required when no password exists,
        403 forbidden_host/forbidden_origin for a Host or Origin
        outside the allowlist, 401 unauthenticated when the session
        cookie is missing, unknown, or expired, and 403
        csrf_required/csrf_invalid when a mutating request lacks a
        valid session-bound CSRF token.
        """
        if self.setup_mode:
            raise ApiError(
                503,
                "Server password is not set. Run "
                "`python -m orion.main --server-setup` on the server "
                "to set one.",
                code="setup_required",
            )
        self._check_host(request)
        self._check_origin(request)
        if request.method == "POST" and request.path == LOGIN_PATH:
            return None
        token = _cookie_value(request.headers, SESSION_COOKIE_NAME)
        session = self._sessions.verify(token) if token else None
        if session is None:
            raise ApiError(
                401,
                "Authentication required.",
                code="unauthenticated",
            )
        self._check_csrf(request, session)
        self._sessions.touch(session.session_id)
        return session

    def _check_host(self, request: "ApiRequest") -> None:
        host = request.headers.get("host", "")
        if not host or _hostname_of(host) not in self._allowed_hostnames:
            raise ApiError(
                403,
                "This request's Host is not allowed.",
                code="forbidden_host",
            )

    def _check_origin(self, request: "ApiRequest") -> None:
        origin = request.headers.get("origin", "")
        if not origin:
            return
        try:
            hostname = urlparse(origin).hostname or ""
        except ValueError:
            raise ApiError(
                403,
                "The Origin header is not a valid URL.",
                code="forbidden_origin",
            ) from None
        if hostname.lower() not in self._allowed_hostnames:
            raise ApiError(
                403,
                "This request's Origin is not allowed.",
                code="forbidden_origin",
            )

    def _check_csrf(self, request: "ApiRequest", session: Session) -> None:
        if request.method not in MUTATING_METHODS:
            return
        token = request.headers.get(CSRF_HEADER_NAME.lower(), "")
        if not token:
            raise ApiError(
                403,
                "A CSRF token is required for this request.",
                code="csrf_required",
            )
        if not verify_csrf_token(
            token, session.session_id, self._credentials.csrf_secret
        ):
            raise ApiError(
                403,
                "The CSRF token is invalid or bound to another session.",
                code="csrf_invalid",
            )

    # -- endpoints ---------------------------------------------------

    def login(self, request: "ApiRequest") -> AuthResult:
        """Verify the password, issue a session cookie + CSRF token."""
        body = request.body
        if body is None:
            body = {}
        if not isinstance(body, Mapping):
            raise ApiError.bad_request("Request body must be a JSON object.")
        password = body.get("password", "")
        if not isinstance(password, str) or not password:
            raise ApiError.bad_request("A password is required.")
        source = self._source(request)
        if self._lockout.is_locked_out(source):
            raise ApiError(
                429,
                "Too many failed logins from this source. "
                "Try again in 15 minutes.",
                code="locked_out",
            )
        if not self._credentials.verify_password(password):
            tripped = self._lockout.record_failure(source)
            self._audit(
                request, "login", "server session",
                detail="Login failed (invalid password).",
                status="failed",
            )
            if tripped:
                self._audit(
                    request, "lockout", source,
                    detail=(
                        "Source locked out for 15 minutes after "
                        f"{LOCKOUT_THRESHOLD} failed logins."
                    ),
                    status="failed",
                )
            raise ApiError(
                401, "Invalid password.", code="invalid_credentials"
            )
        self._lockout.record_success(source)
        session, token = self._sessions.create(
            user_agent=str(request.headers.get("user-agent", "")),
            tailnet_identity=self._tailnet_identity(request),
        )
        csrf = csrf_token_for(
            session.session_id, self._credentials.csrf_secret
        )
        self._audit(
            request, "login", "server session",
            detail=f"Login succeeded; session {session.session_id}.",
        )
        return AuthResult(
            status=200,
            data={
                "session_id": session.session_id,
                "expires_at": _iso(session.expires_at),
                "csrf_token": csrf,
            },
            headers={"Set-Cookie": self._session_cookie(token)},
        )

    def logout(self, request: "ApiRequest") -> AuthResult:
        """Revoke the current session and clear its cookie."""
        session = request.session
        if session is not None:
            self._sessions.revoke(session.session_id)
            self._audit(
                request, "logout", "server session",
                detail=f"Session {session.session_id} revoked via logout.",
            )
        return AuthResult(
            status=200,
            data={},
            headers={"Set-Cookie": self._clear_cookie()},
        )

    def me(self, request: "ApiRequest") -> AuthResult:
        """Session info for the caller, plus a fresh CSRF token."""
        session = request.session
        if session is None:  # pragma: no cover - the gate guarantees it
            raise ApiError(401, "Authentication required.",
                           code="unauthenticated")
        data = session.to_dict()
        data["csrf_token"] = csrf_token_for(
            session.session_id, self._credentials.csrf_secret
        )
        return AuthResult(status=200, data=data)

    def list_sessions(self, request: "ApiRequest") -> AuthResult:
        """All live sessions; the caller's is marked current."""
        current = request.session.session_id if request.session else ""
        sessions = [
            {**item.to_dict(), "current": item.session_id == current}
            for item in self._sessions.list_sessions()
        ]
        return AuthResult(status=200, data={"sessions": sessions})

    def revoke_session(self, request: "ApiRequest") -> AuthResult:
        """Revoke one session by id."""
        session_id = request.path_params.get("session_id", "")
        if not session_id:
            raise ApiError.bad_request("A session id is required.")
        if not self._sessions.revoke(session_id):
            raise ApiError.not_found(f"Unknown session: {session_id}")
        self._audit(
            request, "revoke", "server session",
            detail=f"Session {session_id} revoked.",
        )
        return AuthResult(status=200, data={"revoked": session_id})

    def revoke_all(self, request: "ApiRequest") -> AuthResult:
        """Revoke every session (including the caller's) and rotate the
        CSRF secret, so every outstanding CSRF token dies with them."""
        removed = self._sessions.revoke_all()
        self._credentials.rotate_csrf_secret()
        self._audit(
            request, "revoke-all", "server sessions",
            detail=(
                f"{removed} session(s) revoked; CSRF secret rotated."
            ),
        )
        return AuthResult(
            status=200,
            data={"revoked_sessions": removed},
            headers={"Set-Cookie": self._clear_cookie()},
        )

    # -- cookies ------------------------------------------------------

    def _session_cookie(self, token: str) -> str:
        # A session cookie (no Max-Age): the browser drops it on close
        # and the server-side 7-day sliding expiry governs.
        parts = [
            f"{SESSION_COOKIE_NAME}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
        ]
        if self._secure_cookies:
            parts.append("Secure")
        return "; ".join(parts)

    def _clear_cookie(self) -> str:
        parts = [
            f"{SESSION_COOKIE_NAME}=",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            "Max-Age=0",
        ]
        if self._secure_cookies:
            parts.append("Secure")
        return "; ".join(parts)
