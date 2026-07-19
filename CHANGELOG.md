# v0.5.9 — Canvas

- Added Standard and Git workspace capability modes without requiring or initializing Git.
- Bound immutable Team approvals to workspace capabilities, Codex, active-workspace scope, and implementation intent.
- Passed the router's validated engine and workspace capability through one immutable execution context.
- Added bounded external workspace baselines, actual created/modified/deleted detection, redacted unified diffs, and binary metadata review.
- Added conflict-safe `team rollback` using saved preimages without Git reset, checkout, staging, or commits.
- Allowed active repository subdirectories while retaining Git root, branch, and commit metadata.
- Added confirmed missing-directory creation and capability-aware Git-only command errors.
- Added Standard/Git execution, snapshot, diff, rollback, limits, security, and workspace UX coverage; 292 tests pass.

# v0.5.8 — Prism

- Replaced Ollama-only onboarding with Ollama, OpenAI, Gemini, multiple-provider, and skip choices.
- Reused ConfigManager, ProviderManager, Orion Vault, AI routing profiles, and Execution Engine discovery during setup.
- Added verify-before-commit cloud credentials so failed setup preserves working Vault entries and the active provider.
- Made forced reruns merge existing profile, workspace, provider, credential, and service settings instead of replacing them.
- Added dynamic Ollama model discovery, provider/model selection, routing selection, and execution-engine summaries.
- Replaced the obsolete First Light `setup_orion.py` scaffolder with a compatibility launcher for official First Contact.
- Added onboarding, cancellation, rerun, credential-isolation, external-Vault-path, provider-failure, command-reuse, and execution-summary coverage; 273 tests pass.

# v0.5.7.1 — Forge

- Fixed `team implement` performing a second execution-engine probe after its successful router preflight.
- Passed the router's validated engine snapshot and exact executable path directly into Codex Bridge.
- Preserved one safe pre-claim probe for direct bridge callers and all immutable approval safeguards.
- Added pass-then-fail probe regression coverage; 256 tests pass.

# v0.5.7 — Forge

- Added immutable SHA-256 approvals bound to exact persisted AI Team plans and active workspaces.
- Added single-use `team approve`, `team implement`, and `team run` execution commands.
- Added bounded local `codex exec` integration with strict workspace-write, network, tool, and Git safeguards.
- Added structured implementation, changed-file, test, risk, remaining-work, and review-note results.
- Persisted approvals, run state, JSONL events, schemas, and results under external `~/.orion/codex/` data.
- Added tamper, replay, workspace escape, protected metadata, subprocess, schema, corruption, CLI, and persistence coverage.
- Added `execution status` discovery for Codex CLI, ChatGPT Desktop, Claude Code, Gemini CLI, and Python.
- Distinguished desktop installation, PATH aliases, runnable CLIs, and Orion implementation-adapter support.
- Changed unavailable-engine failures to show detected capabilities instead of exposing a missing Codex dependency.
- Moved engine preflight before approval claiming so unavailable CLIs do not consume immutable approvals.
- Added one shared platform-aware Codex resolver and changed the bridge to launch the exact discovered executable path.
- Added Windows `.cmd`/`.exe`, non-Windows, discovery-to-launch, rendering, preflight, and approval-preservation coverage; 255 tests pass.

# v0.5.6 — Ledger

- Promoted project-local `.orion/tasks.json` into a strict first-class task store.
- Added proposed, ready, progress, blocked, completed, failed, and cancelled task states with explicit approval validation.
- Added append-only `.orion/task-events.jsonl` progress events for future workflow and streaming consumers.
- Added `task create`, `task list`, `task show`, `task approve`, `task cancel`, `task events`, and `task link-plan` commands.
- Added dependency, cycle, artifact, timestamp, transition, event-identity, and workspace-isolation validation.
- Linked reviewed AI Team plans as task artifacts without starting planning or implementation automatically.
- Added Task Manager persistence, lifecycle, corruption, CLI, and safety coverage; 229 tests pass.

# v0.5.5 — Council

- Added strict YAML-defined agents with provider, model, instructions, tools, limits, permissions, and enabled state.
- Added `agent list`, `agent show`, `agent create`, `agent enable`, `agent disable`, and bounded `agent test` commands.
- Persisted custom and built-in agent definitions outside the application under `~/.orion/agents/` without overwriting user edits.
- Separated AI Team workflow roles from assigned agents while preserving legacy role provider/model choices during first-time migration.
- Kept all declared tools and permissions inert during Phase 1 agent tests and team plans.

- Added bounded Architect and Engineer Review planning through `team plan "<goal>"`.
- Added strict JSON role outputs and structured artifact handoff between roles; unknown fields are rejected.
- Added consolidated final plans that stop at `Awaiting Approval` without modifying code.
- Added `team`, `team roles`, and `team status <task-id>` commands with completion and help.
- Persisted team tasks outside the application under `~/.orion/team/tasks/`.
- Added estimated token usage and configurable per-provider cost reporting.
- Added strict validation for persisted task identity, status, timestamps, messages, usage, and nested artifacts.
- Added bounded orchestration, persistence, schema, CLI, and safety coverage; 219 tests pass.

