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

## Orion v1.0 Command Center foundation

`CommandCenterService` is registered as `command_center` beside the existing agent,
team, task, provider, and workspace services. It is the application boundary for one
personal organization, departments that reference existing agent IDs, high-level jobs,
append-only safe activity, the interface-neutral snapshot, and read-only diagnostics.
It contains no provider client, execution engine, terminal renderer, or GUI logic.

`FileCommandCenterRepository` persists versioned YAML organization, department, and
job records plus JSON Lines activity under `OrionPaths.command_center`
(`~/.orion/command-center/`). Complete YAML records are validated, written to an
owner-restricted unique temporary file, flushed, and atomically replaced. Activity
events are validated, ordered, duplicate-resistant, flushed appends. Invalid,
malformed, or unsupported records are reported and never reset. The repository uses a
process-local lock; multiple concurrent Orion processes writing one user root remain
unsupported.

Membership and assignment resolve through the production `AgentManager`, preserving
permanent/workspace scope, enabled state, routing preferences, capabilities, and
central permissions. Departments store only IDs. Missing or disabled historical
references remain visible as snapshot/doctor warnings and are never repaired by
deleting an agent or rewriting user data.

The validated job transition graph separates creation, queueing, planning, approval,
running, review, completion, failure, pause, and cancellation. Creating a job invokes
nothing. Entering Awaiting Approval records a pending state; Running is rejected until
the existing Orion approval has explicitly resolved it. The registered
`command_center_jobs` adapter lets AI Team and future engines report state through the
same service without importing provider, Codex, or terminal types.

The v1 snapshot is a detached JSON-safe dictionary with organization and department
summaries, agent grouping/counts, active/queued/approval/review/completed jobs, recent
safe activity, optional local provider/routing health, an optional path-minimized
workspace summary, and sorted reference warnings. Full goals and absolute workspace
paths are omitted from job summaries. Future interfaces must consume this service
contract rather than parse storage.

See `ORION_V1_COMMAND_CENTER.md` for schemas, lifecycle, CLI, security, migration, and
the staged roadmap to a desktop and remote Command Center.


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

## Provider-neutral Image Center

`ImageService` is the only image-generation coordinator registered in Orion's service
registry. Its `ImageProviderRegistry` resolves a selected provider independently from
the Brain's text provider and reports local readiness without making a paid API call.
The first production adapter is `OpenAIImageAdapter`; it lazily imports the official
OpenAI client, reads the existing `openai` secret through `SecretStore`, and normalizes
one encoded or temporary-URL provider response into validated bytes. Provider URLs,
raw responses, credentials, and base64 payloads never cross the service boundary.

Strict `ImageGenerationRequest`, `ImageGenerationResult`, and `ImageArtifact` schemas
bound prompt length, count, size, output type, status, metadata, and paths. Requests
pass deterministic content boundaries and a process-wide concurrency semaphore.
Provider exceptions become stable safe categories. `ImageStore` atomically persists
immutable results, SHA-256-verified artifacts, and bounded history under
`OrionPaths.images` (`~/.orion/images/`), with untrusted paths resolved against that
root. Application updates and workspace changes cannot replace this state.

Generation has no workspace write capability. `image save` first constructs a
path-confined, no-overwrite copy plan for the active workspace, then submits the exact
image ID, destination, size, and hash through `ActionService`'s `REQUIRE_APPROVAL`
policy. Execution revalidates workspace identity, source hash, destination boundary,
protected metadata, symlinks, and non-existence before the atomic copy. This approval
is unrelated to AI Team approval and grants no parent-directory access.

`DiscordBotInterface` retains the existing inbound bot/user/channel/role/DM/mention
policy as the outer gate. A deterministic intent detector routes only explicit image
requests to the same registered service when the separate image feature and user list
permit it. Per-user/channel cooldowns, concurrent-channel bounds, prompt/upload limits,
and `asyncio.to_thread` keep provider work off the event loop. Discord receives only a
validated attachment plus provider-neutral status; it never receives a local artifact
path and never causes an implicit workspace copy.

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

`TeamRoleRegistry` is registered as `team_roles` and resolves five persistent workflow
assignments: Architect, Engineering Reviewer, Implementation Engine, Tester, and
Documentation Reviewer. Model roles validate the provider and model through
`ProviderManager`; dynamic planning assignments reuse `AIRoutingService` fallbacks.
Execution roles validate the installed CLI and Orion adapter through
`ExecutionEngineService` and fail closed when unavailable. User overrides are written
by `ConfigManager` to external local configuration, not to project or Vault data.

