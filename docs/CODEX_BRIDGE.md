# Codex Bridge Phase 1

Codex Bridge Phase 1 turns an approved, persisted AI Team plan into exactly one
bounded local Codex CLI execution. It may modify files and run tests inside the
active workspace, but it always stops at `Awaiting Review`.

It cannot create or switch branches, modify Git metadata, commit, push, merge, tag,
open a pull request, or continue beyond the bounded Tester and Documentation Reviewer
stages into automatic acceptance or repair.

## Workflow

```text
Persisted AI Team Plan
        |
        v
explicit interactive Y or team approve <team-task-id>
        |
        | immutable plan snapshot + SHA-256 + workspace + approval ID
        v
automatic bounded handoff or team implement <team-task-id> <approval-id>
        |
        | one local codex exec process
        v
Structured implementation artifacts
        |
        v
Automatic Tester (bounded, read-only)
        |
        v
Documentation Reviewer (bounded, read-only)
        |
        v
Awaiting Review + independent validation/documentation results
```

The interactive console offers approval immediately after rendering the final plan.
Only explicit `Y` or `Yes` creates the same immutable approval as `team approve` and
hands it to the same bounded implementation path as `team implement`. `N`, empty input,
invalid input, and Ctrl+C never imply approval. `D` is read-only and returns to the
prompt. Planning never executes based on natural-language replies elsewhere in Orion.

Separate approval and execution commands remain available for scripting and recovery.
`team plan --manual "<goal>"` explicitly suppresses the interactive prompt.

## Commands

```text
team approve <team-task-id>
team implement <team-task-id> <approval-id>
team plan --manual "<goal>"
team run <run-id>
team test <run-id>
team test last
team docs <run-id>
team docs last
team docs show <run-id>
team rollback <run-id>
execution status
```

Approval prints an ID, plan SHA-256, and absolute workspace. Execution requires the
same AI Team task ID and approval ID. There is no implicit “latest approval.”

Before claiming that approval, Orion requires a runnable Codex CLI through the
Execution Engine service. If no compatible engine is available, Orion displays the
host capability report and leaves the approval unconsumed. See
`EXECUTION_ENGINES.md` for detection rules and status output.

The Execution Engine service returns the exact resolved and version-probed CLI path.
`team implement` hands that immutable engine snapshot to the bridge, which uses the
same path as its logical first subprocess argument, including npm-installed
`codex.cmd` wrappers on Windows. The runner selects the fixed-argument Windows wrapper
invocation without `shell=True`. The bridge never repeats engine probing or command
lookup after the router announces that execution is starting; direct service callers
must also supply a validated engine snapshot.

After validating the immutable plan, approval, engine, and workspace bindings—but
before capturing a baseline or claiming the one-use approval—the bridge runs a bounded
`codex exec --help` probe. Supported long-option names are cached for that executable
for the life of the Orion process. Command construction is capability-based rather
than version-based:

- required security/protocol options (`--sandbox`, `--config`, `--cd`,
  `--ignore-user-config`, `--strict-config`, `--json`, and `--output-schema`) must be
  advertised or execution aborts with the approval unconsumed;
- optional compatibility options are included only when advertised; and
- unsupported options are omitted and tracked by the command plan.

Orion's external immutable approval is the approval authority. After that approval,
plan hash, engine, and workspace are validated, the bridge passes the supported strict
configuration override `approval_policy="never"` to the noninteractive child process.
This prevents Codex from trying to open a second approval prompt, but it does not remove
or broaden `workspace-write`. Orion supplies both `--sandbox workspace-write` and the
equivalent strict `sandbox_mode="workspace-write"` config so compatible CLI releases
cannot fall back to their read-only default while resolving noninteractive execution.
On native Windows the isolated strict configuration also selects
`windows.sandbox="elevated"`, Codex's preferred native sandbox. This is required because
`--ignore-user-config` intentionally prevents a user's otherwise-correct Windows sandbox
selection from reaching the child process. Orion validates the same static strict config
during its bounded help probe before consuming the approval.
On Codex CLI 0.144.5, `codex exec` does not advertise `--ask-for-approval`, so the
bridge omits that duplicate CLI option. If a future compatible CLI advertises it,
Orion may include `--ask-for-approval never` as an optional compatibility argument in
addition to the strict config.

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
- Orion's immutable one-use approval is validated before the non-interactive
  `codex exec` launch;
- `approval_policy="never"` prevents a duplicate child-process approval prompt only
  after Orion approval validation and leaves `workspace-write` enforcement intact;
- `sandbox_mode="workspace-write"` duplicates the required CLI sandbox selection as a
  strict compatibility config without adding any writable root;
- native Windows runs select the preferred `windows.sandbox="elevated"` implementation
  inside Orion's isolated config instead of silently falling back to the degraded
  unelevated sandbox;
- an optional Codex `--ask-for-approval never` duplicate is used only when the detected
  CLI supports it; its absence never weakens Orion's approval gate;
- `.git`, `.codex`, and `.agents` remain protected by Codex's workspace sandbox;
- command network access and web search are disabled;
- the immutable approved workspace is passed unchanged through `--cd` and is the
  primary writable directory for `workspace-write`;
- Orion does not pass `sandbox_workspace_write.writable_roots=[]`, because an empty
  override removes Codex's normal writable access to that primary workspace;
- no parent directory, user-profile directory, `--add-dir`, or other additional
  writable root is granted;
- the approved workspace, execution-context workspace, active binding, subprocess
  working directory, and `--cd` path must match or execution fails before launch;
- temporary-directory writable-root discovery remains disabled;
- project Codex configuration is treated as untrusted;
- MCP servers, apps, hooks, remote plugins, and sub-agents are disabled;
- user Codex configuration is ignored for the run; and
- the plan prompt is sent over standard input without a shell.

