# Changelog

## v0.2.3 — Pathfinder

- Added the built-in Search Plugin and read-only SearchSkill service.
- Added content, file-name, regex, case-sensitive, path-scoped, and file-type searches.
- Added search safety limits and ignored generated directories.
- Added 11 search-focused tests.
- Removed the accidental nested repository copy from the `orion/` package.
- Updated status, history, about, roadmap, architecture, and plugin documentation.

## 0.2.1 — Project Memory
- Added persistent, workspace-local Project Context.
- Added `.orion/` metadata, notes, history, metrics, settings, and task storage foundation.
- Added `project init`, `project status`, `project info`, `project set`, and `project note`.
- Added `history` and `about` commands.
- Added the Orion Constitution, updated architecture, roadmap, and release notes.
- Added atomic JSON writes and explicit corrupt-data reporting.

## 0.2.0 — Intelligence Core
- Added Workspace Manager, Code Skill, Session Memory, and Service Registry.

## 0.1.0 — First Light
- Completed Orion's initial foundation and first successful boot.

## v0.2.2 — Open Constellation

- Added the Orion Plugin Manager and plugin contract.
- Added discovery, lifecycle, command routing, help aggregation, and failure isolation.
- Migrated Code Skill into `plugins/code`.
- Added `plugins` and `plugins info <name>` commands.
- Added plugin documentation and five automated tests.
