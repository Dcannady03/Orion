# Orion Service Registry

Orion's `ServiceRegistry` is the canonical source for shared runtime services.
It prevents global state and lets future skills discover dependencies through a
stable interface.

## Registered services in v0.2.0

- `workspace` → `WorkspaceManager`
- `session_memory` → `SessionMemory`
- `code` → `CodeSkill`
- `agents` → `AgentRegistry` (external YAML agent definitions)
- `team` → `TeamOrchestrator` (bounded Architect and Engineer planning)
- `codex_bridge` → `CodexBridge` (approval-bound local implementation runs)
- `execution_engines` → `ExecutionEngineService` (shared CLI resolution/probing and independent desktop-app detection)
- `task_manager` → `TaskManager` (strict project work and progress events)
- `provider_manager` → `ProviderManager` (Ollama/OpenAI/Gemini federation and activation)
- `vault` → `VaultService` (external credential verification and persistence)
- `ai_routing` → `AIRoutingService` (provider-neutral routing profiles and fallback)
- `email` → `EmailService` (normalized read-only Gmail and Microsoft Mail)
- `connect` → `ConnectService` (provider-neutral communication health and Discord)

`WorkspaceManager` owns the active Standard/Git capability snapshot and path boundary.
`CodexBridge` owns a `WorkspaceSnapshotService` for bounded external baselines,
deterministic created/modified/deleted review, redacted diffs, and conflict-safe rollback;
it also owns `AutomaticValidationService` for deterministic, read-only Tester attempts
and immutable validation history. Neither service is a second workspace selector or Git
service.

## Usage

```python
workspace = orion.services.get("workspace")
memory = orion.services.get("session_memory")
code = orion.services.get("code")
agents = orion.services.get("agents")
tasks = orion.services.get("task_manager")
codex_bridge = orion.services.get("codex_bridge")
execution_engines = orion.services.get("execution_engines")
email = orion.services.get("email")
```

Existing attributes remain available for compatibility:

```python
orion.workspace_manager
orion.session_memory
orion.code_skill
```

## Waypoint project memory

`project_context` owns portable `.orion/memory.db` checkpoints and mandatory project rules. Rules are scoped to the active workspace and included in AI context.

- `knowledge_index` → `KnowledgeIndex` (portable workspace structure map)
