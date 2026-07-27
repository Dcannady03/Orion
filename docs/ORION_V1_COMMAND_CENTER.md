# Orion v1.0 Command Center

## 1. Purpose

Command Center is Orion's organization and work-control application layer. It turns
the existing reusable Agent System, AI Team, provider routing, workspace controls, and
approval services into a stable organization model that interfaces can inspect
without understanding their storage or execution details.

The first production milestone implements organization, departments, high-level jobs,
safe activity, a read-only snapshot, CLI commands, and diagnostics. It does not add a
new execution engine. Creating a job records intent only.

## 2. Product vision

Orion is the persistent coordinator for a personal AI organization. A person should be
able to define departments, place reusable agents in one or more departments, submit
goals as jobs, observe their lifecycle, review approvals and results, and use the same
organization from CLI, desktop, Discord, voice, and future mobile clients.

The organization may eventually include Engineering, Marketing, Business, Automation,
and user-defined departments. The roles within those departments remain reusable Orion
agents, not processes owned by Command Center.

## 3. Design principles

1. Orion remains the central identity, policy authority, router, and interface.
2. Existing services are referenced or adapted, never copied into Command Center.
3. Domain models contain no terminal rendering, provider clients, or GUI code.
4. All mutable organization data lives in the external Orion user-data root.
5. Job creation and job execution are separate decisions.
6. Permissions describe eligibility; they do not bypass Orion approvals.
7. Snapshots contain display-safe summaries and plain JSON values.
8. Invalid or newer records fail clearly and are never silently reset.
9. Codex and every other execution engine are optional.
10. The first implementation favors explicit, inspectable lifecycle changes over
    speculative autonomous behavior.

## 4. Orion as central coordinator

`Orion` registers `CommandCenterService` as `command_center` in the shared
`ServiceRegistry`. The service receives existing dependencies:

- `AgentManager` for permanent and workspace agent resolution;
- `ProviderManager` and `AIRoutingService` for local, read-only health summaries;
- `WorkspaceManager` for a safe workspace name and capability summary;
- `FileCommandCenterRepository` for external persistence.

`CommandCenterJobUpdateAdapter`, registered as `command_center_jobs`, is the
provider-neutral lifecycle boundary for AI Team and future engines. It delegates every
transition back to `CommandCenterService`; it cannot execute work or weaken approval
rules.

## 5. Organization model

The milestone supports one default personal organization:

```yaml
schema_version: 1
id: orion-organization
name: Orion Organization
description: Personal AI organization coordinated by Orion.
owner_profile_reference: default
created_at: 2026-07-27T20:00:00+00:00
updated_at: 2026-07-27T20:00:00+00:00
enabled: true
```

The record references the owner profile without copying names, email addresses,
credentials, or other sensitive profile fields. Orion creates the missing default
record once and does not overwrite an existing valid record.

## 6. Department model

A department groups responsibility and existing agent identities:

```yaml
schema_version: 1
id: engineering
name: Engineering
description: Plans, builds, validates, documents, secures, and releases software.
icon: engineering
agent_ids:
  - planner
  - software-engineer
workflow_policy_reference: ""
created_at: 2026-07-27T20:00:00+00:00
updated_at: 2026-07-27T20:00:00+00:00
enabled: true
```

Agent definitions are not embedded. Membership stores normalized IDs, prevents
duplicates, and permits the same agent in multiple departments. Removing membership
does not edit or delete the agent.

Four immutable application templates provide descriptions, symbolic icons, and
recommended roles:

- Engineering: Planner, Architect, Software Engineer, Tester, Reviewer,
  Documentation Writer, Security Reviewer, Performance Reviewer, Release Manager.
- Marketing: Marketing Manager, Copywriter, Content Strategist, SEO Specialist,
  Social Media Manager, Graphic Designer.
- Business: Sales Manager, Customer Support, Research Analyst, Finance Assistant.
- Automation: Automation Coordinator, Email Assistant, Calendar Assistant, Discord
  Assistant, Systems Operator.

Applying a template creates only the department metadata. It never creates agents.

## 7. Agent model and lifecycle

