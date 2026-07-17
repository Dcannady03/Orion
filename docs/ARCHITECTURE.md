# Orion Architecture

Orion is organized around a small core, a shared Service Registry, explicit services, and capability-focused skills.

## Dependency flow

`Orion Core -> Service Registry -> Services/Skills -> Providers`

The core initializes shared components. Consumers discover them through the registry rather than globals.

## Memory layers

- **Session Memory** is temporary and process-local.
- **Project Context** is persistent and stored inside the active workspace's `.orion/` directory.
- Future **Conversation Context** will manage references and interaction history without mixing those responsibilities.

## Project Context files

- `project.json` — project identity, phase, goal, model, and timestamps
- `history.json` — append-only project event timeline
- `tasks.json` — strict project-local Task Manager state
- `task-events.jsonl` — append-only structured task progress
- `notes.md` — human-readable timestamped notes
- `metrics.json` — derived project counts
- `settings.json` — future project-specific preferences

Workspace changes rebind Project Context so each project keeps independent, portable data.

## Task Manager Phase 1

`TaskManager` is registered as `task_manager` and bound to the active workspace. It
owns strict `ProjectTask`, `TaskArtifact`, and `TaskEvent` schemas. Task snapshots are
atomically replaced in `.orion/tasks.json`; state changes append immutable progress
records to `.orion/task-events.jsonl` for future Workflow Engine and streaming UI
consumers.

Phase 1 exposes only user-triggered creation, approval, cancellation, inspection, and
AI Team plan linking. It has no background runner, tool dispatcher, Codex adapter, or
automatic state transitions. Workspace rebinding isolates each project's tasks and
events.


## Workspace Search

`SearchSkill` is registered by the built-in Search Plugin. It depends only on the Workspace Manager, which guarantees that all searched paths remain inside the active workspace. Search remains read-only and applies resource limits before reading files.

## Conversation Context

`ConversationService` is a core registered service shared by CLI, GUI, voice, and future agents. It stores structured messages in workspace-local daily JSON files under `.orion/conversations/`. `ContextBuilder` selects recent conversation, session memory, and active project metadata for the Brain without coupling persistence to any user interface.


## Knowledge Index

`KnowledgeIndex` is a read-only structural workspace service. It inventories files and uses Python's AST to identify classes, functions, and imports. TODO markers and test files are also recorded. The resulting portable JSON index is stored under the active workspace's `.orion/` directory and is rebound whenever the workspace changes.

## Morning Star Briefing Architecture

Startup depends only on `BriefingService`. Integrations implement the `BriefingProvider`
contract and register independently. The service validates items, sorts them by priority,
and isolates provider failures. This prevents Weather, Email, Calendar, Docker, or any
future integration from becoming a hard dependency of Orion startup.

## Adaptive AI Performance

`AIPerformanceStore` persists aggregate provider/model outcomes and latency beneath
the external user-data cache. Each provider/model pair retains only its 100 most
recent outcomes, and errors are reduced to safe categories; prompt and response
content is never stored. `AIRoutingService` retains deterministic profile rules,
then uses the currently configured model's health history to demote degraded
providers after the configured minimum sample count. With adaptive routing disabled
or insufficient evidence, the original deterministic order is used.

## AI Team Phase 1

`TeamOrchestrator` is a bounded planning service registered as `team`. It makes one
Architect provider call, validates the returned JSON schema, passes that structured
artifact to one Engineer Review call, and uses the Engineer recommendations as the
consolidated final plan. There are no retries, implementation tools, code mutations,
or pull-request actions in this phase.

Each `TeamTask` contains artifacts, role-to-role messages, usage estimates, the final
plan, and an approval status. `TeamTaskStore` writes one JSON document per task beneath
the external user-data path `~/.orion/team/tasks/`, using atomic replacement and
owner-only file permissions where supported. Save and load both enforce the exact task
and nested-record schemas, including identity, status, timezone-aware timestamps,
messages, role usage, and role-output fields.

## Agent Registry Phase 1

`AgentRegistry` is registered as `agents` and owns strict YAML definitions beneath
`~/.orion/agents/`. The application seeds Architect, Engineer, and Reviewer agents only
when their files do not exist; subsequent edits remain user-owned across updates.

Workflow roles and configured workers are separate. `TeamOrchestrator` resolves each
role's `agent` assignment, then uses that agent's provider, model, and instructions
while retaining the role's fixed structured-output contract. Tool and permission
declarations are metadata only in Phase 1. Neither `agent test` nor `team plan` receives
a tool dispatcher, filesystem access, shell execution, or Git actions.