# v0.5.4.1 — Sentinel

- Moved the live Orion vault into persistent external user data under `~/.orion/`.
- Added automatic Discord bot token and access-setting recovery from application update backups.
- Preserved compatibility with legacy vault locations and explicit absolute configuration paths.
- Ensured recovery fills only missing secrets and never overwrites a current Discord token.
- Ensured existing local Discord settings remain authoritative over backup values.
- Added update-persistence and non-overwrite regression coverage; 195 tests pass.

# v0.5.4 — Sentinel

- Added persistent provider/model request, success, failure, and latency telemetry.
- Added `ai stats` and `ai health` reporting.
- Added `ai stats clear` for resetting adaptive-routing history.
- Added health-aware routing that demotes providers only after a minimum sample count.
- Scoped health decisions to the currently configured model and bounded history to 100 outcomes per provider/model pair.
- Added benchmark results to the same provider-neutral performance history.
- Sanitized persisted errors to categories so provider details, prompts, and responses are never stored.
- Stored performance data outside the installation under `~/.orion/cache/`.
- Added adaptive-routing, migration, privacy, and bounded-history coverage; 190 tests pass.

# v0.5.3 — Watchtower

- Added the Network Watch plugin for one-time router and Internet checks.
- Added background outage, packet-loss, and latency monitoring.
- Added local-versus-ISP failure diagnosis and JSON Lines event logs.
- Added `network status`, `network watch`, `network report`, `network stop`, and `network config`.
- Kept monitoring logs in external user data under `~/.orion/logs/network/`.
- Added command completion and automated plugin coverage; 181 tests pass.

# v0.5.2 — Navigator

- Added the AI Routing Engine with Fast, Balanced, Coding, and Research profiles.
- Added transparent per-request provider selection across Ollama, OpenAI, and Gemini.
- Added automatic fallback when a provider times out or fails.
- Added `ai route status`, `ai route on`, `ai route off`, and `ai route explain last`.
- Made Ollama request timeout configurable and set the default local model to `qwen3.5:9b`.
- Added routing decision history for explainability.
- Added automated routing and fallback coverage.

# Changelog

## v0.5.1 — Lifeline

- Replaced Git-pull self-updates with package-based application updates.
- Added external application backups and automatic restoration on failure.
- Added `update rollback`.
- Preserved `~/.orion`, `.git`, and local virtual environments during updates.
- Kept Git operations available for development workspaces only.

## 0.4.7 — Relay: Git & Update Foundation

- Added safe Git status, log, and diff commands.
- Added approval-gated pull and push operations.
- Added update checks and fast-forward-only self-updates.
- Added runtime/config backups and dirty-tree protection.
- Added active-workspace Git rebinding and dedicated installation update context.
- Added Git/update regression coverage; 158 tests pass.

## 0.4.6 — Polaris: OpenAI Connection

- Added the guided `ai connect openai` command with hidden API-key entry and Vault-backed storage.
- Added `ai test openai` to verify authentication through the Models endpoint without generating a model response.
- Added `ai disconnect openai` with safe fallback to Ollama.
- Added simple aliases (`ai configure openai`, `ai enable openai`, and `openai connect`).
- Added CLI completion, help text, and OpenAI connection regression coverage.
- Continued using the OpenAI Responses API for Orion conversations.

# Changelog

## 0.4.3 — Signal: Two-Way Discord Interface

- Added a real Discord bot interface for direct messages and `@Orion` mentions.
- Routed Discord conversations through the same Orion Brain, identity, provider, and project context used by the CLI.
- Added an explicit approved-user allowlist; unauthorized Discord accounts cannot invoke Orion.
- Added `connect add discord bot` and `discord bot status`.
- Added `--discord` startup mode and the `discord.py` runtime dependency.
- Preserved Discord webhooks for approval-gated outbound notifications.
- Added Discord interface regression coverage; 139 tests pass.


## 0.4.2.1 — Connect: Discord Webhook Compatibility

- Added an explicit Orion User-Agent to Discord webhook verification and posting requests.
- Improved errors for revoked, invalid, or rejected Discord webhook URLs.
- Added regression coverage for Discord HTTP request headers.

# Orion v0.4.2 — Connect

- Added the unified Orion Connect Center.
- Added Gmail OAuth connection, inbox, unread count, search, message preview, and approval-gated sending.
- Added secure Discord webhook configuration through Orion Vault and approval-gated posting.
- Added Connect status, health checks, command completion, and a Home briefing card.
- Added Connect service regression coverage.

# Orion Changelog

## 0.4.1 — Polaris Vault