Command Center uses `AgentManager` and its existing versioned YAML definitions. New
membership and assignment operations require the referenced agent to exist; new job
assignments also require it to be enabled. Snapshots and doctor tolerate historical
references after an agent is disabled or deleted:

- an available agent includes ID, name, scope, and enabled state;
- a disabled agent remains visible and emits a warning;
- a missing agent becomes a safe placeholder and emits a warning.

Command Center does not grant capabilities, edit agent permissions, select tools, test
an agent, or delete an agent.

## 8. Temporary and permanent agents

Permanent agents remain under `~/.orion/agents/`. Workspace agents remain under
`<workspace>/.orion/agents/` and continue to use the Agent System's confinement and
conflict rules. Departments reference the resolved agent ID rather than duplicating
scope-specific definitions.

A workspace agent that is later promoted retains its ID and remains resolvable. If a
workspace agent disappears with its workspace, Command Center reports a missing
reference rather than rewriting the department or job.

## 9. Job lifecycle

A Command Center job is a high-level goal, broader than a provider prompt:

```yaml
schema_version: 1
id: job-...
title: Build plugin marketplace
goal: Design and implement a secure Orion plugin marketplace.
status: draft
priority: high
department_id: engineering
assigned_agent_ids:
  - architect
workspace_reference: C:\Projects\Orion
created_at: 2026-07-27T20:00:00+00:00
updated_at: 2026-07-27T20:00:00+00:00
started_at: ""
completed_at: ""
created_by: user
approval_state: not_required
current_stage: ""
progress: 0
result_summary: ""
error_summary: ""
metadata: {}
```

Statuses are validated enums:

`draft`, `queued`, `planning`, `awaiting_approval`, `running`, `paused`,
`awaiting_review`, `completed`, `failed`, and `cancelled`.

The transition graph is deliberately bounded:

- `draft` -> `queued` or `cancelled`;
- `queued` -> `planning`, `awaiting_approval`, `running`, `paused`, `failed`, or
  `cancelled`;
- `planning` -> `awaiting_approval`, `running`, `paused`, `failed`, or `cancelled`;
- `awaiting_approval` -> `queued`, `running`, `paused`, `failed`, or `cancelled`;
- `running` -> `awaiting_review`, `completed`, `paused`, `failed`, or `cancelled`;
- `paused` -> `queued`, `running`, `failed`, or `cancelled`;
- `awaiting_review` -> `running`, `completed`, `failed`, or `cancelled`;
- terminal jobs cannot transition.

Entering `running` records `started_at`. Terminal states record `completed_at`;
`completed` also records 100 percent progress. Progress is an integer from 0 through
100, cannot move backward, and does not automatically execute or complete a job.

Priorities are `low`, `normal`, `high`, and `urgent`. Approval states are
`not_required`, `pending`, `approved`, `denied`, and `cancelled`.

## 10. Activity and event model

`activity.jsonl` is append-only. Each line is a strict versioned event with:

- ID and timezone-aware timestamp;
- dotted event type and severity;
- source type and source ID;
- optional job, department, and agent references;
- a bounded summary message;
- bounded JSON metadata.

Events include organization and department creation, membership changes, job
creation/queue/status/assignment/progress/completion/failure, and approval
request/resolution. Events are ordered by timestamp and duplicate IDs are rejected.

Messages and metadata reject credential-bearing key names and common token shapes.
Full private prompts, model responses, raw provider errors, OAuth data, and secrets do
not belong in activity.

Recent reads are limited to at most 1,000 events. The durable JSONL file is capped at
20 MB by the current reader. Orion does not silently delete or rotate audit data; an
operator must archive an old file before that bound is reached. A future database
milestone may add configurable retention with an explicit migration.

## 11. Approval and permission boundaries

Entering `awaiting_approval` sets the job approval state to `pending`. A transition
from that state to `running` fails unless an external Orion approval has explicitly
resolved the job to `approved`.

`CommandCenterJobUpdateAdapter.resolve_approval()` only records the result supplied by
an existing approval workflow. It is not an approval engine. AI Team's immutable plan
hash, single-use approval, execution-engine checks, workspace binding, review gate, and
rollback behavior remain owned by their existing services.

