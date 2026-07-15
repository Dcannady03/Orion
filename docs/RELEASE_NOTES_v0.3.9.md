# Orion v0.3.9 — True North

This reliability release grounds Orion in the active workspace and makes model switching dependable.

## Highlights

- Ollama models are loaded and verified before Orion reports a successful switch.
- AI context automatically refreshes the active workspace index when source files change.
- Copied `.orion/project.json` metadata is excluded when it points at another workspace.
- `project status` now reports live structural metrics and index freshness.
