# Orion v0.8.6 Mission Coordinator

## Purpose

Mission Engine answers “Where are we?” Mission Coordinator answers “What is the one
safe operation the user may explicitly dispatch next?”

```text
Mission -> Preview -> Confirm -> One Operation -> Reconcile -> Stop
```

The Coordinator is a human-controlled gearbox, not a workflow engine. One explicit
confirmation can call at most one existing application operation. The Coordinator
always stops after the downstream result and one Mission reconciliation.

## Current scope

v0.8.6 coordinates exactly one capability:

```text
team.approve -> TeamApprovalRequest -> AiTeamApplicationHandler.approve()
```

This is the only current Mission state with both authoritative projection facts and a
narrow typed application boundary. `team.implement`, validation, documentation
review, rollback, and final-review completion are deliberately unsupported. The
Mission may recommend their existing CLI commands, but the Coordinator will not
dispatch them.

## Architecture and boundaries

```text
Mission JSON + correlated Event Store facts
                  |
                  v
        deterministic reconciliation
                  |
                  v
        MissionNextOperation
                  |
                  v
        preview + advance token
                  |
          explicit CLI Y/N/D
                  |
                  v
    allowlisted MissionOperationTranslator
                  |
                  v
         TeamApprovalRequest
                  |
                  v
      AiTeamApplicationHandler.approve()
                  |
                  v
       team.plan.approved event
                  |
                  v
         Mission reconciliation
                  |
                 STOP
```

The Coordinator does not parse or invoke CLI commands internally. It does not call
providers, agents, execution engines, Git, subprocesses, workspaces, Command Center,
or approval storage directly. AI Team remains responsible for plan SHA-256 validation
and approval creation.

## Coordination models

All public coordination models are frozen and expose JSON-safe dictionaries:

- `MissionNextOperation` describes one eligible capability or an explicit blocked
  reason;
- `MissionAdvancePreview` binds current status, stage, progress, operation, warnings,
  and advance token;
- `MissionAdvanceRequest` carries Mission ID, exact token, explicit confirmation, and
  actor;
- `MissionAdvanceResult` carries one downstream result, old/new projection state,
  reconciliation outcome, audit state, and the stop guarantee;
- `MissionAdvanceAudit` records a bounded reservation and terminal outcome without
  becoming domain truth.

Blocked previews issue no token and cannot be translated or dispatched.

## Next-operation rule

The rule is deterministic and does not inspect goal text or use an LLM:

| Mission state | Additional authority | Coordinator outcome |
| --- | --- | --- |
| `awaiting_approval / approval` | Linked Team task, Team task still awaiting approval, valid plan hash, existing approvals inspectable and empty | Eligible `team.approve` |
| `awaiting_approval` without Team task | None | Blocked: missing authoritative subject |
| `planning` or another unsupported state | None | Blocked: no supported operation |
| `approved / implementation` | Approval event/link | Blocked: `team.implement` is not coordinated in v0.8.6 |
| `awaiting_review / final_review` | None | Blocked: explicit completion operation does not exist |
| `failed` | Failure projection | Blocked: retries are unsupported |
| Invalid/stale Mission or unavailable history | Failed validation | Blocked |
| Existing approval without observed Mission transition | Read-only Team approval inspection | Blocked pending operator inspection/reconciliation |

The Coordinator uses `AiTeamApplicationHandler.approval_details()` as a read-only
boundary to obtain the persisted plan SHA-256 and verify no approval already exists.
If that inspection is unavailable or malformed, it fails closed.

## Preview and confirmation

`mission next <mission-id>` reconciles and validates the Mission, determines the one
operation, and returns a preview. It never dispatches a capability.

An eligible preview includes:

- Mission status, stage, progress, and update timestamp;
- capability `team.approve`;
- Team task target;
- mutation and downstream-approval flags;
- human CLI representation `team approve <task-id>`;
- deterministic advance token.

`mission advance <mission-id>` obtains and renders a fresh preview, then prompts only
at the CLI boundary:

```text
Advance this Mission by exactly one operation? [Y/N/D]:
```

`N`, an empty response, or interruption dispatches nothing. `D` repeats the exact
preview and does not execute. Only `Y` submits a structured request with
`confirmed=True` and the preview token.

Non-interactive future clients can use the structured preview token and application
handler directly; they must still provide explicit confirmation.

## Advance-token binding

The token is SHA-256 over canonical JSON containing:

- coordination schema version;
- Mission, Goal, Proposal, and Proposal-version identities;
- Mission `updated_at`;
- status, stage, and deterministic progress;
- last event ID/time and event cursor;
- all authoritative Mission links;
- capability, operation type, and subject ID;
- resolved Team task ID and exact persisted plan SHA-256.