- Added the centralized Orion Vault subsystem.
- Added `vault`, `vault add`, `vault remove`, and `vault health` commands.
- Added secure local storage at `.orion/vault.yaml` with environment-variable precedence.
- Added automatic migration from the legacy `.orion/secrets.yaml` store.
- Added provider verification and compatible-model discovery during onboarding.
- Added safe fallback to Ollama when an active cloud provider credential is removed.
- Added Vault regression coverage; 131 tests pass.

# Changelog

## 0.3.9 — True North

- Preload newly selected Ollama models before returning control to the prompt.
- Automatically rebuild missing or stale workspace knowledge indexes before AI context is assembled.
- Reject copied project metadata whose stored workspace does not match the active workspace.
- Expand `project status` with live files, Python files, classes, functions, tests, TODOs, and index freshness.

## [0.3.8.1] - 2026-07-15

### Changed
- Ollama model switches now ask whether the selected model should become Orion's startup default.
- Session-only model changes no longer overwrite `config/default.yaml`.
- `ai status` now shows current, default, and session-override model state.

## 0.3.7 — AI Control Center

- Added provider-neutral AI Control Center service.
- Added rich Ollama model metadata and capability labels.
- Added natural-language model switching and recommendations.
- Added persistent AI profiles and safe opt-in model benchmarking.
- Full regression suite: 115 tests passing.


## 0.3.6.5 — Constellation: Model Selector

- Added an interactive Ollama model scanner and numbered selector.
- Model changes persist and take effect immediately without restart.
- Added aliases, help/completion support, and regression coverage.
## [0.3.6.4] - 2026-07-15

### Fixed
- Reused fresh weather results for five minutes instead of immediately calling Open-Meteo again.
- Added cached-report fallback when a temporary weather refresh returns an HTTP 503 or other service error.
- Added regression coverage for duplicate requests and temporary weather outages.

# Changelog

## [0.3.6.2] - Constellation Polish

- Added CLI commands to enable and disable calendar providers.
- Added guided configuration for Google credential paths and Microsoft client IDs.
- Persisted provider changes through `ConfigManager.set()` and `ConfigManager.save()`.
- Kept disabled built-in providers registered for runtime activation.
- Added Calendar provider-management completion entries and regression tests.

## [0.3.6.3] - 2026-07-15

### Added
- Guided "First Contact" onboarding for clean installations.
- Safe forced rerun through `python -m orion.main --first-contact`.
- Atomic config/profile generation and automatic pre-onboarding backups.
- Five onboarding regression tests; full suite now contains 106 passing tests.

## [0.3.8] - 2026-07-15
### Added
- Orion Home startup command center and `home` refresh command.
- Expanded AI Control Center capability and model-detail panel.

### Fixed
- Prevented stale knowledge-index summaries from a different workspace from entering AI context.

## 0.4.0 — Polaris

- Added Orion's provider-federation foundation.
- Added functional OpenAI Responses API provider.
- Added functional Google Gemini generateContent provider.
- Added provider discovery, configuration, activation, and model-list commands.
- Added a separate local secret store with environment-variable precedence.
- Preserved Ollama as the default provider and retained all existing model controls.
- Added provider federation regression tests.

## [0.4.3.1] - 2026-07-15

### Added
- Restricted the two-way Discord interface to explicitly allowed channel IDs.
- Added optional Discord role requirements for server messages.
- Added `connect enable discord bot` and `connect disable discord bot` commands.
- Enabled automatic Discord gateway startup when the interface is configured and enabled.
- Expanded Discord bot status with user, channel, role, enabled, and running state.

## v0.4.3.2 — Signal: Shared Brain Routing
- Routed Discord and CLI requests through shared Orion services before AI fallback.
- Added graceful optional dependency installation for `discord.py`.

## 0.4.3.6 - Signal Access Fix
- Fixed Discord channel-wide access so approved channel members can converse without being configured as owners.
- Kept DMs and sensitive computer/account actions owner-only.
- Separated owner IDs from general channel access policy.
- Switched mention detection to Discord's native mention handling.
- Added detailed gateway diagnostics and ignore reasons.
- Normalized `@Orion ask ...` requests before shared service routing.
- Added regression coverage for channel members, owner boundaries, and sensitive requests.

## 0.4.5 — Horizon

- Expanded Home Center with Tasks, Active Project, Recent Activity, and System Diagnostics cards.
- Reused project context, action history, service registry, plugin manager, and knowledge index as canonical data sources.
- Isolated individual Home card failures so a damaged project file cannot prevent Orion startup.
- Added Home Center coverage; 152 automated tests pass.

## 0.4.4 — Horizon

- Added the first-class Home Center service and reusable snapshot contract.
- Decoupled Home rendering from core service internals for future GUI and mobile interfaces.
- Added Home Center tests; full suite passes 150 tests.

## v0.5.0 — Foundation

- Separated mutable user data from the Git installation.
- Added centralized `OrionPaths` path management.
- Moved configuration, profile, Vault, OAuth tokens, and update backups under `~/.orion`.
- Added automatic migration from the previous local configuration and repository profile locations.
- Updated self-update backups to preserve the external user-data directory without dirtying Git.
