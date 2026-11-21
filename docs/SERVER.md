# Orion Server — Persistent Runtime and Local API

> **Development-only prototype.** The Phase A server binds loopback only and
> serves **unauthenticated** requests. It exists so a local interface (and, in
> later phases, a web Command Center) can drive Orion without the console REPL.
> A deployed server never serves an unauthenticated mutation endpoint, even on
> `127.0.0.1` — authentication lands in Phase B, and until then the server must
> not be exposed beyond the local machine (no port forwarding, no remote
> proxying, no scheduled task at boot).

## Running the server

```powershell
python -m orion.main --server
```

`--server` starts Orion headless: it builds the same service graph as the
console, starts the HTTP listener, and skips the console render and REPL.
Pass `--discord` alongside `--server` to run the Discord bot beside it, the
same as in console mode. The process runs in the foreground; stop it with
Ctrl+C.

The server refuses to start in two cases:

- **First Contact has not been completed** (`--server` cannot run the
  interactive onboarding flow). Run `python -m orion.main --first-contact`
  first.
- **Another Orion process is already running** — see the singleton lock below.

`--server` and `--first-contact` cannot be combined.

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
  anything else is rejected at startup.
- `port: 0` binds an ephemeral port (used by tests).
- `auth_required: true` is the default, and Phase A has **no auth
  implementation** — so a server started with the default config refuses to
  serve, by design. For local development and testing only, set the
  environment variable `ORION_SERVER_AUTH=off` to run unauthenticated. That
  exception is dev/testing only and must never be used for a deployed server.
- `shutdown_drain_seconds` bounds how long Ctrl+C waits for detached work
  before the process exits anyway (see Lifecycle).

## HTTP transport

The Phase A transport is the standard library's `ThreadingHTTPServer` in a
background daemon thread, bound loopback-only. It is a prototype: Phase B
replaces the transport with aiohttp, so the dispatch table and the typed
handler calls live in the transport-independent `LocalApi` class
(`orion/server/api.py`) and the HTTP layer only translates between sockets
and `ApiRequest`/`ApiResponse`.

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
  (slow boot), then fails fast with a clear log line. On Windows the listener
  uses `SO_EXCLUSIVEADDRUSE`, because Windows `SO_REUSEADDR` would let a
  second socket bind an actively listening port.
- **Access log:** one structured JSON line per request (method, path, status,
  duration, request ID) in the server log. Bodies and tokens are never logged.

## API reference

Base URL: `http://127.0.0.1:8000`. All responses are JSON in the standard
`ApplicationResult` envelope:

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
codes follow the typed error: `400` malformed input, `404` unknown route or
record, `409` stale preview / conflicting state, `422` validation failure,
`429` concurrency limit, `500` unexpected error (no tracebacks leak to the
client).

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

## What is not in Phase A

Authentication, remote access, the web UI, the scheduled task at boot, and
durable event cursors are all later phases. The Phase A server is a local
development prototype; treat it accordingly.
