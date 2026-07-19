# Orion Architecture

Orion is organized around a small core, a shared Service Registry, explicit services, and capability-focused skills.

## Dependency flow

`Orion Core -> Service Registry -> Services/Skills -> Providers`

The core initializes shared components. Consumers discover them through the registry rather than globals.

## First Contact onboarding

`FirstContact` runs before the complete `Orion` service graph, but it does not maintain
an onboarding-only provider stack. It constructs the normal layered `ConfigManager`,
`ProviderManager`, `VaultService`, `AIRoutingService`, read-only
`ExecutionEngineService`, and the canonical `EmailService` factory against the same
external user-data paths used at runtime.
Profile fields are merged into the existing profile, and configuration changes use
`ConfigManager.set()` and `save()` rather than replacing the complete document.

Cloud setup is a two-stage Vault transaction. `ProviderManager` verifies a candidate
credential through `AIProviderFactory` using in-memory config/secret overlays. Only a
successful verification produces a `VerifiedProviderConnection`, which `VaultService`
may commit. Failed verification does not write the candidate key, enable the provider,
change its model, or change `providers.default`. Normal `vault add` and
`ai provider configure` commands use the same transaction.

Ollama discovery uses the provider manager with a candidate base URL and no persistent
change until the user confirms First Contact. Multiple-provider setup delegates routing
profile changes to `AIRoutingService.set_profile()`. Execution-engine summaries are
read-only and do not grant implementation permissions.

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

## Provider-neutral Email Phase A

`EmailService` is registered as `email` and is the only mail dependency exposed to the
CLI, Connect Center, Home, First Contact, shared request router, or future interfaces.
`GmailAdapter` and `MicrosoftGraphEmailAdapter` translate provider responses into
immutable normalized account, folder, summary, full-message, thread, attachment, and
status records. Provider access tokens never enter those models.

`GoogleInstalledAppOAuth` and `MicrosoftPublicClientOAuth` centralize the OAuth behavior
shared by Calendar and Email: non-interactive startup, explicit interactive connect,
refresh, sanitized failures, atomic external token writes, and owner-only permissions.
Mail uses scope-specific token caches separate from Calendar. This deliberately trades
one incremental consent for clean capability boundaries and lets local Mail disconnect
preserve a working Calendar connection. Google client-file configuration and Microsoft
client ID/tenant values are reused where available.

Phase A requests only Gmail `gmail.readonly` or Microsoft Graph `User.Read`, `Mail.Read`,
and `offline_access`. Message pages are capped centrally, HTML becomes safe plain text,
attachments remain metadata-only, and bounded summaries are formatted locally without
mailbox fallback to an AI provider. Home reads cached counts only and never performs a
mailbox request during startup.

Legacy direct Gmail send has been removed from the runtime path. Send, reply, forward,
provider drafts, mailbox mutations, and attachment downloads remain disabled until
Phase B adds persisted, immutable, single-use outbound approvals and safe attachment
destinations.

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
ordered final plan, and structured role artifacts. Approval is also bound to one
`WorkspaceCapabilities` snapshot, the Codex engine, active-workspace scope, and
implementation operation. Execution reloads and hashes the current persisted task,
requires the explicit approval ID, rejects replay, and receives the router's validated
engine and workspace through one immutable `ExecutionContext`.

`WorkspaceManager` classifies the selected folder as Standard or Git. Git mode records
the optional repository root, branch, and commit while keeping the active folder as the
execution boundary, so repository subdirectories remain valid. Standard mode uses
Codex's narrow `--skip-git-repo-check` option; Orion never creates a repository.

Before claiming the approval, `WorkspaceSnapshotService` captures a bounded baseline
outside the workspace. After execution it independently derives created, modified, and
deleted paths, redacted unified text diffs, and binary metadata. Structured Codex paths
must match the observed change set. Owner-only compressed preimages support rollback
only after a full post-run conflict preflight.

`LocalCodexRunner` invokes `codex exec` without a shell, sends the plan over standard
input, and supplies a strict output schema. Web search, command network, extra writable
roots, project config, MCP, apps, hooks, remote plugins, and sub-agents are disabled.
The prompt independently prohibits ignored/sensitive paths and every branch, commit,
push, merge, tag, and pull-request action.

Valid JSONL, structured output, baseline, change metadata, and bounded diff become
external artifacts and move the run to `Awaiting Review`. `team rollback` restores
preimages without Git only when affected paths still match the run. Invalid output or
process failure records only a sanitized category. There is no autonomous reviewer,
repair loop, Task Manager transition, Git write, or release action in this phase.

## Execution Engine Discovery

`ExecutionEngineService` is registered as `execution_engines`. Its reusable
`ExecutableResolver` searches and version-probes Codex, Claude, and Gemini command
forms. Windows behavior is isolated: PATH lookup includes extensionless, `.cmd`,
`.exe`, and `.ps1` forms, then falls back to `%APPDATA%\npm` and a bounded
`npm prefix -g` query. A `WindowsAppDetector` separately reads registered Appx package
identities for Codex Desktop and ChatGPT Desktop, supplemented by the application
catalog and known install locations.

Detection, CLI capability, and implementation-adapter support are independent fields.
Only Codex currently has an implementation adapter. `CommandRouter` uses the service
for `execution status` and friendly AI Team failure output. During `team implement`,
the router passes its validated immutable `ExecutionEngine` snapshot into
`CodexBridge`, avoiding a second availability probe before the exclusive approval
claim. Direct bridge callers must supply the same snapshot. The bridge runner safely
adapts `.cmd` and `.ps1` wrappers with fixed argument arrays and `shell=False`, so
Windows command-extension differences cannot create a discovery/launch mismatch.
