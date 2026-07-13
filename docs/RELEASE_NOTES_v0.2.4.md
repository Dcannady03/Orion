# Orion v0.2.4 — Continuum

Continuum gives Orion persistent conversational continuity.

## Highlights

- Workspace-owned daily conversation history
- Context-aware follow-up questions
- Shared conversation service for future CLI, GUI, voice, and agent clients
- Conversation viewing, searching, and clearing commands
- Session-memory and project-context enrichment
- 41 passing automated tests

## Commands

```text
conversation
conversation recent 20
conversation search plugin
conversation clear
```

Conversation files are stored in `.orion/conversations/YYYY-MM-DD.json` and move naturally with each project workspace.
