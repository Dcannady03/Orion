# Orion v0.8.7 Mission Coordinator Phase 3

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

## Phase 3 scope

Phase 3 adds the reviewed typed human final-review decision boundary that Phase 2
deliberately left open:

```text
team.final_review
  -> TeamFinalReviewRequest
  -> AiTeamApplicationHandler.final_review()
```

The decision is the one human boundary between a reviewed run and Mission
completion. `AiTeamApplicationHandler.final_review()` records one immutable
`FinalReviewDecision` on the run (decision, optional note, actor, completion time)
and publishes exactly one strict lifecycle event from the persisted record:

- `accepted` -> `team.final_review.completed` (Mission `completed / 100%`);
- `changes_requested` or `rejected` -> `team.final_review.blocked`
  (Mission `blocked / final_review / 95%`).

A run accepts at most one decision; a second decision is refused at the bridge, and
the Coordinator blocks fail-closed when a decision already exists but the
projection has not observed it yet. There is no rework or repair loop in Phase 3:
a blocked Mission stays blocked. The decision contract is:

- the decision is captured only through `mission advance` (two prompts: decision
  first, then the usual confirmation with the decision shown);
- the decision and note are token-bound resolved inputs, so an accept-token and a
  reject-token are different tokens and a decision cannot be swapped after preview;
- the Coordinator dispatches only when validation passed or warned and
  documentation review passed, warned, or was not required;
- the event carries the full identity payload the projection requires (run, task,
  approval, plan hash, validation ID, documentation-review ID, decision ID);
- the projection links the decision record as a `final_review` Mission link when
  the event is observed, mirroring the validation and documentation-review links.

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
| `awaiting_review / final_review` | Passed/warning validation, passed/warnings/not-required documentation review, no recorded decision | `team.final_review` |
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

When the previewed operation is `team.final_review`, the CLI asks for the decision
first, then re-previews with the decision bound into the token before the usual
confirmation:

```text
Final review: accept the run, request changes, or reject it? [A/C/R/N]:
```

`N`, empty input, or interruption at either prompt dispatches nothing. `D` repeats
details without executing. Only `Y` at the confirmation submits a structured
`MissionAdvanceRequest` with `confirmed=True`, the exact token, and the decision.
An optional note travels with the decision (`mission advance <mission-id> --note
"<text>"`) and is token-bound with it.

The coordination schema-2 token is SHA-256 over canonical JSON. It binds Mission,
Goal, Proposal, and Proposal-version identity; status, stage, progress, update time,
event cursor, last event, links, capability, request type, subject, and the exact
authoritative Team inputs for that operation. Downstream bindings include, as
applicable:

- Team task ID and persisted plan SHA-256;
- immutable approval ID;
- run ID and implementation completion timestamp;
- validation ID, status, and completion timestamp;
- documentation-review ID, status, and completion timestamp;
- the human final-review decision and note, when the operation is
  `team.final_review`.

Preview time and actor are excluded, so a token remains stable after restart only
when authoritative state is unchanged. Before dispatch, the Coordinator reloads and
reconciles the Mission, repeats read-only Team inspection, recomputes the operation
and token, and compares it in constant time. Any relevant lifecycle change makes the
token stale.

## Typed translation and exactly-one operation

`MissionOperationTranslator` contains five explicit branches and exact input
schemas. It constructs only `TeamApprovalRequest`, `TeamImplementationRequest`,
`TeamRunRequest`, or `TeamFinalReviewRequest`, then calls the corresponding named
method on `AiTeamApplicationHandler`. There is no arbitrary callable registry or
stringly typed execution.

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
team.final_review.completed
team.final_review.blocked
```

The final-review events are produced only by `AiTeamApplicationHandler.final_review()`
from a persisted `FinalReviewDecision` record; the Coordinator never fabricates a
decision.

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
but it never calls approval, implementation, validation, documentation, or
final-review mutation handlers and never writes coordination audit. Existing
Mission show, list, history, and validate operations remain read-only.
`mission advance` is the only coordination entry point that can dispatch, and only
after explicit confirmation.

## Intentional gaps

- There is no automatic retry or operator uncertainty-resolution command.
- There is no rework or repair loop after a `changes_requested` decision; the
  Mission stays blocked.
- Command Center lifecycle event coverage is still incomplete.
- Missions update on explicit create, reconcile, next, or advance; there is no live
  subscriber, worker, scheduler, loop, server, GUI, REST, WebSocket, Discord, voice,
  or mobile coordinator.
- Rollback and cancellation are not Mission-coordinated lifecycle transitions.
