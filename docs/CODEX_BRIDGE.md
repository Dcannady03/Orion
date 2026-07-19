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
hash, approval actor, timestamp, and resolved workspace root. Before execution Orion
reloads the AI Team task and recomputes the hash. Any changed goal, step, or role
artifact invalidates the approval and requires a new `team approve` command.

Approval files are immutable and single-use. A failed or interrupted run consumes its
approval because the run record is written before Codex starts. Retrying therefore
requires a new explicit approval.

## Workspace boundary

The active workspace must be a Git repository root with its own `.git` marker. Orion
does not use `--skip-git-repo-check`, grant additional writable directories, or allow
the artifact store to sit inside the workspace.

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
the approved workspace, and cannot escape it or target `.git`, `.codex`, or `.agents`.

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
- `codex_output_too_large`; or
- `invalid_codex_output`.

## Configuration

```yaml
codex_bridge:
  enabled: true
  timeout_seconds: 1800
  max_output_bytes: 5000000
```

Timeout is bounded to 1–7,200 seconds. Captured process output is bounded to
1–100,000,000 bytes. Configuration is validated before the local process starts.

## Phase boundary

Phase 1 deliberately has no streaming UI, Workflow Engine transition, autonomous
reviewer, repair loop, branch creation, commit, push, merge, tag, or pull-request
integration. A later review phase may inspect the persisted run and workspace diff,
but only after a separate design and approval boundary is defined.
