# Orion v0.8.7 Mission Coordinator Phase 2

## Purpose

Mission Engine answers “Where are we?” Mission Coordinator answers “What is the one
safe operation the user may explicitly dispatch next?”

```text
Mission -> Preview -> Confirm -> One Operation -> Reconcile -> Stop
```

The Coordinator is a human-controlled gearbox, not a workflow engine. One explicit
confirmation can call at most one existing application operation. The Coordinator
always stops after the downstream result and one Mission reconciliation.

## Phase 2 scope

Phase 2 extends the reviewed allowlist across the existing post-approval AI Team
lifecycle:

```text
team.approve
  -> TeamApprovalRequest
  -> AiTeamApplicationHandler.approve()

team.implement
  -> TeamImplementationRequest(run_followups=False)
  -> AiTeamApplicationHandler.implement()

team.validate
  -> TeamRunRequest(run_followups=False)
  -> AiTeamApplicationHandler.validate()

team.documentation_review
  -> TeamRunRequest(run_followups=False)
  -> AiTeamApplicationHandler.documentation_review()
```

These are existing typed Team application commands. The Coordinator does not add a
second implementation, validation, or review path. `run_followups=False` is the
coordination-specific stop control: ordinary Team CLI behavior may retain its
existing automatic follow-ups, while one confirmed Mission advance cannot cascade
from implementation into validation or from validation into Documentation Review.

AI Team does not currently expose a typed final-accept or completion operation. The
Coordinator therefore stops at final review. It can reconcile reviewed
`team.final_review.completed` and `team.final_review.blocked` facts if an authorized
application boundary publishes them, but it does not fabricate or dispatch that
decision.

## Architecture and boundaries

```text
Mission JSON + correlated Event Store facts
                  |
                  v
        deterministic reconciliation
                  |
                  v
        read-only Team lifecycle inspection
                  |
                  v
        MissionNextOperation
                  |
                  v
        preview + state-bound token
                  |
          explicit CLI Y/N/D
                  |
                  v
    allowlisted MissionOperationTranslator
                  |
                  v
       one typed Team application request
                  |
                  v
      persisted Team result + lifecycle event
                  |
                  v
         Mission reconciliation
                  |
                 STOP
```

The Coordinator does not parse or invoke CLI commands internally. It does not use
reflection, dynamic capability lookup, free-form shell dispatch, providers, agents,
Git, subprocesses, or direct workspace access. AI Team remains authoritative for
plans, immutable approvals, implementation runs, validation attempts, and
documentation-review attempts.

## Deterministic next-operation rule

`mission next` uses only the reconciled Mission projection and the bounded,
read-only `AiTeamApplicationHandler.coordination_details()` result. It does not use
goal text or an LLM.

| Mission state | Required authoritative Team facts | Outcome |
| --- | --- | --- |
| `awaiting_approval / approval` | Matching Team task and plan hash; no existing approval | `team.approve` |
| `approved / implementation` | Exactly one matching approved approval; no run or unresolved run | `team.implement` |
| `implementing / implementation` | Matching started run without completion | Blocked while implementation is unresolved |
| `awaiting_validation / validation` | Matching completed run; no validation attempt | `team.validate` |
| `awaiting_documentation / documentation_review` | Matching passed/warning validation; no documentation attempt | `team.documentation_review` |
| `awaiting_review / final_review` | Completed documentation review | Blocked pending a separate human final-review boundary |
| `blocked` | Failed validation/documentation or final-review block | Blocked; no automatic retry |
| `failed` | Proposal or implementation failure | Blocked; no automatic retry |
| `completed / completed` | Reviewed final-completion fact | No further operation |

Missing subjects, duplicate approvals, multiple or unresolved runs, mismatched task,
approval, plan, run, attempt, status, or timestamp fields, malformed read-only data,
and unavailable Team inspection all fail closed. The Coordinator never guesses which
record is current.

## Preview, confirmation, and token binding

`mission next <mission-id>` reconciles, validates, and reads Team lifecycle state to
produce a preview. It never dispatches a capability. Blocked previews contain no
advance token.

`mission advance <mission-id>` obtains and displays a fresh preview, then prompts:

```text
Advance this Mission by exactly one operation? [Y/N/D]:
```

`N`, empty input, or interruption dispatches nothing. `D` repeats details without
executing. Only `Y` submits a structured `MissionAdvanceRequest` with
`confirmed=True` and the exact token.

The coordination schema-2 token is SHA-256 over canonical JSON. It binds Mission,
Goal, Proposal, and Proposal-version identity; status, stage, progress, update time,
event cursor, last event, links, capability, request type, subject, and the exact
authoritative Team inputs for that operation. Downstream bindings include, as
applicable:

