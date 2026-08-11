# Orion v0.8.5 Mission Engine Phase 1

## Purpose

The Mission Engine is Orion's durable answer to “Where are we now?” A Mission is a
restart-safe projection of one accepted Goal Proposal version and the authoritative
Event Bus facts correlated to it.

```text
Goal -> Goal Plan -> Goal Proposal -> Mission -> Event projection
```

Phase 1 is observation-only. It recommends a human-facing next command when a safe
one is known, but never runs that command or advances another domain.

## Goal, Proposal, and Mission

| Record | Question answered | Authority |
| --- | --- | --- |
| Goal Plan | What does Orion recommend? | Deterministic Goal Engine |
| Goal Proposal | What exact plan did the user accept? | Goal Proposal service and immutable plan hash |
| Mission | What authoritative lifecycle facts are currently observable? | Persisted proposal plus Event Store history |

A Mission does not replace a Goal Proposal, AI Team task, approval, run, or Command
Center job. Those systems remain authoritative and the Mission stores only stable
identity links and projection state.

## Identity and schema

Mission records use schema version `1` and a stable random ID:

```text
mission-<32 lowercase hex characters>
```

Every Mission permanently records its `goal_id`, `proposal_id`, and
`proposal_version`. Reconciliation cannot switch the Mission to another proposal or
version. Readers reject unknown future schemas instead of reinterpreting them.

The immutable JSON-safe models are:

- `Mission`: identity, safe goal context, projection, links, event cursor, warnings,
  and risks;
- `MissionLink`: link type, subject ID, authoritative source, and link timestamp;
- `MissionEventRef`: event ID, type, timestamp, source, and subject ID;
- `MissionProjection`: an in-memory deterministic replay result;
- `MissionValidation`: read-only integrity and staleness findings.

One Mission is allowed per Goal Proposal by default. Repeating `mission create` for
the same proposal returns the existing Mission and reconciles it; it never creates a
second identity.

## Creation

`mission create <proposal-id>` accepts only `accepted` or `consumed` Goal Proposals.
It verifies the proposal's immutable plan hash, copies bounded safe context, creates
one Mission JSON record, and reconciles already-persisted events.

Creation never accepts a proposal, dispatches its capability, plans a Team task, or
calls a domain application handler. A consumed proposal's safe dispatch summary may
provide an existing Team task or approval ID. A Team task link alone is not treated
as proof that approval is pending; `team.plan.created` must authoritatively establish
that state.

## Persistence and restart behavior

Missions live outside the source repository:

```text
~/.orion/missions/<mission-id>.json
```

`ORION_USER_DATA` relocates the entire user-data root. Orion rejects a Mission root
inside its installation, rejects symlinked roots and records, bounds each record to
1 MiB by default, uses strict JSON fields, and applies owner-only permissions where
supported. Writes use unique create-only temporary files, flush plus `fsync`, and
atomic replacement under process and cross-process locks.

`missions.max_record_bytes` configures the per-record bound and
`missions.history_limit` configures reconciliation's requested Event Store bound;
the service also respects Event Store's configured maximum.

`mission show` reads the persisted projection after restart. Correctness does not
depend on a live subscriber: `mission reconcile` queries Event Store history and
rebuilds the projection explicitly.

## Links

Every Mission contains Goal and Proposal links. Phase 1 recognizes these additional
link types only when an existing persisted result or correlated event supplies the
ID:

- Team task and Team run;
- Command Center job;
- approval;
- validation;
- documentation review.

Current events can populate a Team task link. Goal Proposal dispatch summaries may
also supply existing Team task and approval IDs. There are currently no authoritative
Team run, validation, documentation-review, or Command Center lifecycle events for
Mission projection, so those links remain unset unless an existing persisted result
explicitly supplies one. IDs are never generated or inferred from human text.

## Event correlation and replay

Reconciliation makes bounded Event Store queries by Goal correlation ID, Proposal
subject ID, and existing linked subject IDs. Results are deduplicated by event ID and
sorted by timestamp, known lifecycle order, then event ID.

Proposal events must match all of:

- Mission Goal ID and event `correlation_id`;
- Mission Proposal ID and event `subject_id`;
- payload Goal ID, Proposal ID, and proposal version.

A Team plan event must match the Goal correlation and have identical subject and
payload Team task IDs. It must also match an already-authoritative Team task link or
be caused by this proposal's accepted event. Goal text is never a correlation key.

Phase 1 understands only these existing event contracts:

