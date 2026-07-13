# Changelog

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