The implementation prompt directs Codex to use its file-edit/patch tool for mutations.
A declined compound shell preflight is a command-policy result, not evidence that the
workspace is read-only; Codex must retry diagnostics with simple fixed-argument read or
test commands and remain inside the same sandbox.

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

## Automatic validation boundary

After implementation artifacts and actual workspace changes agree, Codex Bridge asks
the existing role registry for the configured Tester. The requested and resolved
assignment, engine, fallback reason, timestamps, duration, exit codes, checks, files,
and safe diagnostics are stored with the attempt. Execution roles do not silently
fall back; an unavailable Tester records `Validation Unavailable` without launching a
command.

Orion chooses checks deterministically from the actual change set. It compiles changed
Python, prefers matching targeted tests, and uses full discovery only for broad shared
infrastructure or when no target can be identified. JSON, YAML, TOML, and Markdown
receive relevant local validation. Expected file state, snapshot integrity, and
protected `.git`, `.codex`, and `.agents` metadata are checked before and after Tester
commands.

Tester commands use an isolated temporary home and cache, a fixed Python-module
allowlist, time and output limits, no inherited credential variables, blocked network
access, blocked nested commands, and a write guard outside the temporary validation
directory. Raw stdout, stderr, environments, prompts, and file contents are never
persisted. Validation may report failure, but it never repairs, accepts, commits, or
rolls back implementation changes.

`team test <run-id>` and `team test last` rerun this validation stage and then the
automatic Documentation Reviewer. A rerun does not consume another approval or start
Codex implementation. Each validation attempt receives a new immutable artifact pair,
so older results remain available for audit history.

## Documentation Review boundary

After every validation outcome, Orion deterministically classifies whether the actual
change requires documentation and selects an applicable inventory. New commands,
configuration, providers/services/plugins, setup, safety, public contracts, artifact
formats, troubleshooting, releases, architecture, visible output, platforms, and
features normally require coverage. Test-only and explicit internal/no-observable-
behavior work may be `Documentation Not Required`.

The deterministic pass reuses Markdown structure/local-link checks and compares added
commands with completion, interactive help, and the User Guide. It also audits added or
changed default configuration keys, changelog changes, and applicable architecture and
safety documents. A configured planning model then receives only bounded sanitized
plan/implementation summaries, file metadata and safe summaries, validation counts,
known command/configuration changes, project rules, headings, and bounded excerpts from
applicable documentation. It never receives raw diffs, source bodies, credentials,
environment variables, Vault/OAuth/mail data, or unrelated workspace content.

The Documentation Reviewer returns strict findings with severity, category, affected
document/section, implementation evidence, correction recommendation, confidence, and
whether the finding blocks Documentation Passed. Orion derives Passed, Warnings,
Failed, Not Required, Unavailable, or Error independently from validation. A
validation failure does not become a documentation failure unless documentation is
itself inaccurate.

`team docs <run-id>` creates another immutable documentation attempt;
`team docs last` selects the newest complete run bound to the active workspace; and
`team docs show <run-id>` displays the latest bounded findings. These commands never
rerun implementation or validation and never consume approval. The reviewer has no
file, shell, Codex, Tester, Git, approval, role, repair, acceptance, or rollback tools.

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
      validation-protected-baseline.json
      validation/
        validation-0001.json
        validation-0001.log
        validation-0002.json  # present only after a rerun
        validation-0002.log
      documentation/
        documentation-0001.json
        documentation-0001.log
        documentation-0002.json  # present only after a rerun
        documentation-0002.log
      snapshot/
        blobs/
      rollback.json  # present only after an approved rollback
```

Approval, claim, and artifact files are created once and never overwritten. The claim
uses exclusive file creation before Codex starts, so two Orion processes cannot consume
the same approval concurrently. `run.json` uses atomic replacement while moving from
`Executing` to `Awaiting Review` or `Failed`. Owner-only file permissions are requested
where the platform supports them.

`run.json` retains the newest strict validation and documentation envelopes plus
bounded immutable history paths. Existing schema-v2 runs without either field remain
readable and display `Validation Not Run` or `Documentation Not Run`. Validation and
documentation logs contain only bounded, redacted check/finding summaries; they are
not raw provider or process logs.

The JSONL artifact contains parsed, validated events reserialized by Orion; raw process
stdout and stderr are never written. The run document and final result use strict
schemas and reject missing, malformed, non-finite, or unknown fields. `run.json`
contains a sanitized diagnostics envelope with the exit code, timeout state, resolved
executable, a fixed safe stderr summary, and a validated unsupported option name when
the CLI reports one. Failures retain only one sanitized category:

- `codex_cli_unavailable`;
- `codex_cli_argument_error`;
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
team:
  validation:
    command_timeout_seconds: 120
    max_output_bytes: 250000
  documentation_review:
    enabled: true
    max_documents: 24
    max_findings: 30
    max_diff_summary_chars: 24000
```

Timeout is bounded to 1–7,200 seconds. Captured process output is bounded to
1–100,000,000 bytes. Snapshot limits are validated before the approval is claimed or
the local process starts. Ignored paths are outside snapshot review and are explicitly
prohibited in the implementation prompt.

Each automatic validation command is separately bounded to 1–900 seconds. Validation
output capture is bounded to 1,000–5,000,000 bytes and discarded rather than persisted.
Documentation Review inspects 5–100 documents, retains 1–100 findings, and bounds its
sanitized documentation/diff-summary context to 4,000–200,000 characters.

## Phase boundary

Codex Bridge still has no streaming UI, repair loop, Documentation Writer, automatic
acceptance, branch creation, commit, push, merge, tag, or pull-request integration.
Tester and Documentation Reviewer evidence plus rollback stop at the human boundary.
