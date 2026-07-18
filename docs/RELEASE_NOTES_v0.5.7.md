# Orion v0.5.7 — Forge

Forge turns an approved, persisted AI Team plan into exactly one bounded local Codex
CLI implementation run and stops at `Awaiting Review`.

## Immutable approval gate

`team approve` binds the exact AI Team task, canonical plan SHA-256, approval actor,
timestamp, and active workspace. `team implement` reloads and re-hashes that plan,
rejects changed plans or workspaces, and consumes each approval at most once.

## Bounded local implementation

Codex receives workspace-write access only to the active Git repository. Orion blocks
network access, web search, extra writable roots, project Codex configuration, MCP,
apps, hooks, plugins, sub-agents, and every branch, commit, push, merge, tag, or pull
request action. A strict schema captures changed files, test results, risks, remaining
work, and review notes.

New commands:

```text
team approve <team-task-id>
team implement <team-task-id> <approval-id>
team run <run-id>
execution status
```

## Execution Engine discovery

Orion distinguishes runnable CLI engines from installed desktop applications and its
Python runtime. When no implementation engine is available, `team implement` explains
the detected capabilities before claiming the approval.

Windows Codex discovery checks `codex.cmd`, `codex.exe`, and `codex` in that order.
The bridge launches the exact resolved path that passed discovery, eliminating the
status-versus-launch mismatch that previously consumed an approval on failed startup.

## External artifacts

Approvals, claims, run state, approved plans, result schemas, JSONL events, and final
implementation results are stored under `~/.orion/codex/`. Application updates and
workspace cleanup do not remove active bridge state.

## Verification

The v0.5.7 regression suite contains **255 passing tests**, including immutable-plan,
workspace-binding, replay, persistence, corruption, structured-output, subprocess,
execution discovery, Windows resolver, and approval-preservation coverage.
