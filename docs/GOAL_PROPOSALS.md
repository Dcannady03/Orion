# Orion v0.8.3 Goal Proposals

## Purpose

A Goal Plan answers what Orion recommends. A Goal Proposal records the exact,
versioned plan a person can review, accept, or reject. Existing application handlers
still decide whether the one accepted operation is valid and safe.

```text
High-level goal
  -> deterministic Goal Engine
  -> immutable GoalPlan
  -> Goal Proposal Service
  -> external GoalProposal JSON
  -> human review and explicit hash-bound acceptance
  -> allowlisted typed request
  -> existing application handler
  -> ApplicationResult
```

Proposal creation, showing, listing, and validation execute no capabilities. An
acceptance can dispatch at most one operation and never continues to later plan
steps.

## Persistence

Proposal records live outside the application repository:

```text
~/.orion/goals/proposals/<proposal-id>.json
```

`ORION_USER_DATA` relocates the same user-data root. The runtime passes Orion's
installation root as a forbidden storage root, so proposal persistence cannot fall
back into source. Records use schema version 1, strict JSON, bounded files,
owner-restricted permissions where supported, unique temporary files, flush/fsync,
atomic replacement, and per-record cross-process transition locks. Symlinked roots
and records are rejected.

The repository supports create-only save, strict get, bounded/filterable list,
expected-status replacement, and fail-closed supersession. It never deletes proposal
history.

A process terminated during a status transition can leave a lock file. Orion does
not silently break that lock because doing so could allow acceptance replay; an
operator must inspect the proposal and remove the stale lock deliberately.

## Models

All proposal models are frozen and JSON-safe:

- `GoalProposal` is the complete persisted lifecycle record.
- `GoalProposalStep` preserves every Goal Plan step in its original order.
- `GoalProposalSnapshot` contains immutable content covered by the plan hash.
- `GoalProposalAcceptance` binds confirmation to a proposal ID and exact hash.
- `GoalProposalRejection` records an actor and optional safe reason.
- `GoalProposalValidation` exposes integrity, context, input, and translation checks.

Steps copy the selected capability's approval flag, mutation flag, required inputs,
resolved inputs, expected outputs, permissions, and explicit typed request label.
Only the current eligible step can change step status. Later steps remain proposal
history; v0.8.3 never advances to them.

## Lifecycle

```text
pending
  |-- reject ----------------------> rejected
  |-- expiry ----------------------> expired
  |-- integrity invalid -----------> invalid
  |-- explicit replacement --------> superseded
  `-- confirmed hash-bound accept -> accepted
                                      |-- handler success -> consumed
                                      `-- handler failure -> failed
```

- `pending`: reviewable and not yet used.
- `accepted`: explicit acceptance was atomically recorded. Dispatch is in progress,
  or a crash left its outcome uncertain. This state is non-replayable.
- `consumed`: exactly one translated application operation returned a successful or
  warning `ApplicationResult`.
- `failed`: the one downstream operation returned failure or could not be dispatched
  after acceptance. Automatic and manual retry commands are intentionally absent.
- `rejected`: a person rejected the pending proposal. It remains readable.
- `expired`: its fixed acceptance window elapsed.
- `invalid`: immutable content, selected capability metadata, or required current
  inputs failed integrity validation.
- `superseded`: an explicitly linked newer proposal replaced it.

Every terminal state prevents acceptance. A failed operation is not retried because
mutation uncertainty is safer than replay.

## Integrity model

### Proposal ID and version

Each proposal uses a random UUID-derived ID:

```text
proposal-<32 lowercase hexadecimal characters>
```

Creating another proposal for the same `goal_id` increments its version and preserves
all older records. It does not silently supersede them. `--supersedes <proposal-id>`
explicitly creates a linked replacement and marks only that pending proposal
`superseded`. Storage blocks the old proposal before publishing the replacement, so a
partial write fails closed.

### Immutable plan hash

`plan_hash` is SHA-256 over canonical UTF-8 JSON with sorted keys, stable separators,
and non-finite numbers prohibited. It covers:

- proposal ID, goal ID, proposal version, creation time, goal text, and classification;
- exact workspace and department identity;
- priority, fixed expiry, source, supersession link, and safe metadata;
- full and proposal-scoped registry fingerprints; and
- every ordered step's ID, capability ID, reason, mutation/approval metadata,
  required/resolved inputs, expected outputs, permissions, and typed request label.

Mutable lifecycle status, actors, timestamps, failure summaries, dispatch summaries,
and `superseded_by` are excluded. Every acceptance supplies and re-verifies the exact
stored hash.

### Capability fingerprints

Orion currently treats registration in `CapabilityRegistry` as enabled state.
Fingerprints include `enabled: true`, capability ID, mutation and approval flags,
permissions, and complete input/output schemas.

