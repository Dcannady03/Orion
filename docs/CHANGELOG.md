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

## 0.4.7 — Relay: Git & Update Foundation

## v0.5.1 — Lifeline

- Replaced Git-pull self-updates with package-based application updates.
- Added external application backups and automatic restoration on failure.
- Added `update rollback`.
- Preserved `~/.orion`, `.git`, and local virtual environments during updates.
- Kept Git operations available for development workspaces only.

- Added safe Git status, log, and diff commands.
- Added approval-gated pull and push operations.
- Added update checks and fast-forward-only self-updates.
- Added runtime/config backups and dirty-tree protection.
- Added active-workspace Git rebinding and dedicated installation update context.
- Added Git/update regression coverage; 158 tests pass.


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

## [0.3.6.1] - 2026-07-14 — Constellation

- Refactored Calendar into a provider-neutral coordinator.
- Added Microsoft Graph support for Outlook and Microsoft 365 calendars.
- Added multi-provider event merging and source labels.
- Added provider-specific connect commands and provider listing.
- Prevented implicit OAuth during startup.
- Added Microsoft token isolation and setup documentation.
- Expanded the regression suite to 98 tests.

## v0.3.6 — Calendar

- Added an optional Google Calendar service using read-only OAuth access.
- Added `calendar`, `calendar today`, `calendar tomorrow`, `calendar next`, and `calendar connect`.
- Routed schedule, agenda, appointment, next-event, and availability questions directly to CalendarService.
- Added natural agenda and focused free/busy responses without relying on the LLM for calendar facts.
- Added a fault-isolated Calendar briefing provider showing today's event count and next event.
- Added Calendar health to `status`, help, and command completion.
- Added secure credential and token paths to `.gitignore`.
- Added eight focused Calendar tests; the complete suite now contains 95 passing tests.

### v0.3.5 Weather conversational refinement

- Preserved greetings when weather questions are routed to the Weather service.
- Added natural summaries for conversational weather requests.
- Added focused answers for rain and temperature questions.
- Kept the detailed structured report for the explicit `weather` command.
- Added regression coverage for conversational and command-style output.

## v0.3.5 — Weather

- Added a common external-service contract with service state, status, and result models.
- Added an Open-Meteo client using only Python's standard library and no API key.
- Added geocoding for profile defaults and explicit locations.
- Added current temperature, apparent temperature, humidity, wind, conditions, highs/lows, and rain chance.
- Added `weather`, `weather tomorrow`, `weather <location>`, and conversational weather routing.
- Routed weather-like `ask` requests to WeatherService instead of the language model.
- Added a fault-isolated Weather briefing provider for Morning Star.
- Added weather service health to `status` and command completion/help updates.
- Added six focused weather tests; the complete suite now contains 82 passing tests.

## v0.3.4 — Morning Star

- Added a first-class, provider-neutral Briefing Service.
- Added validated briefing items with critical, important, and informational priorities.
- Added provider registration, replacement, removal, and deterministic ordering.
- Isolated provider failures so one integration cannot prevent Orion from starting.
- Added a truthful built-in System provider using live workspace, AI, and application state.
- Integrated the briefing into startup without coupling startup code to future services.
- Added the `briefing` command and briefing-provider count to `status`.
- Added Developer Mode diagnostics for failed briefing providers.
- Added five focused tests; the full suite now contains 76 passing tests.


## v0.3.3 — Companion

- Added conversational `Y`, `N`, `A`, and `D` application approvals.
- Hid internal action UUIDs in normal mode while preserving them for audit and Developer Mode.
- Added persistent, workspace-isolated application trust and trust revocation.
- Added a numbered pending-action queue.
- Added Developer Mode and a readable settings summary.
- Added persistent command history with Up/Down navigation.
- Added Tab completion for commands and discovered applications.
- Added cross-platform semantic color output with graceful fallback.
- Reworked startup into a time-aware Companion readiness summary.
- Reorganized help around user abilities rather than internal subsystems.
- Added a compact system status dashboard.
- Improved graceful `Ctrl+C` and `Ctrl+D` shutdown behavior.
- Completed the release with 71 passing automated tests.

## v0.3.1 — Safeguard

- Added the central approval and policy engine.
- Added allow, require-approval, and deny policy decisions.
- Added pending, approve, deny, and protected action CLI flows.
- Prevented approval bypass, denied execution, and action replay.

## v0.3.0 — Ignition

