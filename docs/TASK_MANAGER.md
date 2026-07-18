# Orion Task Manager — Phase 1

Task Manager makes project work a durable Orion object before any automated workflow
or implementation system is introduced.

## Commands

```text
task create "<goal>"
task list
task show <task-id>
task approve <task-id>
task cancel <task-id>
task events <task-id>
task link-plan <task-id> <team-task-id>
```

Project Context must be initialized with `project init`. Tasks and events then remain
inside the active workspace:

```text
<workspace>/.orion/tasks.json
<workspace>/.orion/task-events.jsonl
```

Changing workspaces rebinds Task Manager, so projects do not share work state.

## Task schema

Each entry in `tasks.json` has exactly these fields:

```json
{
  "task_id": "task-20260718t120000z-a1b2c3",
  "goal": "Add Discord image generation",
  "status": "proposed",
  "approval": "pending",
  "assigned_role": "",
  "assigned_agent": "",
  "dependencies": [],
  "artifacts": [],
  "created_at": "2026-07-18T12:00:00+00:00",
  "updated_at": "2026-07-18T12:00:00+00:00"
}
```

Supported states are `proposed`, `ready`, `in_progress`, `blocked`, `completed`,
`failed`, and `cancelled`. Phase 1 commands create `proposed` tasks, explicitly approve
them into `ready`, or move non-terminal tasks to `cancelled`. The additional states are
validated storage contracts reserved for the Workflow Engine.

Approval states are `pending`, `approved`, and `cancelled`, with cross-field rules that
prevent contradictory combinations. Dependencies must exist in the same project and
cannot be duplicated, self-referential, or cyclic.

## Task events

Every mutation appends one strict event containing an event ID, task ID, event type,
actor, previous and current status, concise detail, and a timezone-aware timestamp.
Event types are `created`, `approved`, `cancelled`, and `team_plan_linked`. Their
transitions are semantically validated, event IDs must be unique, and referenced tasks
must exist.

Task snapshots use atomic replacement. Orion's API only appends to the JSON Lines event
file; it never rewrites prior events. Invalid task or event documents are reported and
left unchanged.

## AI Team artifacts

`task link-plan` verifies that the referenced AI Team task reached `Awaiting Approval`,
then records its ID and goal as an `ai_team_plan` artifact. The AI Team task remains in
its external audit store under `~/.orion/team/tasks/`; Task Manager stores only the
reference and summary.

## Safety boundary

Task approval means “ready for a future workflow.” It does not run an AI role, invoke
Codex, expose tools, modify files, run tests, create branches, commit, push, or open a
pull request. Phase 1 has no background worker and performs no automatic transitions.
Codex Bridge approvals are separate immutable approvals for persisted AI Team plans;
approving a project task or linking a plan never triggers that bridge.
