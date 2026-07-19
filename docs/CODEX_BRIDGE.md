# Codex Bridge Phase 1

Codex Bridge Phase 1 turns an approved, persisted AI Team plan into exactly one
bounded local Codex CLI execution. It may modify files and run tests inside the
active workspace, but it always stops at `Awaiting Review`.

It cannot create or switch branches, modify Git metadata, commit, push, merge, tag,
open a pull request, start a reviewer, or continue automatically.

## Workflow

```text
Persisted AI Team Plan
        |
        v
team approve <team-task-id>
        |
        | immutable plan snapshot + SHA-256 + workspace + approval ID
        v
team implement <team-task-id> <approval-id>
        |
        | one local codex exec process
        v
Structured implementation and test artifacts
        |
        v
Awaiting Review
```

Approval and execution are intentionally separate commands. Typing `team approve`
is the explicit user decision; planning never executes automatically.

## Commands

```text
team approve <team-task-id>
team implement <team-task-id> <approval-id>
team run <run-id>
team rollback <run-id>
execution status
```

Approval prints an ID, plan SHA-256, and absolute workspace. Execution requires the
same AI Team task ID and approval ID. There is no implicit “latest approval.”

Before claiming that approval, Orion requires a runnable Codex CLI through the
Execution Engine service. If no compatible engine is available, Orion displays the
host capability report and leaves the approval unconsumed. See
`EXECUTION_ENGINES.md` for detection rules and status output.

The Execution Engine service returns the exact resolved CLI path. `team implement`
hands that validated engine snapshot to the bridge, which uses the same path as its
first subprocess argument, including `codex.cmd` on Windows. It does not repeat engine
probing or command lookup after announcing that execution is starting. Direct bridge
callers without a supplied snapshot perform one equivalent pre-claim resolution.

## Immutable approval contract

Orion builds a canonical snapshot from the persisted AI Team task's:

- schema version;
- task ID;
- goal;
- ordered final plan; and
- structured Architect and Engineer artifacts.

The canonical JSON is hashed with SHA-256. The approval record stores that snapshot,
hash, approval actor, timestamp, resolved workspace capability, Codex engine ID,
active-workspace scope, and implementation operation. Before execution Orion
reloads the AI Team task and recomputes the hash. Any changed goal, step, or role
artifact invalidates the approval and requires a new `team approve` command.

Approval files are immutable and single-use. A failed or interrupted run consumes its
approval because the run record is written before Codex starts. Retrying therefore
requires a new explicit approval.

## Workspace modes and boundary

Git is optional. `WorkspaceManager` detects one capability record and passes it through
the approval and immutable execution context:

- **Standard Workspace Mode:** any valid user-approved directory. Team planning,
  approval, implementation, review, and rollback are available without Git.
- **Git Workspace Mode:** the active directory is inside a repository. Orion records
  its repository root, branch, and commit when available, but execution remains bounded
  to the selected directory even when it is a repository subdirectory.

Standard mode adds Codex's narrow `--skip-git-repo-check` CLI option. Git mode does not.
Orion never creates a hidden repository, runs `git init`, grants additional writable
directories, or allows the artifact store to sit inside the workspace.

The local process uses these boundaries:

- `codex exec --sandbox workspace-write`;
- non-interactive approval policy `never`, so requests outside the sandbox are denied;
- `.git`, `.codex`, and `.agents` remain protected by Codex's workspace sandbox;
- command network access and web search are disabled;
- temporary writable roots and extra writable roots are disabled;
- project Codex configuration is treated as untrusted;
- MCP servers, apps, hooks, remote plugins, and sub-agents are disabled;
- user Codex configuration is ignored for the run; and
- the plan prompt is sent over standard input without a shell.

Git-only commands such as status, history, Git diff, pull, and push reject Standard
mode with a capability-specific message. Their unavailability does not block Team
execution.

## Bounded change tracking

Before the exclusive approval claim or Codex process, Orion captures a deterministic
baseline under the external run directory. It excludes `.git`, Orion runtime metadata,
virtual environments, dependency caches, build outputs, and root project ignore rules.
The baseline enforces configured file-count, per-file, total-size, and diff-size limits.
Unreadable files, symbolic links, and exceeded limits fail before Codex starts and leave
the approval unconsumed.

