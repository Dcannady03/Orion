# Orion v0.2.5 — Waypoint

Waypoint gives every workspace a portable handoff memory and a set of mandatory project rules.

## Added

- `.orion/memory.db` SQLite store for project checkpoints and rules.
- `project checkpoint <summary>` to save where work stopped.
- `project resume` to recover the latest checkpoint and current rules.
- `project rules`, `project rule add <rule>`, and `project rule remove <id>`.
- Automatic project recognition when switching workspaces.
- Mandatory rules and the latest checkpoint are supplied to Orion's AI context.
- Isolation tests proving that separate projects never share rules or checkpoints.

## Example rule

```text
project rule add Only create modules; never edit upstream server files because updates overwrite them.
```
