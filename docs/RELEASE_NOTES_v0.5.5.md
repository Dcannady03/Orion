# Orion v0.5.5 — Council

Council gives Orion a bounded AI planning team and configurable specialist agents
without granting autonomous implementation access.

## AI Team Phase 1

`team plan "<goal>"` coordinates one Architect response and one Engineer Review,
validates their strict JSON outputs, persists the task under `~/.orion/team/tasks/`,
and stops at `Awaiting Approval`. Orion reports estimated token use and configured
provider costs but does not modify code, run tools, create commits, or open pull
requests.

## Agent Registry Phase 1

Agents are strict YAML definitions stored under `~/.orion/agents/`. Each agent has a
name, enabled state, provider, model, instructions, declared tools, turn limit, and
explicit filesystem, shell, and Git permissions. Built-in Architect, Engineer, and
Reviewer definitions are created only when missing, so user edits survive updates.

New commands:

```text
agent list
agent show <name>
agent create
agent enable <name>
agent disable <name>
agent test <name>
```

AI Team roles now resolve to assigned agents, keeping the workflow job separate from
the configured worker.

## Safety and persistence

- Phase 1 grants no tools during agent tests or team plans.
- Unknown fields, capabilities, statuses, and malformed persisted records are rejected.
- Agent provider failures are reduced to safe error categories.
- Agent definitions and team tasks remain outside the replaceable application folder.
- Existing agent files are never overwritten during startup or updates.

## Verification

The v0.5.5 regression suite contains **219 passing tests**, including agent schema,
permissions, external persistence, role assignment, disabled-agent enforcement,
bounded provider calls, AI Team persistence, and update-safe Discord configuration.
