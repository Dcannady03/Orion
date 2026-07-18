# Orion v0.5.6 — Ledger

Ledger makes project work a first-class, durable Orion object before introducing an
automated workflow or implementation engine.

## Task Manager Phase 1

Tasks are stored inside the active workspace at `.orion/tasks.json`. Each task has a
strict identity, goal, status, approval state, optional role and agent assignment,
dependencies, artifacts, and timezone-aware timestamps.

New commands:

```text
task create "<goal>"
task list
task show <task-id>
task approve <task-id>
task cancel <task-id>
task events <task-id>
task link-plan <task-id> <team-task-id>
```

## Observable progress

Every task mutation appends a structured record to `.orion/task-events.jsonl`. Event
histories require unique IDs, valid transitions, ordered timestamps, an initial
creation event, and references to existing project tasks. This stream is the foundation
for the planned Workflow Engine and streaming progress UI.

## AI Team integration

Reviewed AI Team plans can be linked as task artifacts while their complete audit
records remain under `~/.orion/team/tasks/`. Linking a plan does not approve the project
task or start implementation.

## Safety and persistence

- Task snapshots use atomic replacement and corrupt documents are left unchanged.
- Dependencies reject missing references, duplicates, self-reference, and cycles.
- Approval moves a proposed task only to `Ready`.
- Cancellation is explicit and terminal.
- Workspace changes isolate each project's tasks and events.
- No workflow runner, Codex bridge, tools, file writes, or Git actions are enabled.

## Verification

The v0.5.6 regression suite contains **229 passing tests**, including strict task and
event schemas, lifecycle transitions, corruption preservation, dependency cycles,
workspace isolation, Home metrics, CLI behavior, and AI Team artifact linking.