- The proposal-scoped fingerprint covers the ordered selected capabilities. Any
  selected capability removal or safety-relevant metadata change invalidates the
  proposal.
- The full registry fingerprint covers the complete catalog. An unrelated addition
  or change produces a warning when the scoped fingerprint is unchanged.

### Expiry and context

The default expiry is 24 hours and the configured maximum is 168 hours. Reads never
refresh expiry. Validation may safely mark a stale pending proposal `expired`.

Validation requires the proposal workspace to remain an existing directory and the
active Orion workspace. This prevents a typed request without an explicit workspace
field from operating in a different context. A recorded department must remain an
existing enabled Command Center department. An unassigned Goal Plan remains valid
without one.

## Translation and dispatch

Translation is an explicit allowlist. It does not dynamically import classes, reflect
over proposal names, resolve callables from strings, or call a CLI adapter.

The only supported v0.8.3 translation is:

| Capability | Typed request | Existing handler |
| --- | --- | --- |
| `team.plan` | `TeamPlanRequest(goal=<resolved goal>)` | `AiTeamApplicationHandler.plan` |

The service scans the ordered plan for the first supported capability, so an earlier
read-only proposal step such as `workspace.inspect` remains preserved without
blocking a later `team.plan`. If no selected step is supported, validation reports
`blocked`; the proposal remains pending and nothing is dispatched.

Acceptance first validates and translates without side effects, atomically changes
`pending` to `accepted`, and then calls the existing Team application handler once.
Another acceptance loses the expected-status transition and cannot dispatch.

AI Team planning may contact its configured planning provider because that is the one
operation explicitly accepted. The returned Team task still enters its normal
`awaiting_approval` lifecycle. Goal Proposal acceptance is never Codex implementation
approval and does not satisfy AI Team's plan hash, workspace, execution-engine, or
single-use approval rules.

## CLI

```text
goal proposal create "<goal>"
goal proposal show <proposal-id>
goal proposal list
goal proposal list --status pending
goal proposal list --goal <goal-id>
goal proposal validate <proposal-id>
goal proposal accept <proposal-id>
goal proposal reject <proposal-id>
goal proposal reject <proposal-id> --reason "Wrong workspace"
```

Creation accepts Goal Request options plus `--expires-hours` and `--supersedes`.
Acceptance first renders the proposal and validation evidence, then requires
`Y`, `N`, or `D`. Confirmation logic remains entirely in the CLI adapter. The
application handler receives a structured, hash-bound `GoalProposalAcceptance`.

The existing `goal validate "<goal>"` validates a new, unpersisted Goal Plan.
`goal proposal validate <proposal-id>` validates an existing persisted proposal.

## Safety guarantees and limits

- Creation, show, list, validation, rejection, and supersession call no provider,
  agent, job launch, approval, execution engine, subprocess, Git, or CLI adapter.
- Those operations do not bind, refresh, or modify workspaces.
- Acceptance dispatches at most the single current allowlisted operation.
- The proposal layer does not create Command Center jobs or reimplement Team logic.
- No proposal acceptance can approve implementation or continue automatically.
- Failure messages are bounded and raw stack traces are not persisted.
- Cross-process record locks and expected-state replacement serialize acceptance and
  other lifecycle transitions for a proposal.
- A crash after the downstream handler returns but before terminal persistence can
  leave the proposal `accepted`. This deliberately blocks replay and requires human
  inspection.

Mission Engine, multi-step continuation, retries, workers, schedules, REST, GUI,
voice, WebSockets, mobile, automatic acceptance, and automatic implementation
approval remain out of scope.

## Risks and migration notes

- File persistence can fail because of permissions, storage exhaustion, corruption,
  or a stale transition lock. These failures block the operation and require
  inspection; Orion never falls back to repository-local storage.
- A downstream handler can return before terminal proposal persistence succeeds.
  The retained `accepted` state prevents replay but cannot prove whether external
  effects occurred.
- Capability definitions may become stale. Scoped safety changes invalidate the
  proposal, while unrelated full-registry changes remain visible warnings.
- Legacy clients that only display `ApplicationResult.message` can still render the
  lifecycle, but integrations should migrate to the structured proposal fields.
- Only `team.plan` is translated in v0.8.3; every other capability stays blocked
  until it has an audited typed request and application-handler boundary.
- Future proposal schema migrations must preserve immutable-hash semantics and
  continue rejecting unknown or lossy conversions.

## Future Mission relationship

Mission Engine should be considered only after proposal lifecycle behavior is stable.
A future Mission may reference consumed proposals and separately approved downstream
artifacts, but it must never reinterpret proposal acceptance as permission to execute
every remaining capability.