- Added the unified Action Core.
- Added action models, handler registration, execution results, and project-local audit history.
- Added harmless `action echo` and `action history` CLI commands.
- Added action lifecycle and isolation tests.

## v0.2.6 — Atlas

- Added the first-class `knowledge_index` service.
- Added a portable `.orion/knowledge-index.json` workspace map.
- Added Python AST discovery for classes, functions, and imports.
- Added file inventory, test discovery, and TODO/FIXME/HACK scanning.
- Added `index build`, `index status`, `index find`, `index classes`, `index functions`, `index todos`, and `index imports`.
- Added a compact index summary to Orion AI context without injecting the full index.
- Rebound the index automatically when the active workspace changes.
- Added workspace-isolation and context tests; the full suite now contains 48 passing tests.

## v0.2.4 — Continuum

- Added a first-class `conversation` service to the Service Registry.
- Added persistent, workspace-owned conversation files under `.orion/conversations/`.
- Added structured `ConversationMessage`, `ConversationService`, and `ContextBuilder` components.
- Updated the Brain to include relevant recent conversation, session memory, and project context in AI requests.
- Updated the Brain to record successful user/assistant exchanges for every client surface.
- Added `conversation`, `conversation recent [n]`, `conversation search <text>`, and `conversation clear` commands.
- Rebound conversation history automatically when the active workspace changes.
- Added four automated tests; the full suite now contains 41 passing tests.

## v0.2.3 — Pathfinder

- Added the built-in Search Plugin and read-only SearchSkill service.
- Added content, file-name, regex, case-sensitive, path-scoped, and file-type searches.
- Added search safety limits and ignored generated directories.
- Added 11 search-focused tests.
- Removed the accidental nested repository copy from the `orion/` package.
- Updated status, history, about, roadmap, architecture, and plugin documentation.

## v0.2.2 — Open Constellation

- Added the Orion Plugin Manager and plugin contract.
- Added discovery, lifecycle, command routing, help aggregation, and failure isolation.
- Migrated Code Skill into `plugins/code`.
- Added `plugins` and `plugins info <name>` commands.
- Added plugin documentation and five automated tests.

## v0.2.1 — Project Memory

- Added persistent, workspace-local Project Context.
- Added `.orion/` metadata, notes, history, metrics, settings, and task storage foundation.
- Added `project init`, `project status`, `project info`, `project set`, and `project note`.
- Added `history` and `about` commands.
- Added the Orion Constitution, updated architecture, roadmap, and release notes.
- Added atomic JSON writes and explicit corrupt-data reporting.

## v0.2.0 — Intelligence Core

- Added Workspace Manager, Code Skill, Session Memory, and Service Registry.

## v0.1.0 — First Light

- Completed Orion's initial foundation and first successful boot.

## v0.3.2 — Discovery

- Added persistent application catalog generated from Windows Start Menu and desktop shortcuts.
- Added fuzzy application matching and personal aliases.
- Added `apps scan`, `apps list`, `apps find`, `app alias`, and `open` commands.
- Added Windows Search fallback for unknown application names.
- Routed application launches through the Action Service, approval engine, and audit history.
- Added Discovery unit tests.

### v0.3.5 Weather hotfix
- Fixed geocoding for profile locations containing a region, such as `Yuba City, California`.
- Weather now sends only the city name to Open-Meteo and uses state/country qualifiers to select the correct result.
- Added support for common state abbreviations such as `CA`.

## 0.4.0 — Polaris

- Introduced the AI Federation foundation with Ollama, OpenAI, and Gemini providers.
- Added provider-aware configuration and switching through the AI Control Center.
- Added separate local API-key storage; normal configuration files never contain API keys.
- Added 5 federation tests; full suite passes 127 tests.

## 0.4.5 — Horizon

- Expanded Home Center with Tasks, Active Project, Recent Activity, and System Diagnostics cards.
- Reused project context, action history, service registry, plugin manager, and knowledge index as canonical data sources.
- Isolated individual Home card failures so a damaged project file cannot prevent Orion startup.
- Added Home Center coverage; 152 automated tests pass.

## 0.4.4 — Horizon

- Promoted Home into a first-class registered service.
- Added interface-neutral `HomeSnapshot` and `HomeCard` models.
- Decoupled the console Home renderer from Orion's internal services.
- Routed startup and the `home` command through `HomeService`.
- Added Home Center tests; full suite passes 150 tests.

## v0.5.0 — Foundation

- Introduced a true application-data boundary: Git contains code and defaults; `~/.orion` contains personal/runtime state.
- Added migration coverage and update-safety tests.
