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
AI Team plan linking. Task Manager has no background runner or automatic state
transitions. Codex Bridge remains a separate explicit approval and execution service;
linking or approving a project task never invokes it. Workspace rebinding isolates
each project's tasks and events.


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

## Codex Bridge Phase 1

`CodexBridge` is registered as `codex_bridge` after `TeamOrchestrator`. It reads strict
`TeamTask` documents through the existing external `TeamTaskStore`, creates immutable
`PlanApproval` records, and persists `CodexRun` state through `CodexBridgeStore` under
`~/.orion/codex/`.

The approval hash covers a canonical plan snapshot containing the task identity, goal,
ordered final plan, and structured role artifacts. Approval is also bound to the
resolved active workspace. Execution reloads and hashes the current persisted task,
requires the explicit approval ID, rejects replay, and writes an `Executing` run record
before starting the local process.

`LocalCodexRunner` invokes `codex exec` without a shell, sends the plan over standard
input, supplies a strict output schema, and confines the process to the active Git
repository root. Web search, command network, extra writable roots, project config,
MCP, apps, hooks, remote plugins, and sub-agents are disabled. Codex's workspace-write
sandbox protects Git metadata, while the bridge prompt independently prohibits every
branch, commit, push, merge, tag, and pull-request action.

Valid JSONL and structured final output become immutable external artifacts and move
the run to `Awaiting Review`. Invalid output or process failure records only a
sanitized category. There is no reviewer, repair loop, Task Manager transition, Git
write, or release action in this phase.

## Execution Engine Discovery

`ExecutionEngineService` is registered as `execution_engines`. It performs read-only
host capability probes for Codex CLI, ChatGPT Desktop, Claude Code, Gemini CLI, and the
current Python runtime. CLI status requires both PATH resolution and a successful
bounded `--version` process; desktop discovery uses the application catalog and common
host locations.

Detection, CLI capability, and implementation-adapter support are independent fields.
Only Codex currently has an implementation adapter. `CommandRouter` uses the service
for `execution status` and friendly AI Team failure output. `CodexBridge` independently
requires the same capability immediately before creating its exclusive approval claim,
so UI bypass or a missing CLI cannot consume approval state.
