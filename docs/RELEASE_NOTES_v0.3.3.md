# Orion v0.3.3 — Companion

Companion turns Orion's approval system into a natural CLI experience while preserving the secure Action framework underneath.

## Highlights

- Interactive application approval with `Y`, `N`, `A`, and `D` choices
- Internal UUIDs hidden in normal Companion Mode
- Persistent, narrowly scoped “always allow” trust for applications
- Numbered pending-action queue (`action approve 1`)
- Developer Mode for action IDs and match diagnostics
- Workspace-isolated Companion settings and trust decisions
- Commands to inspect and revoke trusted applications

## Commands

- `open <application>`
- `action pending`
- `action approve <number>`
- `action deny <number>`
- `developer on|off`
- `settings`
- `trust list`
- `trust revoke <application>`
