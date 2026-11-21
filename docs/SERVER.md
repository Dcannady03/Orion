# Orion Server — Persistent Runtime and Local API

> **Phase B: authenticated, remotely reachable over the tailnet.** The server
> binds loopback only and serves **no** endpoint without a session cookie
> (except the login endpoint itself). Remote access goes through Tailscale
> Serve, which terminates TLS and proxies to loopback — Orion never binds a
> network interface and is never exposed to the public internet (no Funnel,
> no port forwarding).

## Quick start

```powershell
# 1. Set the server password (local only, hidden prompt, min 12 characters).
python -m orion.main --server-setup

# 2. Tell Orion the Tailscale Serve hostname (one line in ~/.orion/config.yaml).
#    server:
#      tailscale_host: "daniels-server.tail807316.ts.net"

# 3. Start the server.
python -m orion.main --server

# 4. From any device on the tailnet, open the Serve URL and log in.
#    https://daniels-server.tail807316.ts.net/api/healthz
```

`--server` starts Orion headless: it builds the same service graph as the
console, starts the HTTP listener, and skips the console render and REPL.
Pass `--discord` alongside `--server` to run the Discord bot beside it, the
same as in console mode. The process runs in the foreground; stop it with
Ctrl+C. (There is no scheduled task at boot yet — that is Phase D.)

The server refuses to start in two cases:

- **First Contact has not been completed** (`--server` cannot run the
  interactive onboarding flow). Run `python -m orion.main --first-contact`
  first.
- **Another Orion process is already running** — see the singleton lock below.

`--server` and `--first-contact` cannot be combined.

## Authentication

A deployed server never serves an unauthenticated mutation endpoint, even on
`127.0.0.1` (§3.3 invariant). Every `/api/*` endpoint requires a session
cookie — including `GET /api/healthz` — except `POST /api/auth/login`.

### Setting the password

```powershell
python -m orion.main --server-setup
```

The setup command is **local-only**: run it on the server itself (via SSH or
RustDesk). It prompts for the password with hidden input (twice), hashes it
with scrypt, and writes the hash directly to `~/.orion/server/auth.json`.
There is no setup token, no web setup page, and no `POST /api/auth/setup`
endpoint. The command runs without the singleton lock and without starting
Orion, so it works while the server is running.

- Minimum password length: 12 characters.
- The password is never stored in plaintext — not in config, not in the
  Vault, not in any log.
- `auth.json` is written atomically with owner-only permissions
  (best-effort on Windows).
- Changing the password signs out every device: all sessions are revoked and
  the CSRF secret rotates.
- When stdin is not a terminal (piped input, a non-interactive SSH command),
  the prompt reads the password from stdin instead of hiding it — on Windows,
  `getpass` would otherwise block forever with no console attached.

### Setup mode

When no password exists yet, the server starts and answers **every** endpoint
with `503 setup_required` (login included), with instructions to run the
setup command. The server log says `SETUP MODE` loudly. Set the password
while the server runs — the next login works with no restart.

### Sessions

`POST /api/auth/login` with `{"password": "..."}` issues a session cookie
and returns the CSRF token:

```json
{
  "status": "success",
  "message": "Logged in.",
  "data": {
    "session_id": "0123456789abcdef",
    "expires_at": "2026-11-27T18:00:00Z",
    "csrf_token": "0123456789abcdef..."
  }
}
```

- The cookie (`orion_session`) is HttpOnly, SameSite=Lax, and a session
  cookie (no Max-Age) — the browser drops it on close and the server-side
  7-day sliding expiry governs. The expiry slides forward on activity (the
  record is rewritten at most once per 60 seconds per session).
- Sessions are stored server-side under `~/.orion/server/sessions/`
  (bounded at 20, expiring), with **only the SHA-256 hash** of each session
  token on disk — a stolen session file yields no usable token.
- The `Secure` cookie flag is explicit config (`server.secure_cookies`,
  default `true`): behind Serve, Orion sees plain loopback HTTP (TLS
  terminates at Serve), so the flag cannot be inferred from the socket. Set
  it `false` only for loopback HTTP development; the server logs a warning
  when it is disabled.