After execution Orion hashes the workspace again and independently classifies files as
created, modified, or deleted. Codex's structured `files_changed` list must exactly
match those observed paths. A deletion is accepted only when the approved plan names
the deleted path and deletion operation. UTF-8 text receives a bounded unified diff
with credential-like values redacted. Binary and sensitive files receive size/hash
metadata only rather than printable content.

`team rollback <run-id>` requires explicit confirmation. It removes created files and
restores owner-only compressed preimages for modified and deleted files. A complete
preflight verifies every affected path still matches the run result; any newer change
refuses the entire rollback. Rollback never uses Git reset, checkout, staging, or commits.

The child environment uses an allowlist of ordinary operating-system paths and locale
settings. API keys, access tokens, GitHub tokens, and other arbitrary parent variables
are not forwarded. Phase 1 therefore expects the local Codex CLI to have a persisted
login rather than environment-only authentication.

## Structured result

Codex receives a strict JSON Schema and must report:

```json
{
  "summary": "Implemented the approved plan.",
  "files_changed": [
    {
      "path": "orion/services/example.py",
      "summary": "Added the bounded service."
    }
  ],
  "tests": [
    {
      "command": "python -m unittest tests.test_example",
      "status": "passed",
      "summary": "3 tests passed."
    }
  ],
  "risks": [],
  "remaining_work": [],
  "review_notes": []
}
```

All fields are required and unknown fields are rejected. Test status is limited to
`passed`, `failed`, or `not_run`. At least one test record is required, even when the
record explains why testing could not run. Changed paths must be unique, relative to
the approved workspace, cannot escape it or target `.git`, `.codex`, or `.agents`, and
must match Orion's independent workspace change record.

Valid structured output reaches `Awaiting Review` even when a reported test failed or
remaining work exists. That information belongs to the reviewer; it does not trigger
an automatic repair loop.

## External persistence

All bridge state lives outside application and workspace files:

```text
~/.orion/codex/
  approvals/
    <team-task-id>/
      <approval-id>.json
  claims/
    <team-task-id>/
      <approval-id>.json
  runs/
    <run-id>/
      run.json
      approved-plan.json
      result-schema.json
      events.jsonl
      implementation-result.json
      workspace-baseline.json
      workspace-changes.json
      workspace.diff
      snapshot/
        blobs/
      rollback.json  # present only after an approved rollback
```

Approval, claim, and artifact files are created once and never overwritten. The claim
uses exclusive file creation before Codex starts, so two Orion processes cannot consume
the same approval concurrently. `run.json` uses atomic replacement while moving from
`Executing` to `Awaiting Review` or `Failed`. Owner-only file permissions are requested
where the platform supports them.

The JSONL stream records Codex progress events. The run document and final result use
strict schemas and reject missing, malformed, non-finite, or unknown fields. Raw
stderr is not persisted. Failures retain only one sanitized category:

- `codex_cli_unavailable`;
- `codex_timeout`;
- `codex_process_failed`;
- `codex_output_too_large`;
- `invalid_codex_output`;
- `workspace_snapshot_failed`; or
- `workspace_change_mismatch`.

## Configuration

```yaml
codex_bridge:
  enabled: true
  timeout_seconds: 1800
  max_output_bytes: 5000000
  snapshot_max_files: 10000
  snapshot_max_file_bytes: 25000000
  snapshot_max_total_bytes: 250000000
  diff_max_bytes: 2000000
```

Timeout is bounded to 1–7,200 seconds. Captured process output is bounded to
1–100,000,000 bytes. Snapshot limits are validated before the approval is claimed or
the local process starts. Ignored paths are outside snapshot review and are explicitly
prohibited in the implementation prompt.

## Phase boundary

Codex Bridge still has no streaming UI, Workflow Engine transition, autonomous
reviewer, repair loop, branch creation, commit, push, merge, tag, or pull-request
integration. Deterministic review and rollback stop at the human approval boundary.
