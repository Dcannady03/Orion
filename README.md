# Orion

Orion is a local-first personal intelligence operating system. It combines AI,
memory, project knowledge, plugins, discovery, and approval-based actions behind a
cohesive command-line companion.

## Current release

**v0.7.0 — Conductor**

Conductor adds persistent role-based routing for Architect, Engineering Reviewer,
Implementation Engine, Tester, and Documentation Reviewer. Planning models and
execution engines are independently configurable, validated before use, and reported
with their actual assignment, fallback, usage, cost, and duration metadata.

AI Team assignments live in update-safe external user configuration. Dynamic planning
roles reuse Orion's provider routing policy, implementation remains fail-closed when
its engine is unavailable, and Gatekeeper's immutable approval, workspace sandbox,
Awaiting Review, and rollback boundaries remain unchanged.

The unreleased Automatic Validation milestone turns the configured Tester into a real
post-implementation stage. Orion deterministically selects relevant local checks,
stores redacted immutable validation history, and still leaves every Keep Changes or
Roll Back decision to the user.

The unreleased Documentation Review milestone activates the configured Documentation
Reviewer after every Tester outcome. It classifies whether documentation is required,
checks applicable guides/help/configuration/changelog coverage, makes one bounded
planning-model review when needed, and stores immutable findings without editing files.

Weather gives Orion live current conditions and forecasts through Open-Meteo, with no
API key required. It also plugs into Morning Star through the provider architecture:

```text
Today's Briefing
--------------------------------------------------
  [WX] Weather: 75°F and clear sky; high 96°F, low 64°F
  [OK] Workspace: Orion is ready
  [OK] AI: ollama:qwen3.6:35b is connected
```

Weather failures are isolated, so a network outage never prevents Orion from starting.

## Quick start

```powershell
python -m pip install -r requirements.txt
python -m orion.main
```

See `docs/USER_GUIDE.md` for the living command guide and `docs/CONFIGURATION.md`
for external user settings, including AI Team role assignments.

## Core abilities

```text
help                         Show Orion's abilities
status                       Show the system dashboard
briefing                     Refresh the current briefing
weather                      Show live conditions and today's forecast
weather tomorrow             Show tomorrow's forecast
weather <location>           Check another location
network status               Check router and Internet connectivity
network watch [seconds]      Monitor outages and latency in the background
network report               Show current network monitoring statistics
network stop                 Stop monitoring and save the final summary
ask <question>               Talk to the configured AI provider
task create "<goal>"         Create a proposed project task
task list                    List project-local tasks
task show <task-id>          Show task state and artifacts
task approve <task-id>       Explicitly approve a proposed task
task cancel <task-id>        Cancel a non-terminal task
task events <task-id>        Show append-only task progress
task link-plan <id> <plan>   Link a reviewed AI Team plan artifact
agent list                   Show configurable AI agents
agent show <name>            Inspect an agent's instructions and permissions
agent create                 Create a planning-safe custom agent
agent enable <name>          Enable an agent
agent disable <name>         Disable an agent
agent test <name>            Run one bounded structured-output test
team plan "<goal>"           Plan, then offer explicit Y/N/D approval
team plan --manual "<goal>"  Plan without an interactive approval prompt
team roles                   Show AI Team role assignments
team role show <role>        Inspect one model or engine assignment
team role set <role> <assignment> Persist a provider:model or engine
team role reset <role>       Restore the role's dynamic default
team status <task-id>        Reopen a persisted AI Team plan
team approve <task-id>       Approve this plan hash for the active workspace
team implement <id> <approval-id> Run one bounded local Codex execution
team run <run-id>            Show implementation, validation, and docs review
team test <run-id>           Rerun validation, then documentation review
team test last               Validate the newest eligible workspace run
team docs <run-id>           Rerun Documentation Review only
team docs last               Review the newest eligible workspace run
team docs show <run-id>      Show the latest documentation findings
team rollback <run-id>       Restore a run when affected files are unchanged
execution status             Detect usable local execution engines
workspace                    Inspect the active workspace
files                        List workspace files
code tree                    Inspect the source tree
remember <key> <value>       Store session memory
index build                  Build the structural knowledge index
apps scan                    Discover installed applications
apps find <name>             Search the application catalog
app alias <alias> = <name>   Teach Orion an application alias
open <application>           Safely launch an application
settings                     Show Companion settings
trust list                   Show trusted applications
```

