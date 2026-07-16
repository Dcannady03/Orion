# Changelog

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