It excludes preview time and actor, so a token survives restart when authoritative
state is unchanged. Before dispatch, the Coordinator reloads and reconciles the
Mission, re-inspects Team approval state, recomputes the operation and token, and uses
constant-time comparison. A new event, changed link, changed plan hash, reconciled
stage, or other state change invalidates the token.

## Typed translation and downstream approval

`MissionOperationTranslator` contains one explicit branch. It accepts only an
eligible `team.approve` operation whose resolved inputs are exactly
`team_task_id` and `plan_sha256`, creates `TeamApprovalRequest`, and calls only
`AiTeamApplicationHandler.approve()`.

There is no reflection, import-by-name, callable registry, arbitrary capability
lookup, or CLI parsing. The typed request binds:

- Team task identity;
- actor;
- exact plan SHA-256;
- Mission Goal correlation ID;
- latest Mission event as causation where available.

Mission confirmation authorizes calling the Team approval operation. The resulting
Team approval remains an existing immutable Codex Bridge record. The Coordinator does
not manufacture an approval ID, write approval files, bypass the plan hash, implement
the plan, or consume the approval.

## Concurrency, audit, and crash uncertainty

Coordination audit records live under external Mission runtime data:

```text
~/.orion/missions/coordination/<mission-id>.json
```

They are strict, bounded, atomically replaced, owner-restricted where supported, and
protected from repository-local and symlinked storage. Configuration keys are:

```text
missions.coordination_max_record_bytes
missions.coordination_lock_timeout_seconds
```

Every advance uses an exclusive create-only per-Mission lock. Under that lock the
Coordinator reconciles, verifies the token, rejects prior duplicate/uncertain state,
and persists a `reserved` attempt before calling AI Team. The currently supported
approval operation is a short local application boundary and invokes no provider, so
holding the coordination lock does not surround a long-running provider operation.

The audit records attempt ID, token, capability, subject, actor, timestamps, safe
result state, downstream approval reference, and last observed event ID. It contains
no secrets, raw exceptions, prompts, workspace content, or provider output.

Two processes cannot reserve the same operation concurrently. A stale lock fails
closed. If a process terminates after reservation, the persisted `reserved` record is
treated as uncertain after restart and blocks replay. If the handler raises or
reconciliation fails after the dispatch boundary, the audit becomes `uncertain`.
There is no automatic stale-lock removal, retry, or uncertainty reset command.

A returned downstream failure is recorded as `failed`, reconciled, and cannot be
automatically retried with the same token. A success without an observable Mission
transition is recorded as `succeeded`, warns the user, and blocks duplicate replay.

## Authoritative event and projection

AI Team now publishes one narrowly scoped fact only after approval succeeds:

```text
team.plan.approved
```

The event contains Team task ID, approval ID, status, approval state, and plan
SHA-256. Mission correlation requires the already-authoritative Team task identity
and a Goal or Team-task correlation ID. Failed approval emits no success event.

Projection maps the event to:

```text
status: approved
stage: implementation
progress: 35
approval link: <authoritative approval-id>
```

The Mission may recommend the existing `team implement <task> <approval>` command,
but v0.8.6 Coordinator returns a blocked result for that operation and stops.

## Commands

```text
mission next <mission-id>
mission advance <mission-id>
```

All existing Mission commands remain available. `mission next` performs only Mission
reconciliation, validation, read-only Team inspection, and preview generation.
`mission advance` may write the coordination audit, call one Team approval handler,
and reconcile Mission state once after the result.

## Exactly-one-operation and failure semantics

For one confirmed request the Coordinator performs at most one translator dispatch.
After an `ApplicationResult` returns it reconciles once, records the safe outcome,
returns structured old/new state, and stops. It never inspects the new state to launch
another capability.

Downstream failures are returned without retry. Unexpected exceptions after the
reserved dispatch boundary are uncertain even when no mutation is visible; safety
takes precedence over guessing. Operators must inspect Team state and Mission history.

## Known limitations and safer next milestone

- Only Team approval is coordinated.
- Implementation, validation, documentation review, rollback, and final review are
  not dispatched by Mission Coordinator.
- Event coverage ends at successful Team approval; there is still no Mission-visible
  implementation, review, rollback, or completion signal.
- Command Center event coverage remains incomplete.
- Progress stops at 35%; no real completion operation means no 100% Mission state.
- Advancement is explicit CLI/application work only; no worker, subscriber reaction,
  scheduler, server, GUI, REST, WebSocket, Discord, voice, mobile, retry, or autonomy
  exists.

Because observable lifecycle coverage is still incomplete, the safer next milestone
is **Mission Coordinator Phase 2**, limited first to reviewed Team lifecycle events and
typed operations. Orion Server would expose an incomplete coordination contract too
early. Phase 2 must remain explicit and should not introduce autonomous continuation.
