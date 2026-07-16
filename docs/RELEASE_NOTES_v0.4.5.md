# Orion v0.4.5 — Horizon

This Horizon update expands Orion Home from a live service dashboard into a useful daily command center.

## Added

- **Tasks** shows the number of open project tasks and identifies the next task.
- **Project** shows the active project and its current goal.
- **Activity** summarizes the newest project or approved-action event.
- **System** reports Orion state, registered services, loaded plugins, and knowledge-index readiness.

Each card reads from Orion's existing canonical services. Card failures are isolated and exposed through developer diagnostics rather than interrupting startup.

## Verification

- 152 automated tests pass.