Use the Up/Down arrows for command history and Tab for command/application
completion in supported terminals.

## Network Watch plugin

The built-in Network Watch plugin distinguishes local gateway failures from likely
Internet or ISP outages by monitoring the router, Cloudflare, and Google. Run
`network status` for a one-time check or `network watch` to begin background
monitoring. Reports include outages, packet loss, average latency, peak latency, and
a plain-language diagnosis. JSON Lines logs are stored outside the installation at
`~/.orion/logs/network/`.

Use `network config` to review the current targets and timing settings.

## Safety model

All operating-system actions use Orion's Action framework. Policies can allow,
require approval, or deny an action. Approval decisions and execution results are
auditable. “Always allow” trust is narrowly scoped and stored per project workspace.

## Project structure

- `orion/core` — runtime, configuration, profile, and routing
- `orion/intelligence` — Brain, identity, intents, and AI providers
- `orion/agents` — strict external agent definitions and registry
- `orion/actions` — action models, policies, execution, and history
- `orion/services` — workspace, discovery, Companion, Morning Star, and Weather services
- `orion/conversation` — persistent conversation context
- `orion/knowledge` — structural workspace index
- `orion/plugins` — plugin contracts and lifecycle
- `tests` — automated regression suite
- `docs` — architecture, constitution, roadmap, changelog, and release notes

## Testing

