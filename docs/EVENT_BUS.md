# Orion v0.8.4 Event Bus

## Purpose and boundary

The Event Bus makes significant Orion application facts observable without turning
those facts into commands:

```text
Application or lifecycle boundary
  -> authoritative domain transition
  -> immutable OrionEvent
  -> persist in Event Store
  -> synchronously notify observation-only subscribers
```

An event means that something happened. It is never permission to execute another
operation. The bus has no capability registry, provider, approval, job, agent,
workspace, execution-engine, Git, subprocess, or application-handler dependency.

There are no asynchronous workers, automatic reactions, Mission Engine hooks,
external streams, REST endpoints, WebSockets, Discord notifications, email
notifications, or voice notifications in v0.8.4.

## Event model

`OrionEvent` is a frozen, schema-versioned model:

| Field | Meaning |
| --- | --- |
| `event_id` | Unique `event-<uuid-hex>` identity |
| `event_type` | Stable lowercase `domain.resource.action` contract |
| `occurred_at` | ISO 8601 UTC timestamp |
| `source` | Application boundary that published the fact |
| `severity` | Diagnostic significance, never execution priority |
| `correlation_id` | Optional high-level activity grouping |
| `causation_id` | Optional ID of the directly causal Orion event |
| `subject_id` | Optional resource described by the event |
| `schema_version` | Event record schema; currently `1` |
| `data` | Explicitly selected JSON-safe lifecycle fields |
| `metadata` | Explicitly selected JSON-safe observation metadata |

Nested mappings are copied into immutable mapping proxies and sequences into tuples.
Mutating caller-owned dictionaries or lists after construction cannot change an
event. `to_dict()` returns detached ordinary JSON dictionaries and lists.

Event construction rejects:

- unknown event types;
- invalid severities or non-UTC timestamps;
- unknown persisted fields or unsupported schema versions;
- live Python objects such as paths, datetimes, enums, services, or exceptions;
- non-finite numbers, excessive nesting, oversized strings, or oversized records;
- sensitive field names and common secret-shaped values.

Unsupported values are never silently stringified.

## Naming and supported event types

The v0.8.4 public event contracts are:

```text
goal.plan.created
goal.proposal.created
goal.proposal.validated
goal.proposal.accepted
goal.proposal.rejected
goal.proposal.expired
goal.proposal.consumed
goal.proposal.failed
team.plan.created
```

Only these existing operations are instrumented. Command Center, agents, providers,
workspaces, and other domains do not emit Event Bus records yet.

## Severity

The stable severity values are:

- `debug`: diagnostic detail;
- `info`: routine successful state change;
- `notice`: important user-visible transition;
- `warning`: blocked or recoverable condition;
- `error`: failed operation;
- `critical`: integrity or system-level failure.

Severity affects observation and filtering only.

## Correlation and causation

Goal and proposal events use the authoritative `goal_id` as their correlation ID.
A Proposal acceptance is persisted first and then publishes
`goal.proposal.accepted`. Its event ID becomes the causation ID on the typed
`TeamPlanRequest`.

```text
goal.proposal.created       correlation = goal_id
goal.proposal.accepted      correlation = goal_id
  -> team.plan.created      correlation = goal_id
                            causation = accepted event ID
  -> proposal terminal      correlation = goal_id
                            causation = accepted event ID
```

Standalone AI Team plans use their Team task ID as correlation unless a typed caller
supplies an existing correlation. Separate `goal plan` and
`goal proposal create` commands each create a new Goal Plan, so Orion does not invent
a causal link between those independently requested commands.

## Event Factory and publisher

`EventFactory` owns type registration, ID creation, UTC timestamps, JSON validation,
defensive copying, and the configured byte limit. Injectable clocks and ID factories
keep tests deterministic.

`EventPublisher` is a narrow application helper. Callers select every safe payload
field explicitly; it does not reflect over domain objects or serialize complete
application models.

If event construction, persistence, or delivery reporting fails after a domain
operation succeeds:

1. the domain result is not rolled back;
2. no subscriber receives an unpersisted event;
3. the original `ApplicationResult` remains authoritative;
4. a bounded observability warning is attached;
5. successful event IDs may be exposed in `ApplicationResult.data.event_ids`.

## Synchronous publish semantics

With the default store enabled, `EventBus.publish()` performs:

```text
validate immutable event
  -> append and fsync JSONL record
  -> snapshot subscribers in registration order
  -> synchronously deliver EventDelivery(replayed=False)
  -> return EventPublishResult
```

A subscriber registered during delivery begins with the next event. Subscriber
return values are discarded. One subscriber exception becomes a bounded warning and
does not prevent later subscribers from receiving the event or alter the publisher's
domain result.