`TeamOrchestrator` is a bounded planning service registered as `team`. It makes one
Architect provider call, validates the returned JSON schema, passes that structured
artifact to one Engineering Reviewer call, and uses the reviewer recommendations as
the consolidated final plan. There are no unbounded retries, implementation tools,
code mutations, or pull-request actions in the planning phase.

Each `TeamTask` contains artifacts, role-to-role messages, usage estimates, assignment
snapshots, the final plan, and an approval status. Produced role artifacts also retain
requested and actual assignment, fallback reason, token/cost data, and duration.
`TeamTaskStore` writes one JSON document per task beneath
the external user-data path `~/.orion/team/tasks/`, using atomic replacement and
owner-only file permissions where supported. Save and load both enforce the exact task
and nested-record schemas, including identity, status, timezone-aware timestamps,
messages, role usage, and role-output fields.

## Reusable Agent System

`AgentManager` is registered as `agents` and coordinates two injected
`AgentRepository` instances: permanent definitions under `~/.orion/agents/` and the
active workspace's definitions under `.orion/agents/`. Repositories enforce scope,
identity, containment, symlink rejection, bounded YAML, validation, and atomic
replacement. The manager owns lifecycle operations, promotion, copying, cross-scope
conflict detection, name/ID resolution, templates, and provider/model resolution.

`ManagedAgentDefinition` is the version 1 provider-neutral schema. Role, execution,
capability, permission, workspace-access, and metadata records are separated.
Application resources supply ten copyable templates through `AgentTemplateRegistry`.
Legacy Phase 1 definitions remain readable and unknown fields do not prevent startup.
Credentials remain in Vault and credential-shaped definition values are rejected.

Explicit agent jobs extend rather than replace `TeamOrchestrator`. An ordered selection
activates a generic sequential planning path. `AgentPromptBuilder` places Orion rules
first, marks agent text and earlier outputs as untrusted, and includes the goal,
workspace, job, specialty, personality, instructions, responsibility, permissions,
and previous structured contributions. Calls retain the strict output schema and
receive no tool dispatcher.

`TeamTask.selected_agents` preserves order. `agent_snapshots` stores a sanitized
effective definition, responsibility, requested and actual provider/model, and source
definition timestamp for each selected agent. This prevents later profile edits from
changing history. `WorkspaceTeamDraftStore` supports incremental `team create`,
`team agents add`, and `team run` flow beneath workspace metadata.

Provider/model candidates follow job override, agent preference, `AIRoutingService`,
and configured-provider precedence. `ProviderManager` supplies current readiness and
model validation, while `AIProviderFactory` creates the provider. Runtime fallback is
allowed only under current routing policy and is written to artifacts and snapshots.
Approved implementation and validation continue through existing execution engines,
approvals, Codex Bridge, Automatic Tester, Documentation Review, rollback, and human
review.

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
external artifacts. `AutomaticValidationService`, owned by `CodexBridge`, then resolves
the persisted Tester role and derives a deterministic validation plan from the actual
change set. Language parsers run in-process; allowlisted Python compile/test commands
run with isolated temporary state, blocked network/nested processes, bounded output,
and a child write guard. A second snapshot comparison detects any Tester mutation.

Each strict `ValidationAttempt` is immutable under the external run directory while
`run.json` keeps the latest attempt and bounded history paths. Existing schema-v2 run
records tolerate missing validation fields. Implementation and validation status remain
separate.

`DocumentationReviewService`, also owned by `CodexBridge`, receives each completed
validation outcome. It first classifies documentation need from plan/change metadata,
builds an applicable bounded inventory, reuses Markdown checks, and audits command,
help, configuration, changelog, architecture, and safety coverage. Required work then
uses `TeamRoleRegistry.planning_candidates("documentation", ...)` and the existing
provider factory for one strict structured-output call. Only sanitized plans,
implementation/file summaries, validation summaries, command/config changes, project
rules, and bounded documentation excerpts enter the prompt; raw diffs, source bodies,
credentials, environment variables, Vault/OAuth/mail data, and unrelated workspaces do
not.

Each strict `DocumentationAttempt` is immutable under
`documentation/documentation-NNNN.{json,log}`. `run.json` independently retains its
latest summary and history, and tolerant schema-v2 loading treats older records as
Documentation Not Run. Snapshot comparisons before and after the provider call enforce
the read-only boundary. The reviewer has no file, shell, execution-engine, Git,
approval, role, repair, acceptance, or rollback tools.

Implementation, validation, and documentation statuses remain independent. Every
outcome moves the completed run to human review. `team rollback` restores preimages
without Git only when affected paths still match the run. Invalid output or provider
failure records only a sanitized category. There is no repair loop, Documentation
Writer, Task Manager transition, Git write, automatic acceptance, or release action in
this phase.

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
