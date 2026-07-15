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