| Method | Path | Behavior |
| ------ | ---- | -------- |
| POST | `/api/auth/login` | Log in; issues the session cookie + CSRF token |
| POST | `/api/auth/logout` | Revoke the current session, clear its cookie |
| GET | `/api/auth/me` | Session info + a fresh CSRF token (re-fetch after reload) |
| GET | `/api/auth/sessions` | List live sessions (the caller's is marked `current`) |
| POST | `/api/auth/sessions/{id}/revoke` | Revoke one session |
| POST | `/api/auth/revoke-all` | Revoke every session (including the caller's) and rotate the CSRF secret |

### CSRF + Origin/Host validation

Mutating requests (POST/PUT/PATCH/DELETE) need a CSRF token in the
`X-CSRF-Token` header, HMAC-bound to the authenticated session
(HMAC-SHA256 of the server secret and the session ID; the secret lives in
`auth.json` and rotates on revoke-all and on password change). A matching
cookie alone is insufficient, and a token bound to another session is
rejected (`403 csrf_invalid`). Reads (GET) need only the session cookie.
Login needs no CSRF token (there is no session yet).

The `Host` header is validated on every request and the `Origin` header when
present, against the allowlist: localhost variants (`127.0.0.1`, `::1`,
`localhost`) plus `server.tailscale_host` (the Serve hostname). Anything
else is `403 forbidden_host` / `403 forbidden_origin`.

Gate order: setup mode, then Host/Origin, then session, then CSRF.

### Lockout

5 failed logins in 15 minutes from one source locks that source out for 15
minutes (`429 locked_out`); a successful login clears the failures. The
counters are in-memory and re-created on restart. Behind Serve every request
arrives from loopback, so a lockout covers all remote clients — acceptable
for a single-user system, and it stops brute force cold.

### Audit

Every auth event — login success/failure, lockout, setup, logout, revoke,
revoke-all, password change — is recorded in the recovery history (kind
`server-auth`, viewable with the recovery-history tools) with the source
address and the tailnet identity (`user@device`, best-effort from the local
Tailscale daemon) when available. Audit failures never break the auth path.
Gate rejections (401/403 on ordinary endpoints) are in the access log, not
the recovery history.

Tailnet identity comes from the local Tailscale daemon only
(`tailscale status --json`, cached 5 minutes). Client-supplied identity
headers are never accepted, and the identity is audit context, never an
auth decision.

### Recovery (lost password)

Delete `~/.orion/server/auth.json` and re-run the setup command. The server
returns to setup mode immediately — no restart needed, and no sessions
survive (the password change revokes them all). Losing the password is
recoverable only with server access, by design: there is no email reset, no
security question, and no backdoor.

## Remote access (Tailscale Serve)

Orion binds loopback only. Remote access goes through Tailscale Serve, which
terminates TLS at the tailnet hostname and proxies to `127.0.0.1:8000`:

```powershell
# One-time setup on the server (persists in the Tailscale daemon).
tailscale serve --bg 8000

# Check it.
tailscale serve status
```

If Serve has never been enabled on the tailnet, the command prints an admin
link (`https://login.tailscale.com/f/serve?...`) — a tailnet admin must open
it once to enable Serve, then re-run the command.

The Serve URL is **https://daniels-server.tail807316.ts.net** (the tailnet
hostname from `tailscale status`). Serve stays configured when Orion is
stopped; requests just fail to connect until the server starts again.

The server must know the Serve hostname to accept its Host/Origin — set it
in `~/.orion/config.yaml`:

```yaml
server:
  tailscale_host: "daniels-server.tail807316.ts.net"
```

(Without it, requests through Serve are rejected with `403 forbidden_host`.)

### Phone setup

1. Install Tailscale on the phone and sign in to the same tailnet.
2. Open `https://daniels-server.tail807316.ts.net/api/healthz` in the browser —
   an unauthenticated request answers `401`, which proves the tunnel works.
3. Log in at `POST /api/auth/login` (the web Command Center in Phase C will
   be the real phone UI; until then the API is the interface).

## Singleton lock

Orion is a single-writer system: the Command Center store forbids two
simultaneous writers. Every Orion process — console and server alike — takes a
**lifetime-held OS file lock** at startup, before anything else:

- Lock file: `~/.orion/server.lock` (empty; never read).
- Windows: `msvcrt.locking` on the first byte. POSIX: `fcntl.flock`
  (`LOCK_EX | LOCK_NB`).
- The lock is held for the whole process lifetime and released on graceful
  shutdown. The operating system releases it automatically when the process
  dies, including on abrupt termination — there is no stale-lock cleanup and
  no takeover protocol.
- A second process refuses to start with a clear message and exits with code
  2. Diagnostic metadata (PID, mode, started-at) is written to the sibling
  file `~/.orion/server.lock.json` for humans only; it is never consulted for
  ownership. (The metadata lives in a sibling file because a locked byte
  range cannot be read through a second handle on Windows.)

This lock is unrelated to the Mission Coordinator's operation lock files
(`orion/application/missions/`), which are operation-scoped, O_EXCL-created,
and content-match-guarded. Those are unchanged.

## Configuration

```yaml
# config/default.yaml — the server: root
server:
  enabled: false
  host: 127.0.0.1
  port: 8000
  auth_required: true
  secure_cookies: true
  tailscale_host: ""
  shutdown_drain_seconds: 30
  max_concurrent_requests: 8
  max_request_bytes: 1048576
  max_response_bytes: 4194304
  request_timeout_seconds: 60
```

`server` is a persistent root in `orion/core/config.py`, so overrides in
`~/.orion/config.yaml` survive upgrades. Notes:

- `enabled` is a deployment flag for later phases; `--server` starts the
  listener regardless of it.
- `host` must be a loopback address (`127.0.0.1`, `::1`, or `localhost`);
  anything else is rejected at startup. Remote access is Serve's job, never
  a wider bind.
- `port: 0` binds an ephemeral port (used by tests).
- `auth_required: true` is the default. The server refuses to start only
  when auth is explicitly disabled in config without the per-process
  development opt-out — that combination means someone turned auth off in
  the config file, which must never happen for a deployed server.
- `ORION_SERVER_AUTH=off` is the dev/testing exception only: the server runs
  unauthenticated (no session gate; the auth endpoints answer `503`). Never
  for a deployed server.
- `secure_cookies` and `tailscale_host` are documented in Authentication
  and Remote access above.
- `shutdown_drain_seconds` bounds how long Ctrl+C waits for detached work
  before the process exits anyway (see Lifecycle).

## HTTP transport

The transport is aiohttp (`orion/server/aiohttp_server.py`), on an asyncio
event loop in a background daemon thread (the Discord precedent). Synchronous
application-layer calls go through `asyncio.to_thread`. The dispatch table
and the typed handler calls live in the transport-independent `LocalApi`
class (`orion/server/api.py`); the HTTP layer only translates between
sockets and `ApiRequest`/`ApiResponse`.

Bounds and behavior:

- **Concurrency:** a semaphore caps simultaneous in-flight requests at
  `max_concurrent_requests`; excess requests get `429` immediately.
- **Request size:** bodies larger than `max_request_bytes` get `413`.
- **Response size:** a response larger than `max_response_bytes` is never
  truncated — the request fails with `500` instead of returning partial JSON.
- **Per-request timeout:** a request that runs longer than
  `request_timeout_seconds` gets `504`. The underlying work is not cancelled;
  long work is detached by design (see below).
- **Bind retry:** startup retries the bind a few times with a short backoff
  (slow boot), then fails fast with a clear log line. The site sets
  `reuse_address=False`, because Windows `SO_REUSEADDR` would let a second
  socket bind an actively listening port.
- **Access log:** one structured JSON line per request (method, path, status,
  duration, request ID) in the server log. Bodies and tokens are never logged.

## API reference

Base URL: `http://127.0.0.1:8000` locally, or the Serve URL remotely
(`https://daniels-server.tail807316.ts.net`). All responses are JSON in the
standard `ApplicationResult` envelope:

```json
{
  "status": "success",
  "message": "...",
  "data": { "...": "..." },
  "warnings": [],
  "errors": [],
  "next_actions": []
}
```

`data` is `null` on failure; `errors` carries the failure detail. HTTP status
codes follow the typed error: `400` malformed input, `401` missing/invalid
session (or wrong password on login), `403` CSRF/Origin/Host rejection,
`404` unknown route or record, `409` stale preview / conflicting state,
`422` validation failure, `429` concurrency limit or login lockout, `500`
unexpected error (no tracebacks leak to the client), `503` setup mode (no
password set) or auth disabled in dev mode, `504` request timeout.

Every endpoint requires a session cookie except `POST /api/auth/login` (see
Authentication above). Mutating endpoints additionally require the
`X-CSRF-Token` header.

### Read endpoints

| Method | Path | Returns |
| ------ | ---- | ------- |
| GET | `/api/snapshot` | Command Center snapshot |
| GET | `/api/jobs` | Job list (`?limit=`, `?cursor=`) |
| GET | `/api/jobs/{id}` | One job |
| GET | `/api/activity` | Activity events (`?limit=`, `?cursor=`) |
| GET | `/api/doctor` | Command Center doctor report |
| GET | `/api/health` | Service health |
| GET | `/api/logs` | Recent log entries (`?limit=`, `?cursor=`) |
| GET | `/api/missions` | Mission list (`?limit=`, `?cursor=`) |
| GET | `/api/missions/{id}` | One mission |
| GET | `/api/missions/{id}/history` | Attempt history (`?limit=`, `?cursor=`) |
| GET | `/api/team/tasks/{id}` | One Team task (includes plan hash) |
| GET | `/api/team/runs/{id}` | One Team run |
| GET | `/api/healthz` | Liveness + startup reconciliation report |
| GET | `/api/auth/me` | Session info + CSRF token |
| GET | `/api/auth/sessions` | Live sessions |

List endpoints take simple `cursor`/`limit` parameters now (cursor = the last
seen record ID; an unknown cursor is `400`). Durable event cursors are a
later phase.

### Mutation endpoints

| Method | Path | Behavior |
| ------ | ---- | -------- |
| POST | `/api/jobs` | Create a job (draft, inert) |
| POST | `/api/jobs/{id}/launch/preview` | Launch dry-run (no writes, no provider calls) |
| POST | `/api/jobs/{id}/launch` | Launch — **202**, detached (see below) |
| POST | `/api/jobs/{id}/sync` | Sync job state from its Team task |
| POST | `/api/jobs/{id}/cancel` | Cancel a job |
| POST | `/api/missions/{id}/next` | Next-action preview for a mission |
| POST | `/api/missions/{id}/advance` | Advance a mission — fast ops inline (`200`), long ops **202** |
| POST | `/api/missions/{id}/resolve/preview` | Read-only uncertainty-resolution preview |
| POST | `/api/missions/{id}/resolve` | Resolve an uncertain mission operation |
| POST | `/api/team/tasks/{id}/approve` | Approve a Team task (decision + optional note) |
| POST | `/api/auth/login` | Log in (no session/CSRF needed) |
| POST | `/api/auth/logout` | Log out |
| POST | `/api/auth/sessions/{id}/revoke` | Revoke one session |
| POST | `/api/auth/revoke-all` | Revoke all sessions, rotate the CSRF secret |

Every handler calls a typed service or application-coordinator method
directly. The `cc` handler's `shlex` text parser is never invoked — the API
is a second renderer on the same typed boundary, not a shell over the CLI.
Approval semantics are unchanged: mission advances still go through the
preview → state-bound token → confirm flow, and the token in the request must
match the mission's current preview or the advance is rejected with `409`.

### The 202 contract (long work)

Mutations that start long work — job-launch execution and mission advances
that dispatch a Team operation (`team.implement`, `team.validate`,
`team.documentation_review`) — return `202 Accepted` **immediately**, with the
operation ID and a poll URL in `data`:

```json
{
  "status": "success",
  "message": "Launch accepted; execution running detached.",
  "data": {
    "operation_id": "op-...",
    "job_id": "job-...",
    "team_task_id": "task-...",
    "poll_url": "/api/jobs/job-..."
  }
}
```

Clients poll the persistent record via the GET endpoints. Fast advances
(`team.approve`, `team.final_review` — record writes only) run inline and
return `200` with the updated mission.

**Durable execution.** Before the 202 is returned, the accepted operation and
its identity are durably recorded — the mission audit (reserved state) for
mission advances, the job's Team-task link for job launches. Disconnecting a
client never cancels, duplicates, or re-authorizes an operation.

**Startup reconciliation.** On startup the server reconciles in-flight
detached operations from the durable records and classifies each as
completed, failed, or uncertain. Uncertain work is **never automatically
replayed**. It surfaces in the `/api/healthz` report and is resolved by the
operator through the Mission Coordinator's resolve workflow
(`POST /api/missions/{id}/resolve/preview` then `.../resolve` with a finding
of `ran`, `did-not-run`, or `cannot-determine`) — the same workflow the
console uses. There is no second execution path.

## Lifecycle

- **Graceful shutdown:** Ctrl+C (or Ctrl+Break on Windows) triggers a bounded
  drain — up to `shutdown_drain_seconds` for detached work to finish — then
  the listener stops, the singleton lock is released, and the process exits 0.
  SIGTERM is handled where the platform delivers it, but Orion does not depend
  on it: Windows Task Scheduler termination is closer to TerminateProcess, so
  graceful shutdown there is best-effort.
- **Abrupt termination:** the OS releases the singleton lock immediately, so
  the next start acquires it with no manual cleanup. All durable stores are
  atomic temp-file-plus-rename (Command Center YAML, Team tasks, goals,
  agents, missions, coordination records), so a crash mid-write leaves every
  store consistent; the Command Center activity log tolerates and repairs a
  torn final line. In-flight detached work is picked up by startup
  reconciliation (above), never silently dropped and never silently replayed.
- **Logging:** server output goes to `~/.orion/logs/server.log` (rotating,
  1 MB × 3 backups) and to stderr. Use `ORION_USER_DATA` to point Orion at a
  temporary user-data root for testing — never experiment against the real
  `~/.orion` data.

## What is not in Phase B

The web UI (Phase C), the scheduled task at boot (Phase D), and durable
event cursors are still later phases. The server is started manually and
stopped with Ctrl+C; the API is the interface until the Command Center UI
lands.
