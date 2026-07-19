# Orion v0.5.9 — Canvas

Canvas removes the Git-repository prerequisite from Orion's AI Team implementation
workflow. An ordinary user-approved folder can now move from planning through immutable
approval, one bounded Codex execution, deterministic review, and safe rollback without
creating a repository.

## Workspace capabilities

Orion detects one immutable capability record whenever a workspace is bound or reaches
an approval/execution boundary:

- **Standard:** no Git repository is required; Team execution remains available.
- **Git:** the active folder is inside a repository; Orion records the optional
  repository root, branch, and commit while keeping Codex confined to the active folder.

Git-only status, history, diff, pull, and push commands still require Git Workspace
Mode. Orion never runs `git init`, and it does not stage, commit, reset, checkout, or
push as part of Team execution or rollback.

## Approval and execution context

Plan approvals continue to contain the exact persisted Team plan and SHA-256 hash.
Canvas additionally binds the workspace capability, Codex engine ID, active-workspace
scope, and implementation operation. The router hands its validated engine and current
workspace capability to Codex Bridge through one immutable execution context, preserving
the single-probe executable handoff introduced by Forge.

Standard mode invokes the installed Codex CLI with its narrow
`--skip-git-repo-check` option. The workspace-write sandbox, disabled network and web
search, no extra writable roots, untrusted project configuration, disabled MCP/apps/
hooks/plugins/sub-agents, and no-shell execution remain unchanged.

## Deterministic review and rollback

Before an approval is consumed, Orion captures a bounded external baseline under the
run directory in `~/.orion/codex/`. The baseline excludes Git and Orion metadata,
virtual environments, dependency caches, build outputs, and project ignore patterns.
Per-file, file-count, total-size, and diff-size limits stop unsafe runs before Codex.

After execution Orion independently identifies created, modified, and deleted files and
requires Codex's structured file list to match. UTF-8 text receives a bounded unified
diff with credential-like values redacted. Binary or sensitive files receive metadata
only. Owner-only compressed preimages support `team rollback <run-id>`.

Rollback removes run-created files and restores modified or deleted files without Git.
It first verifies every affected path still matches the completed run and refuses the
entire rollback if restoration would overwrite newer work.

## Workspace creation

`workspace <path>` now offers to create a missing directory after explicit confirmation.
Parent directories may be created when safe; filesystem roots and protected operating-
system locations are rejected. Creation never initializes Git or Orion project memory.

## Verification

The v0.5.9 regression suite contains **292 passing tests** covering Standard and Git
workspaces, repository subdirectories, immutable context handoff, created/modified/
deleted review, unified and binary diffs, secret redaction, ignore rules, snapshot
limits, approval invalidation, rollback conflicts, workspace creation, and Git-only UX.

## Upgrade note and safety boundaries

Approvals created before v0.5.9 do not contain the stronger engine, scope, operation,
and workspace-capability binding. Create a fresh approval before implementing an older
persisted Team plan.

Snapshot limits are intentionally fail-closed. Symbolic links and files or workspaces
above the configured limits require the user to narrow the active workspace or adjust
the explicit limits before execution. Ignored dependency, runtime, build, credential,
and project-ignore paths remain outside the review snapshot and are forbidden by the
Codex execution prompt. Rollback affects only the files recorded for that run and
refuses to proceed if any affected path has changed since execution.