- Team task ID and persisted plan SHA-256;
- immutable approval ID;
- run ID and implementation completion timestamp;
- validation ID, status, and completion timestamp.

Preview time and actor are excluded, so a token remains stable after restart only
when authoritative state is unchanged. Before dispatch, the Coordinator reloads and
reconciles the Mission, repeats read-only Team inspection, recomputes the operation
and token, and compares it in constant time. Any relevant lifecycle change makes the
token stale.

## Typed translation and exactly-one operation

`MissionOperationTranslator` contains four explicit branches and exact input
schemas. It constructs only `TeamApprovalRequest`, `TeamImplementationRequest`, or
`TeamRunRequest`, then calls the corresponding named method on
`AiTeamApplicationHandler`. There is no arbitrary callable registry or stringly typed
execution.

For each confirmed advance:

1. acquire the per-Mission cross-process lock;
2. reconcile and verify current authoritative state and token;
3. reserve one audit attempt before crossing the dispatch boundary;
4. invoke the translator exactly once;
5. re-read/reconcile Mission state exactly once after the result;
6. persist the safe audit outcome and return;
7. stop, even when the new state has another legal operation.

A returned command result is not itself proof of lifecycle success. Projection moves
only when a strict persisted lifecycle event is present. A successful result without
an observable state transition is reported with a warning and cannot be replayed.

## Lifecycle events and projection

AI Team publishes the following strict events only after it can derive the fact from
a persisted application result:

```text
team.plan.approved
team.implementation.started
team.implementation.completed
team.implementation.failed
team.validation.completed
team.documentation_review.completed
```

Mission projection also recognizes reviewed final-decision contracts:

```text
team.final_review.completed
team.final_review.blocked
```

The projection checks source, correlation, causation where applicable, Team task,
approval, plan hash, run, attempt identity, status, and UTC completion time. Events
must arrive in a legal lifecycle order; malformed, unrelated, duplicated, late, or
phase-skipping facts do not advance state.

| Observed fact | Mission projection |
| --- | --- |
| Team plan created, approval required | `awaiting_approval / approval / 30%` |
| Approval completed | `approved / implementation / 35%` |
| Implementation started | `implementing / implementation / 45%` |
| Implementation completed | `awaiting_validation / validation / 60%` |
| Validation passed or warned | `awaiting_documentation / documentation_review / 75%` |
| Validation failed, unavailable, or errored | `blocked / validation / 70%` |
| Documentation passed, warned, or was not required | `awaiting_review / final_review / 90%` |
| Documentation failed, unavailable, or errored | `blocked / documentation_review / 85%` |
| Final review completed | `completed / completed / 100%` |
| Final review blocked | `blocked / final_review / 95%` |
| Implementation failed | `failed / failed` |

## Audit, duplicate prevention, and uncertainty

Coordination audit records live under external Mission runtime data:

```text
~/.orion/missions/coordination/<mission-id>.json
```

They are strict, bounded, atomically replaced, owner-restricted where supported, and
protected from repository-local and symlinked storage. Every advance uses an
exclusive create-only per-Mission lock and writes a `reserved` record before calling
AI Team.

The audit contains bounded identities, capability, token, actor, timestamps, safe
outcome state, downstream reference, and last observed event ID. It does not contain
secrets, raw exceptions, prompts, workspace content, or provider output.

A persisted `reserved` attempt after restart is uncertain and blocks replay. An
exception after the dispatch boundary or a failed post-dispatch reconciliation also
becomes `uncertain`. There is no automatic retry, stale-lock removal, uncertainty
reset, or duplicate dispatch. Operators must reconcile authoritative Team and Event
Store state before any future recovery mechanism may be considered.

## Commands and read-only guarantees

```text
mission next <mission-id>
mission advance <mission-id>
```

`mission next` may reconcile Mission projection and perform bounded Team inspection,
but it never calls approval, implementation, validation, or documentation mutation
handlers and never writes coordination audit. Existing Mission show, list, history,
and validate operations remain read-only. `mission advance` is the only coordination
entry point that can dispatch, and only after explicit confirmation.

## Intentional Phase 3 gaps

- AI Team has no typed final-accept/final-completion application command; Phase 2
  therefore stops at final review even though projection can observe reviewed final
  events.
- There is no automatic retry or operator uncertainty-resolution command.
- Command Center lifecycle event coverage is still incomplete.
- Missions update on explicit create, reconcile, next, or advance; there is no live
  subscriber, worker, scheduler, loop, server, GUI, REST, WebSocket, Discord, voice,
  or mobile coordinator.
- Rollback and cancellation are not Mission-coordinated lifecycle transitions.

Phase 3 should begin only with a reviewed typed human final-decision boundary and an
authoritative event producer. It must preserve explicit confirmation and the
one-operation/reconcile/stop invariant.