Every feature follows [Orion's Definition of Done](docs/DEFINITION_OF_DONE.md), which
requires code, focused and full tests, help, the living User Guide, feature-specific
documentation, changelog updates, manual verification, and a final safety audit.

```powershell
python -m unittest discover -s tests -v
```

The current codebase contains **362 passing tests**.

## Roadmap

The active development milestone is **Email Phase B: Approval-Gated Mail Actions**,
building on Courier's read-only provider-neutral foundation.
See `docs/ROADMAP.md` for the complete plan.

## v0.3.6.2 — Constellation Polish

- Added `calendar enable <provider>` and `calendar disable <provider>`.
- Added guided `calendar configure google|microsoft`.
- Calendar provider settings now persist to `config/default.yaml`.
- Both Google and Microsoft providers remain registered so they can be enabled without restarting or editing YAML.


## First Contact

A clean installation now begins with a guided, conversational setup instead of a
configuration error. Orion collects the user's identity, location, timezone, default
workspace, AI providers, routing profile, and initial service choices through the same
configuration and provider services used by normal Orion commands.

First Contact supports Ollama/local AI, OpenAI, Google Gemini, multiple-provider
routing, or skipping AI setup. Ollama is probed for installed models. Cloud credentials
are hidden, verified before saving, and stored only in external Orion Vault. Failed or
cancelled provider setup preserves existing credentials and the active provider.

Run the experience manually at any time:

```powershell
python -m orion.main --first-contact
```

When rerun, existing profile, workspace, credentials, provider selection, and service
settings appear as defaults and remain unchanged unless the user explicitly changes
them. Backups use the `.before-first-contact` suffix. The final summary shows the
active model, other connected providers, routing, workspace, services, and execution
engines. ChatGPT Desktop is reported separately because it is not a CLI execution
engine. See `docs/FIRST_CONTACT.md` for the complete flow.

### Change the active Ollama model

Orion can scan the local Ollama server and switch models without restarting:

```text
Orion> change ollama model
```

The numbered picker marks the current model, saves the new selection to `config/default.yaml`, and activates it immediately. `ollama model` is available as a shorter alias.


## AI Control Center

Manage local Ollama models without editing YAML or restarting Orion:

```text
Orion> ai status
Orion> ai models
Orion> switch to qwen3.5:9b
Orion> use the fastest model
Orion> ai profile coding
Orion> ai benchmark
```

AI profiles and model choices persist in `config/default.yaml`. The quick benchmark is opt-in because loading several models can use substantial RAM and VRAM.

## Orion Home

Orion opens to a provider-neutral personal command center. Refresh it at any time:

```text
Orion> home
```

Home cards are contributed independently by Weather, Calendar, Workspace, AI, Applications, and future services.


### Current vs. default AI model

Use `ai use <model>` or `change ollama model` to switch immediately. Orion will ask whether the selection should be saved as the default for future launches. `ai status` shows both the current and default model.

## Reliability and Project Grounding

Orion preloads a newly selected Ollama model before returning to the prompt, reducing first-request failures after a model switch. `project status` reports live workspace metrics, and AI context automatically rebuilds a stale index while treating current workspace facts as authoritative over older conversation history.

```text
project status
ai use qwen3.5:9b
```

## AI Federation (Polaris)

Orion can now use Ollama, OpenAI, or Google Gemini through one provider-neutral Brain.

```text
Orion> ai providers
Orion> ai provider configure openai
Orion> ai provider configure gemini
Orion> ai provider use gemini
Orion> ai provider models gemini
```

API keys are not stored in product or user configuration. Orion uses
`OPENAI_API_KEY` or `GEMINI_API_KEY` when available, or the external
`~/.orion/vault/vault.yaml` when the user explicitly chooses interactive
configuration. Candidate credentials are verified before Orion Vault replaces any
working credential.

## Adaptive AI Routing (Sentinel)

Sentinel learns from real provider and model performance without storing prompts or
responses. Orion keeps a rolling window of the 100 most recent outcomes for each
provider/model pair in `~/.orion/cache/ai-routing-stats.json`. After a configurable
minimum sample count, unhealthy models are moved behind healthier fallbacks while
the selected routing profile continues to determine task suitability. Persisted
errors are reduced to safe categories rather than full provider messages.

```text
ai stats
ai stats clear
ai health
ai route status
ai route explain last
```

## Task Manager Phase 1

Orion stores first-class project work in `.orion/tasks.json` and appends structured
progress records to `.orion/task-events.jsonl`. Tasks carry explicit status and
approval state, optional role and agent assignments, dependencies, linked artifacts,
and timezone-aware timestamps.

```text
task create "Add Discord image generation"
task list
task show <task-id>
task approve <task-id>
task cancel <task-id>
task events <task-id>
task link-plan <task-id> <team-task-id>
```

Approval moves a proposed task to `Ready` but starts nothing. AI Team plans can be
linked only as reviewed artifacts, and Task Manager itself has no automatic runner or
state transitions. Codex Bridge is a separate, explicit AI Team approval path and does
not silently execute linked project tasks. The event stream remains the foundation for
future workflow and streaming-progress consumers. See `docs/TASK_MANAGER.md` for the
strict schemas and lifecycle.

## Agent Registry Phase 1

Orion now separates workflow roles from the agents assigned to perform them. Agent
definitions are strict YAML files under `~/.orion/agents/`, so custom specialists and
their provider, model, instructions, tools, limits, and permissions survive application
updates. Orion creates external Architect, Engineer, and Reviewer definitions once and
never overwrites user edits.

```text
agent list
agent show security-reviewer
agent create
agent enable security-reviewer
agent disable security-reviewer
agent test security-reviewer
```

`agent test` makes exactly one provider call and requires strict structured JSON.
Declared tools and permissions are visible but inert: Phase 1 does not expose tools,
modify files, run commands, or perform Git operations. See
`docs/AGENT_REGISTRY.md` for the YAML schema and safety contract.

## AI Team Phase 1

Orion can coordinate specialized roles without starting an open-ended agent loop.
The Architect creates a strict JSON plan, the Engineering Reviewer returns consolidated
implementation steps, and Orion persists the task under `~/.orion/team/tasks/` before
stopping for approval.
Both role output and persisted task files use strict schemas: missing, malformed, or
unknown fields are rejected instead of being silently accepted.

```text
team
team roles
team role show architect
team role set architect openai:gpt-5
team role reset architect
team plan "Add OpenAI image generation"
team plan --manual "Plan without prompting"
team status <task-id>
```

The persistent role registry distinguishes planning models (Architect), validation
roles (Engineering Reviewer, Tester, and Documentation Reviewer), and execution engines
(Implementation Engine). Planning roles accept `ollama:model`, `openai:model`, or
`gemini:model`; the implementation and tester defaults are `codex`. Dynamic planning
defaults follow Orion's active provider and existing routing policy, with any actual
fallback reported in the task artifact. Unavailable implementation engines fail closed.
Assignments are stored in external user configuration, never Vault or project files.

The planning phase makes exactly two AI calls and cannot implement code or execute
tools. In Orion's interactive console, the completed plan then offers `Y`, `N`, or
`D`etails. Only explicit `Y` or `Yes` creates the immutable approval and immediately
starts the bounded Codex Bridge run. `N`, empty input, and Ctrl+C leave the workspace
unchanged. `team plan --manual` and noninteractive router callers retain the separate
approval commands. Token usage is shown as an estimate. Cost is shown when rates are
configured under `team.pricing`; local Ollama defaults to zero cost. See
`docs/AI_TEAM.md` for role configuration and the persisted task schema.

After a successful implementation, the configured Tester runs a separate automatic,
bounded validation stage. Python changes receive compile validation and targeted tests
when they can be identified; broad or unmatched Python changes use full discovery.
Changed JSON, YAML, TOML, and Markdown files receive relevant deterministic checks.
Orion also verifies expected created/deleted files, exact snapshot state, and protected
workspace metadata. Validation never edits implementation files, commits, accepts, or
rolls back a run.

After validation—whether Passed, Warnings, Failed, Unavailable, or Error—the configured
Documentation Reviewer runs as a separate planning-model role. Orion supplies only a
bounded sanitized plan, implementation/file summaries, validation summary, known
command/configuration changes, project rules, and applicable documentation. The stage
reports Passed, Warnings, Failed, Not Required, Unavailable, or Error and always leaves
the final decision to human review. It cannot edit documentation or source, run tools,
consume approvals, access Vault/OAuth data, or perform Git actions.

## Codex Bridge Phase 1

Codex Bridge turns one persisted AI Team plan into one bounded local implementation
run. Interactive `team plan` can create and consume the approval after an explicit
`Y`; the manual commands remain available for scripting and recovery. Both paths use
the same approval and execution services. `team approve` creates an immutable external
approval containing the plan snapshot, SHA-256 hash, and exact active workspace.
`team implement` reloads and re-hashes the plan before invoking `codex exec`; changed
plans, changed workspaces, missing approvals, and approval replay are rejected before
the process starts.

```text
team approve <team-task-id>
team implement <team-task-id> <approval-id>
team run <run-id>
team test <run-id>
team test last
team docs <run-id>
team docs last
team docs show <run-id>
team rollback <run-id>
```

The active folder may be a Standard workspace with no Git repository or a Git workspace,
including a repository subdirectory. Git adds repository, branch, and commit metadata;
it never expands Codex beyond the active folder. Orion sets Codex's noninteractive
approval policy only after validating its own immutable one-use approval and supplies
both the `--sandbox workspace-write` flag and strict
`sandbox_mode="workspace-write"` compatibility config. On native Windows, the isolated
child also explicitly selects Codex's preferred `windows.sandbox="elevated"` mode;
Orion does not reload the user's broader Codex configuration. Network access, web search,
extra writable roots, MCP, apps, plugins, hooks, and sub-agents remain unavailable.

Before Codex starts, Orion captures a bounded external baseline. Afterward it verifies
the structured file list against actual created, modified, and deleted files, writes a
redacted unified text diff plus binary metadata, invokes the configured Tester and
Documentation Reviewer, and stops at `Awaiting Review` with independent implementation,
validation, and documentation statuses. A validation or documentation failure is
evidence for human review, not permission to repair or roll back automatically.
`team test` creates another immutable validation attempt followed by Documentation
Review; `team docs` reruns only Documentation Review. Neither reruns implementation or
consumes approval.
`team rollback` restores saved preimages only when affected files have not changed
again. No Team command initializes Git, creates a branch, commits, resets, pushes,
merges, tags, or opens a pull request. See `docs/CODEX_BRIDGE.md` for the complete
security and persistence contract.

## Execution Engine Discovery

`execution status` separates runnable CLI engines from Codex Desktop and ChatGPT
Desktop. Orion detects Codex, Claude, and Gemini command-line tools, both OpenAI
desktop applications, and its current Python runtime. A CLI must pass a bounded
`--version` probe before it is Ready; a path that exists but cannot launch is reported
as Installed but not executable.

On Windows, one reusable resolver checks extensionless, `.cmd`, `.exe`, and `.ps1`
forms, preferring a working `.cmd` or `.exe` wrapper. If PATH is incomplete, it also
checks `%APPDATA%\npm` and the bounded result of `npm prefix -g`. Orion safely invokes
npm `.cmd` wrappers through the Windows command interpreter with fixed arguments and
uses the exact validated path for Codex Bridge—there is no second bridge lookup.

Codex Desktop and ChatGPT Desktop are detected independently. `OpenAI.Codex` is never
reported as ChatGPT, desktop apps remain informational, and ChatGPT Desktop displays
`CLI Support: No`. Claude and Gemini discovery prepares future adapters, but Codex
Bridge Phase 1 still supports only a Ready Codex CLI. See
`docs/EXECUTION_ENGINES.md` for status and diagnostic details.

## Orion Vault

Polaris stores cloud-provider credentials outside normal configuration through a centralized Vault.

```text
vault
vault add gemini
vault add openai
vault health
vault remove gemini
```

The local vault is stored at `~/.orion/vault/vault.yaml`, excluded from Git, and written with owner-only permissions where supported. Environment variables (`GEMINI_API_KEY` and `OPENAI_API_KEY`) still take precedence. Existing legacy credentials and application-update backups are migrated without overwriting current values. Native Windows Credential Manager, macOS Keychain, and Linux Secret Service backends are planned behind the same Vault interface.

## Orion Connect

Orion Connect unifies communication services behind one center.

```text
connect
connect health
email status
email providers
email connect gmail
email connect microsoft
email disconnect gmail
email disconnect microsoft
email accounts
email inbox [gmail|microsoft]
email unread [gmail|microsoft]
email search "<query>" [gmail|microsoft]
email read <provider:message-id>
email thread <provider:message-id>
email summarize [gmail|microsoft]
connect add discord
discord send <message>
```

Gmail uses Google OAuth with `gmail.readonly`. Outlook and Microsoft 365 use Microsoft
Graph with `Mail.Read`, `User.Read`, and `offline_access`. Mail token caches are stored
outside the application under `~/.orion/tokens/` with owner-only permissions where the
platform supports them. They are separate from Calendar tokens so incremental Mail
consent and disconnect cannot damage a working Calendar connection.

Courier is intentionally read-only. It never downloads attachments or renders raw HTML.
`email send`, reply, forward, archive, trash, mark-state, provider-saved draft, and
attachment-download operations remain disabled until Phase B can bind a one-use,
persisted approval to the exact provider, account, recipients, subject, complete body,
attachments, and action. Installing the Outlook desktop application does not authorize
Orion to access Microsoft mail.

## Two-Way Discord Interface

Orion can run a Discord bot beside the CLI so approved users can talk to the same Orion Brain from Discord.

```text
connect add discord bot
discord bot status
```

Create a bot in the Discord Developer Portal, enable **Message Content Intent**, invite it to your server, then provide the bot token and your numeric Discord user ID. Start the interface with:

```powershell
python -m orion.main --discord
```

Orion replies to direct messages and server messages that mention `@Orion`. Messages from users not listed in `connect.discord_bot.allowed_user_ids` are refused. The existing Discord webhook remains available for approval-gated outbound notifications.

## Restricted two-way Discord interface

Configure the bot with:

```text
connect add discord bot
```

Orion asks for a bot token, approved user IDs, allowed channel IDs, and optional required human role IDs. Direct messages are limited to approved users. Server replies require an approved user, an allowed channel, an `@Orion` mention, and—when configured—one of the required roles.

After configuration, the Discord interface starts automatically with Orion. It can be toggled with:

```text
connect enable discord bot
connect disable discord bot
discord bot status
```

Discord channel permissions should still restrict the Orion bot role to only the intended channel. Orion's internal allowlists provide a second security layer.

## Shared Orion request routing

CLI and Discord requests now use the same Orion request router. Live weather and calendar questions are answered by Orion services first; general questions fall back to the active AI provider.

If the optional Discord package is missing, Orion offers to install it with the active Python interpreter instead of terminating with a traceback.

## Connect OpenAI

Orion can connect to the OpenAI API while keeping the API key outside normal configuration and source control.

```text
ai connect openai
ai test openai
ai provider models openai
ai provider use openai
ai disconnect openai
```

`ai connect openai` prompts for the key with hidden input, stores it in Orion Vault, verifies the connection, discovers models, and optionally makes OpenAI active. `ai test openai` verifies authentication without generating an AI response.


## Safe Package Updates

Stable Orion installations update without Git:

```text
update check
update
update rollback
```

Orion downloads a pinned GitHub package, backs up the current application, replaces application files, and leaves `~/.orion` untouched. Git commands remain available in development workspaces.
