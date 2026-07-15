
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
