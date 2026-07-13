# Orion Architecture

Orion is organized around a small core, a shared Service Registry, explicit services, and capability-focused skills.

## Dependency flow

`Orion Core -> Service Registry -> Services/Skills -> Providers`

The core initializes shared components. Consumers discover them through the registry rather than globals.

## Memory layers

- **Session Memory** is temporary and process-local.
- **Project Context** is persistent and stored inside the active workspace's `.orion/` directory.
- Future **Conversation Context** will manage references and interaction history without mixing those responsibilities.

## Project Context files

- `project.json` — project identity, phase, goal, model, and timestamps
- `history.json` — append-only project event timeline
- `tasks.json` — Task Manager storage foundation
- `notes.md` — human-readable timestamped notes
- `metrics.json` — derived project counts
- `settings.json` — future project-specific preferences

Workspace changes rebind Project Context so each project keeps independent, portable data.


## Workspace Search

`SearchSkill` is registered by the built-in Search Plugin. It depends only on the Workspace Manager, which guarantees that all searched paths remain inside the active workspace. Search remains read-only and applies resource limits before reading files.

## Conversation Context

`ConversationService` is a core registered service shared by CLI, GUI, voice, and future agents. It stores structured messages in workspace-local daily JSON files under `.orion/conversations/`. `ContextBuilder` selects recent conversation, session memory, and active project metadata for the Brain without coupling persistence to any user interface.


## Knowledge Index

`KnowledgeIndex` is a read-only structural workspace service. It inventories files and uses Python's AST to identify classes, functions, and imports. TODO markers and test files are also recorded. The resulting portable JSON index is stored under the active workspace's `.orion/` directory and is rebound whenever the workspace changes.