Agent permission policies remain declarations of what an agent may request. Command
Center cannot grant network, shell, file, Git, calendar, email, image, or other access.

## 12. AI routing principles

Jobs and departments are provider-neutral. They contain no provider client, API key,
model response, Codex object, or execution command.

The snapshot asks existing provider services only for local configured/enabled status,
active model metadata, and routing profile. It makes no health-call network request.
Ollama, OpenAI, Gemini, future providers, and local execution engines remain
interchangeable behind existing Orion services.

## 13. Memory boundaries

Command Center is durable organization state, not conversational or semantic memory.
It does not copy:

- session memory;
- conversation bodies;
- knowledge-index content;
- agent prompts or instructions;
- provider responses;
- email, calendar, or Discord content;
- organization-wide embeddings.

Jobs contain the user-authored goal and bounded result/error summaries. Activity
contains identifiers and safe summaries only.

## 14. Storage layout

All runtime data uses `OrionPaths.command_center`:

```text
~/.orion/command-center/
|-- organization.yaml
|-- departments/
|   `-- <department-id>.yaml
|-- jobs/
|   `-- <job-id>.yaml
`-- activity.jsonl
```

YAML writes validate a complete record, write an owner-restricted uniquely named
temporary file, flush and `fsync`, then atomically replace the target. JSONL appends
validate and flush each complete line. Directories are created only when needed.
Symlinked storage roots, directories, and records are rejected.

The repository uses a process-local reentrant lock, matching Orion's current
single-process runtime. It does not claim cross-process transaction safety. Multiple
simultaneous Orion writers to the same user-data root are unsupported until a
cross-process lock or transactional database is introduced.

## 15. Command Center snapshot contract

`CommandCenterService.snapshot()` returns detached dictionaries, lists, strings,
numbers, booleans, and null only. The top-level v1 contract includes:

- `schema_version` and `generated_at`;
- safe organization summary;
- department summaries;
- total/enabled/disabled/referenced agent counts;
- `agents_by_department`, including `unassigned`;
- `active_jobs`, `queued_jobs`, `jobs_awaiting_approval`,
  `jobs_awaiting_review`, and `recently_completed_jobs`;
- recent safe activity;
- optional local provider/routing health;
- optional workspace name and capability summary;
- sorted warnings for missing or disabled references.

Job summaries intentionally omit the full goal and workspace path. The workspace
summary includes only its final name and capabilities, not its absolute parent path.
Lists and warning order are deterministic for the same persisted state and injected
clock.

Interfaces must consume this service contract, not parse YAML or JSONL directly.

## 16. CLI experience

The full namespace is `command-center`; `cc` is its short alias.

```text
cc status
cc snapshot [--json]
cc departments
cc department show <name-or-id>
cc department create [--name ... | --template Engineering]
cc department add-agent <department> <agent>
cc department remove-agent <department> <agent>
cc templates
cc template show <template>
cc jobs [--status <status>]
cc job create --title "..." --goal "..." [options]
cc job show <job-id>
cc job assign <job-id> <agent>
cc job status <job-id> <status> [--stage "..."]
cc job progress <job-id> <0-100> [--stage "..."]
cc job cancel <job-id>
cc activity [--limit <1-1000>]
cc doctor [--json]
```

Department and job creation are guided when no flags are supplied and noninteractive
when flags are supplied. `cc snapshot` always emits machine-readable JSON; `--json`
is accepted for explicit scripts. Job creation prints that execution was not started.

## 17. Future GUI architecture

A future desktop GUI should call the application service or a thin local API that
serializes the same snapshot. Views should dispatch explicit Command Center
operations and refresh snapshots after confirmed writes.

The GUI must not read user-data files, invoke providers, or infer approval from a
button label alone. Approval controls must call the existing approval service and then
report their result through the lifecycle adapter.

## 18. Future live dashboard

The append-only event contract is the basis for a live activity feed. A later
publisher may tail validated events and emit snapshot deltas over an authenticated
local transport.

WebSockets, background file watchers, event replay cursors, notification delivery,
agent presence, and streaming model output are not implemented in this milestone.

## 19. Future mobile and remote control

Mobile and remote clients should consume a versioned authenticated API derived from
the snapshot. Remote mutation will require explicit identity, authorization, replay
protection, approval visibility, transport encryption, and revocation.

No remote server, VPS control plane, mobile app, or public network listener is present
in this milestone.

## 20. Future scheduled automations

Future automation may create or queue jobs through a scheduler service. Schedules must
remain separate from job records and must not make `job create` executable.

Email, calendar, Discord, and systems automations must continue to use their existing
service permissions and Action approval policies. Command Center will observe their
job state; it will not inherit their credentials.

## 21. Security and privacy

- No API keys, OAuth tokens, passwords, or Vault values are stored.
- Metadata rejects secret-bearing field names and credential-shaped strings.
- Activity is summary-only and bounded.
- Snapshot job summaries omit goals and absolute workspace paths.
- Provider failures become safe availability states, not raw exceptions.
- Job creation runs no provider, tool, shell, Git, or execution engine.
- Membership removal never deletes an agent.
- Doctor is read-only and never repairs, resets, or deletes records.
- File paths are derived from validated IDs under one external root.
- Workspace and approval enforcement remain in their existing authoritative services.

## 22. Cross-platform requirements

Paths use `pathlib`, storage uses UTF-8 and platform-neutral YAML/JSON, and terminal
status uses ASCII-safe separators and markers. Atomic replacement uses `os.replace`;
permission hardening is best effort because Windows and POSIX permission models differ.

No hardcoded home directory, slash convention, shell command, Codex path, or
provider-specific executable is required. Windows, Linux, and future macOS use the
same domain and snapshot schemas.

## 23. Migration strategy

Every record carries `schema_version: 1`. Missing or unsupported versions produce
clear validation errors and are left untouched. Additive unknown fields are validated
as safe extensions, retained in memory, and written back unchanged.

Future breaking versions must provide an explicit, tested migration that:

1. reads the old record without changing it;
2. validates the complete migrated value;
3. creates a recoverable backup;
4. atomically writes the new record;
5. records a safe migration event;
6. never migrates Vault or agent content into Command Center.

This milestone has no legacy Command Center data to import and does not modify agent,
team-task, project-task, approval, Codex-run, config, or Vault schemas.

## 24. Milestone roadmap to Orion v1.0

1. **Foundation (implemented):** organization, departments, membership, job lifecycle,
   activity, snapshot, persistence, CLI, doctor, and lifecycle adapter.
2. **Workflow linkage:** explicit user-controlled links between Command Center jobs,
   project tasks, AI Team plans, approvals, and reviewed run artifacts.
3. **Local application API:** authenticated process-local snapshot and mutation
   endpoints with event cursors.
4. **Desktop Command Center:** organization tree, job board, approvals, review, and
   activity views backed by the v1 snapshot.
5. **Automation:** opt-in schedules and service-triggered jobs with existing Action
   policies.
6. **Remote interfaces:** authenticated Discord/voice/mobile summaries and narrowly
   authorized controls.
7. **v1.0 hardening:** cross-process storage coordination or database migration,
   explicit retention controls, migrations, performance budgets, accessibility, and
   release compatibility testing.

## 25. Explicit non-goals for this implementation

The foundation does not implement:

- a desktop, web, or mobile GUI;
- autonomous job execution or a background scheduler;
- parallel multi-agent execution redesign;
- full automatic synchronization with every existing AI Team run;
- live WebSockets or remote VPS control;
- organization-wide long-term memory;
- browser preview, screenshot annotation, or voice dashboard;
- avatars, a marketplace, or agent auto-generation;
- a new provider, execution engine, approval system, workspace selector, or Vault;
- Git mutation, automatic acceptance, or automatic rollback;
- repair mode for doctor;
- cross-process writes to one Command Center store.

These boundaries keep the first milestone production-safe while leaving explicit
extension points for the rest of Orion v1.0.