| Event | Projection effect |
| --- | --- |
| `goal.proposal.created` | Preserve an event reference; no eligible Mission transition |
| `goal.proposal.validated` | Preserve an event reference; no lifecycle claim |
| `goal.proposal.accepted` | Proposal `accepted`; planning/team-planning at 10% |
| `goal.proposal.consumed` | Proposal `consumed`; planning/team-planning at least 15% unless a later observed state is stronger |
| `team.plan.created` | Link its Team task; planning at 20%, or awaiting approval at 30% when its payload requires approval |
| `goal.proposal.failed` | Failed/failed with no automatic retry action |

Unknown, malformed, unrelated, duplicated, or text-only matches do not affect the
projection. The Mission Engine publishes no Mission events, avoiding projection
feedback loops.

## Status, stage, and deterministic progress

Only states supported by current facts are modeled:

| Status | Stage | Progress | Derivation |
| --- | --- | ---: | --- |
| `created` | `proposal` | 5% | Model/replay baseline; manual creation itself requires an eligible proposal |
| `planning` | `team_planning` | 10% | Accepted proposal |
| `planning` | `team_planning` | 15% | Consumed proposal without a later plan fact |
| `planning` | `team_planning` | 20% | Team plan created without an observable approval requirement |
| `awaiting_approval` | `approval` | 30% | Team plan event explicitly reports awaiting/required approval |
| `failed` | `failed` | Last observed value, minimum 10% | Proposal failure event |

Progress is a fixed mapping, never an AI estimate. Phase 1 cannot observe
implementation, validation, documentation review, final review, cancellation,
rollback, or completion authoritatively. It therefore does not model those statuses
and never reports 100%.

## Recommended next action

When a correlated Team plan requires human approval, Mission projection uses the
shared interface-action mapping and recommends:

```text
team approve <team-task-id>
```

Structured results may also suggest the read-only `team status <team-task-id>` and
`mission reconcile <mission-id>` commands. Stable capability IDs such as
`team.approve` may appear as internal semantic metadata, but they are never exposed as
CLI commands. Recommendations are returned as data only and are never executed.

If no safe action follows from observable facts, `next_action` is empty and
`next_action_reason` explains why.

## History, reconciliation, and validation

`mission history <mission-id>` returns relevant events newest first, with original
event IDs, timestamps, types, and payloads. The view is bounded to 1,000 requested
events and Event Store's configured maximum. It does not republish or replay events.
The Mission JSON stores bounded event references rather than duplicate full payloads;
`event_cursor` equals the reference count and the final reference supplies
`last_event_id` and `last_event_at`.

`mission reconcile <mission-id>`:

1. loads the persisted Mission;
2. queries and strictly correlates existing events;
3. deterministically rebuilds projection state;
4. compares projection-only fields;
5. atomically replaces only the Mission record when state changed.

No domain state is repaired or invoked. If Event history is disabled or unavailable,
reconciliation preserves the last persisted lifecycle projection and records a
warning instead of downgrading state from an empty observation set.

`mission validate <mission-id>` checks schema parsing, storage location, proposal
existence and version, Goal identity, proposal plan hash, link syntax/uniqueness,
event cursor integrity, replayability, and whether the persisted projection matches a
fresh rebuild. Validation is read-only. A stale projection must be corrected with an
explicit reconcile command.

## CLI

```text
mission create <proposal-id>
mission show <mission-id>
mission list [--status <status>] [--goal <goal-id>] [--proposal <proposal-id>] [--limit <n>]
mission history <mission-id> [--limit <n>]
mission validate <mission-id>
mission reconcile <mission-id>
```

The core router contains only one Mission-family dispatch branch. Parsing and
rendering live in the CLI adapter; all lifecycle logic lives below the application
handler.

## Safety boundary

Mission creation writes only Mission persistence. Reconciliation may replace only a
Mission projection. Show, list, history, and validation are read-only.

No Mission code executes capabilities, consumes approvals, calls Team or Command
Center mutation handlers, launches implementation, validation, documentation review,
agents, jobs, providers, Git, subprocesses, or workspace mutations. There is no
subscriber, background worker, scheduler, retry, pause/resume, GUI, REST endpoint,
WebSocket, voice path, or Mission-generated event in Phase 1.

## Known limitations and next milestone

- Event Store queries and stored references are bounded; Phase 1 has no pagination or
  incremental byte-offset cursor.
- Command Center currently publishes no lifecycle events that can safely drive a
  Mission link or status.
- AI Team exposes no Mission-consumable implementation, validation, documentation,
  review, rollback, or completion events.
- No authoritative completion signal exists, so Missions cannot reach completed or
  100%.
- Missions update only when explicitly created or reconciled; there is no live
  Mission subscriber.

The recommended next milestone is **Orion v0.8.6 — Mission Coordinator**, which may
design explicit progression while preserving domain approvals and authority. It is
not implemented in Phase 1.