Recursive publication from the same subscriber delivery path is rejected before a
nested event can be persisted. This prevents an unbounded failure-event loop.

When `events.store_enabled` is deliberately false, the bus has no persistence layer
and only in-process observation remains. The default is `true`.

## Subscribers

Two observation-only subscribers exist:

- `DiagnosticEventLogger` writes concise IDs and type information through Python's
  `orion.events` logger. It does not print or publish another event.
- `InMemoryEventSubscriber` is a bounded observer intended primarily for tests and
  diagnostics.

Subscriptions have stable `subscription-<name>` IDs. Duplicate names are rejected.
The read-only CLI exposes only IDs and names, never subscriber objects.

## Persistence

Event records live outside the source repository:

```text
~/.orion/events/YYYY-MM-DD.jsonl
```

`ORION_USER_DATA` relocates the same root. Orion passes its installation root as a
forbidden storage root and never falls back to repository-local storage.

Properties:

- UTC date determines the daily filename;
- exactly one canonical, compact UTF-8 JSON object occupies each valid line;
- appends do not rewrite the daily log;
- a process-local lock and exclusive per-day cross-process lock serialize appends;
- one write, flush, and `fsync` complete each append;
- owner-only permissions are requested where the platform supports them;
- symlinked roots, logs, and lock files are rejected;
- each line is bounded by `events.max_event_bytes`;
- stale lock files fail closed after a bounded wait.

If a crash leaves a partial final line, the next append first writes a line
separator. History then reports and ignores the malformed record while preserving
the next valid event.

## History

`EventStore.history()` is read-only and returns newest-first records. The default
limit is `100`; the hard configured maximum is `1000`.

Filters are available for:

```text
event type
correlation ID
subject ID
source
severity
start timestamp
end timestamp
```

History scans daily logs newest-first and reads each file backwards in bounded
chunks. It does not load an unlimited log into memory. Malformed, oversized, unsafe,
or unsupported-version lines are skipped with bounded warnings rather than
reinterpreted.

## Replay

`EventBus.replay()` reads one bounded history selection and delivers it to one
explicit observer using `EventDelivery(replayed=True)`.

Replay:

- preserves original event IDs, timestamps, data, and metadata;
- delivers the selected window oldest-first for natural observation;
- never republishes or re-appends events;
- never registers the observer automatically;
- never invokes an application handler or domain service;
- returns only delivery counts, original event IDs, and warnings.

The persisted event is never modified to mark replay.

## Read-only CLI

```text
events status
events list
events list --type goal.proposal.accepted
events list --source goal_proposals
events list --severity warning
events list --limit 50
events show <event-id>
events correlation <correlation-id>
events subject <subject-id>
events types
events subscribers
```

There is no production CLI command for publishing an event. The CLI adapter parses,
calls `EventApplicationHandler`, and uses the shared `ApplicationResult` renderer.
All structured responses include `read_only: true`.

## Configuration

```yaml
events:
  enabled: true
  store_enabled: true
  max_event_bytes: 65536
  history_default_limit: 100
  history_max_limit: 1000
  diagnostic_logging: true
```

Only implemented settings are present. Automatic retention deletion is not
implemented or claimed.

## Safety guarantees

Event construction, persistence, history, replay, subscribers, the application
handler, and CLI:

- cannot launch agents or jobs;
- cannot invoke providers or execution engines;
- cannot create or consume approvals;
- cannot mutate workspaces;
- cannot run subprocesses or Git;
- cannot dispatch application requests;
- cannot advance Goal Proposals or AI Team workflows;
- cannot return commands or actions from subscribers;
- cannot accept arbitrary user-published lifecycle events.

The only domain activity in the integrated acceptance flow remains the one typed,
explicitly accepted `team.plan` operation already authorized by Goal Proposals.
Events merely record its transitions.

## Schema compatibility

Event schema version `1` is the only supported persisted version. Readers reject
future or unknown versions with warnings and never silently reinterpret them.
Future schema work must preserve existing event identities and public type meanings.

## Risks and future work

- JSONL files grow until an explicit future maintenance policy is implemented.
- A process crash can leave a malformed final line or stale lock.
- Synchronous subscribers add latency to publication, so observers must remain
  concise.
- Event type and schema changes require compatibility discipline.
- Only selected high-value boundaries emit events, leaving intentional
  observability gaps.
- Correlation cannot bridge independently created Goal Plans without a real shared
  activity identity.
- Legacy clients that parse messages should migrate to structured event fields.

The recommended next milestone is **Orion v0.8.5 — Mission Engine Phase 1**:
persistent mission lifecycle records and event-based observation only, with no
automatic progression.
