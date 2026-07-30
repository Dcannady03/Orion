# Orion v0.8.2 Goal Engine

## Purpose

The Goal Engine is Orion's interface-neutral reasoning boundary for high-level user
outcomes. It classifies a goal, resolves safe context, discovers registered
capabilities, predicts approval boundaries, and returns an immutable plan. It never
executes that plan.

Before this milestone, a user had to select a command family such as `team plan`,
`cc job create`, or `workspace`. The new flow allows any future interface to submit
the outcome first:

```text
Interface goal
  -> GoalRequest
  -> deterministic Goal Engine
  -> CapabilityRegistry inspection
  -> GoalPlan / GoalPreview
  -> ApplicationResult
```

No provider, application handler, agent, job, approval, or execution engine appears
on the planning path. Existing application handlers remain the only future execution
boundaries, after an explicit user decision.

## Models

All public models are frozen dataclasses and expose ordinary JSON-safe dictionaries.
They contain no services, clients, paths, providers, agents, or other implementation
objects.

- `GoalRequest` contains goal text plus optional workspace, department, priority,
  requested outcome, attachment references, provider preferences, execution mode,
  and the future `allow_ai_planning` switch.
- `GoalContext` records the resolved workspace source/mode, project name, existing
  department identity, and priority.
- `CapabilityStep` records the selected registry capability, reason, stage,
  registry-declared mutation/approval metadata, input/output fields, and required
  permissions.
- `GoalExplanation` records deterministic evidence for classification, workspace,
  department, capabilities, approval, and the safety boundary.
- `GoalPreview` is an informational-only execution outline with stages and approval
  boundaries.
- `GoalPlan` combines the stable goal ID, classification and confidence, context,
  steps, warnings, risks, explanation, preview, and planning-only next actions.

Goal IDs are deterministic SHA-256-derived identifiers over the canonical request,
resolved context, classification, and selected capability IDs. Plans are not
persisted by the Goal Engine. v0.8.3 can copy a completed plan into a separate
versioned Goal Proposal; the plan model and deterministic planner remain unchanged.

## Goal Plan versus Goal Proposal

A `GoalPlan` is an ephemeral recommendation. A `GoalProposal` is a persisted,
expiring, integrity-hashed snapshot created from that recommendation:

```text
GoalPlan (recommendation; no persistence)
  -> Goal Proposal Service
  -> GoalProposal (review record; no execution)
  -> explicit hash-bound acceptance
  -> one allowlisted typed application request
```

The Proposal Service revalidates every selected capability against the current
registry before persisting it. Proposal validation later checks the immutable plan
hash, selected-capability fingerprint, full-registry fingerprint, expiry, active
workspace, department, current inputs, and allowlisted translation. It does not ask
the Goal Engine to replan or silently refresh any field. See `GOAL_PROPOSALS.md`.

## Deterministic planner flow

1. Validate the immutable `GoalRequest`. Only `plan` and `preview` execution modes
   are accepted.
2. Classify the normalized goal with ordered deterministic rules. Authoritative
   categories are Engineering, Marketing, Documentation, Research, Automation,
   Security, Operations, Planning, Release, and Personal Productivity. Unknown
   goals fail with guidance instead of guessing.
3. Resolve an explicitly supplied directory, the active workspace, or Project
   Context. Resolution uses read-only path checks and never calls workspace bind,
   change, or refresh methods.
4. Read enabled Command Center departments once. An explicit department must exist.
   Automatic ownership uses category preferences but never creates or invents a
   department; an unmatched plan remains unassigned with a warning.
5. Inspect the current `CapabilityRegistry`. Semantic role selectors match capability
   IDs and descriptions, then retain only definitions that actually exist. The
   returned ID, schemas, permissions, mutation flag, and approval flag all come from
   the registry.
6. Build consecutively numbered steps, estimated stages, explanations, warnings,
   risks, and safe follow-up Goal commands.
7. Return a `GoalPlan`. At no point does the planner invoke a capability.

For a standard release goal and the current registry, the proposed capabilities are:

```text
1. team.plan
2. team.implement
3. team.validate
4. team.documentation_review
```

The planner can also discover renamed compatible capabilities from their metadata; it
does not fabricate a missing capability. If no role matches any registered
capability, planning fails safely.

## Approval prediction

Approval is descriptive, not an approval request. Each step copies
`requires_approval` from its selected `CapabilityDefinition`. The plan requires
approval when any step requires it. With the current catalog, `team.implement`
therefore introduces an implementation approval boundary while `team.plan`,
`team.validate`, and `team.documentation_review` do not.

This prediction never creates, claims, consumes, or bypasses an existing approval.
At future execution time, the relevant application handler must still enforce its
normal immutable-plan, workspace, actor, and single-use approval rules.

## CLI

The thin CLI adapter supports:

```text
goal plan "<goal>"
goal explain "<goal>"
goal preview "<goal>"
goal capabilities "<goal>"
goal classify "<goal>"
goal validate "<goal>"
```

Planning commands accept optional `--workspace`, `--department`, `--priority`,
`--outcome`, repeated `--attachment`, repeated `--provider`,
`--execution-mode plan|preview`, and `--allow-ai-planning`. The adapter only parses a
`GoalRequest`, calls `GoalApplicationHandler`, and uses the shared
`ApplicationResult` renderer. The core router only dispatches the `goal` family.

`goal classify` needs only goal text. The other views resolve a complete plan:

- `plan` shows the overall proposal;
- `explain` shows the evidence for every decision;
- `preview` shows stages and approval boundaries;
- `capabilities` shows registry metadata used by every proposed step; and
- `validate` confirms that the complete plan resolved safely.

None is an execution command.

## Safety boundary

The Goal Engine must never:

- invoke a selected capability or application handler;
- call an AI provider, agent, execution engine, shell, Git, or background runner;
- create, launch, synchronize, cancel, or modify a Command Center job;
- create, approve, claim, consume, or bypass an approval;
- bind, refresh, or change the active workspace or Project Context;
- edit, persist, or otherwise modify a repository; or
- treat a preview as authorization.

Attachment references are recorded but not opened. Provider preferences are recorded
but no provider is contacted. A proposed state-changing step is only metadata about
possible later work.

## Future AI assistance

`allow_ai_planning=True` is intentionally present for a future suggestion layer. In
v0.8.2 it produces a warning and uses the same deterministic planner. A future model
may suggest a classification or candidate steps, but deterministic workspace,
department, registry, permission, schema, and approval validation remains
authoritative. AI output may never introduce an unregistered capability.

Voice, GUI, Discord, REST, mobile, autonomous execution, automatic approval,
background agents, LLM reasoning loops, and mission execution remain out of scope.

## Recommended next milestone

Goal Proposals now provide the versioned, expiring review and single-operation bridge.
The next milestone may investigate a Mission Engine only after acceptance, failure,
crash-recovery, and migration behavior is proven stable. A Mission must preserve
separate capability and implementation approvals and must not reinterpret proposal
acceptance as authorization for automatic multi-step execution.
