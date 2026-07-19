# Orion AI Team

Orion coordinates specialized planning, implementation, validation, and documentation
roles while remaining the only user-facing orchestrator. Planning remains tool-free;
execution, automatic validation, and Documentation Review use separate bounded stages.

## Workflow

```text
Goal -> Architect JSON -> Engineering Reviewer JSON -> Final Plan -> Y/N/D Approval
     -> Implementation Engine -> Automatic Tester -> Documentation Reviewer
     -> Awaiting Review -> Human decision
```

The planning orchestrator makes exactly two provider calls. It does not expose tools
to either planning role, retry indefinitely, modify source files, run tests, create
commits, or open pull requests. A required post-implementation Documentation Review
may make one separate bounded provider call after the Tester.

After a plan reaches `Awaiting Approval`, `task link-plan` can attach it to a
first-class project task as an artifact. Linking does not approve the project task or
start implementation.

In the interactive console, Orion asks whether to approve the exact displayed plan.
Only `Y` or `Yes` creates the approval and immediately invokes the bounded Codex Bridge
path. `N`, empty input, or Ctrl+C records no approval and performs no implementation.
`D` displays the complete final plan, risks, workspace binding, execution engine,
sandbox, and expected permissions before returning to the prompt. No natural-language
response elsewhere in Orion is treated as approval.

The manual Codex Bridge path remains available. `team approve` binds the persisted
plan's SHA-256 and current workspace to an immutable, single-use approval. The same
approval service is used by interactive `Y`. The same
`team implement <team-task-id> <approval-id>` execution service consumes it and stops
at `Awaiting Review`; approval replay is rejected in either workflow. After successful
implementation, the configured Tester runs deterministic validation. The configured
Documentation Reviewer then assesses applicable documentation before Orion renders the
final human-review state, regardless of whether validation passed, warned, or failed.

## Commands

```text
team
team roles
team role show <role>
team role set <role> <provider:model|engine>
team role reset <role>
team plan "Add OpenAI image generation"
team plan --manual "Add OpenAI image generation"
team status <task-id>
team approve <task-id>
team implement <task-id> <approval-id>
team run <run-id>
team test <run-id>
team test last
team docs <run-id>
team docs last
team docs show <run-id>
execution status
task link-plan <project-task-id> <team-task-id>
```

`team plan --manual` disables the Y/N/D prompt for scripting, recovery, and automated
callers. Command routers are noninteractive unless the Orion console explicitly enables
interactive Team approval, so tests and embedded callers cannot accidentally block on
standard input.

## Role routing

Orion separates a workflow role from the model or engine assigned to perform it.
Assignments are persisted in external user configuration, normally
`~/.orion/config.yaml`, never in a project or Vault file. `team roles` reports
the requested and actual assignment, availability, capability, fallback policy, and
whether the value is a default or a user override.

```yaml
team:
  assignments:
    architect: active-planning-model
    engineer_reviewer: active-planning-model
    implementation: codex
    tester: codex
    documentation: active-planning-model
```

The five roles are:

- **Architect** — planning model; creates the first structured plan.
- **Engineering Reviewer** — validation role using a planning model; critiques and
  consolidates the plan.
- **Implementation Engine** — execution engine; Codex is the default and currently
  supported implementation adapter.
- **Tester** — validation role using an execution engine; Codex is the default.
- **Documentation Reviewer** — validation role using a planning model; performs one
  bounded, read-only documentation assessment after the Tester.

Use a provider/model pair for model-backed roles and an engine ID for execution roles:

```text
team role set architect openai:gpt-5
team role set engineer_reviewer gemini:gemini-2.5-pro
team role set implementation codex
team role show architect
team role reset architect
```

The exact model must exist and be available through the configured provider. Disabled
or unconfigured providers and disabled assigned agents are rejected. Execution roles
require an installed CLI with an Orion implementation adapter; implementation fails
closed when that engine is unavailable.

`active-planning-model` follows the active provider and existing Fast, Balanced,
Coding, or Research routing policy. If that dynamic assignment cannot be used, Orion
may choose an available planning fallback from the same routing policy and reports the
reason. Explicit `provider:model` assignments do not silently change to another model.
Execution roles never use planning fallbacks. An unavailable Tester records
`Validation Unavailable` and launches no check; Orion never silently substitutes a
different execution engine.

The Documentation Reviewer uses the same planning-model routing contract as Architect.
An explicit `provider:model` is honored or fails closed. A dynamic
`active-planning-model` assignment may follow the existing routing fallback order, but
the requested and actual model plus the reason are persisted. An unavailable reviewer
records `Documentation Unavailable`; it does not prevent the human-review gate.

Agent definitions under `~/.orion/agents/` remain the configurable workers behind
planning roles. Their instructions specialize the role, while Orion continues to own
every prompt, provider call, handoff, artifact, approval, and user-facing result.
Providers never communicate directly with the user.

## Automatic Tester

The Tester is a separate validation role, not another implementation turn. After a
successful implementation Orion inspects the recorded created, modified, and deleted
files and selects only relevant deterministic checks:

- changed Python files receive compile validation and matching targeted tests;
- broad shared Python changes, or changes with no target match, receive full test
  discovery;
- changed JSON, YAML, and TOML files are parsed locally;
- changed Markdown receives heading, fence, and practical local-link checks;
- expected created/deleted files, the implementation result, workspace snapshot, and
  protected `.git`, `.codex`, and `.agents` metadata are verified.

Tester commands are allowlisted, time-bounded, and run with an isolated temporary home,
disabled network access, no inherited credential variables, no nested processes, and a
Python-level filesystem guard. Temporary caches stay outside the workspace and are
removed after the attempt. Orion compares the workspace again after testing; any
mutation becomes a validation failure. The Tester cannot fix failures, update plans or
roles, access Vault/OAuth data, consume an approval, or run Git operations.

Possible review results are:

- `Awaiting Review — Validation Passed`;
- `Awaiting Review — Validation Warnings`;
- `Awaiting Review — Validation Failed`;
- `Validation Unavailable`;
- `Validation Error`; or
- `Awaiting Review — Validation Not Run` for compatible older artifacts.

Validation never accepts or rolls back changes. Use `team test <run-id>` to create a
new immutable validation attempt, or `team test last` for the newest eligible run in
the active workspace. Each validation attempt is followed by Documentation Review.
Neither command reruns implementation or consumes an approval.

## Documentation Reviewer

Documentation Review begins with a deterministic requirement classifier. Commands,
configuration, services/providers/plugins, setup, safety/approval/credential behavior,
public contracts, artifact formats, troubleshooting, release behavior, architecture,
visible output, platforms, and features normally require documentation. Test-only work
and explicit internal refactors with no observable contract impact may be
`Documentation Not Required`. Orion records its reasons and evidence.

For required changes, Orion builds an applicable inventory rather than demanding every
document. New commands normally require interactive help, the User Guide command
reference, a feature guide, and changelog. Configuration changes normally require
defaults, Configuration reference, setup/migration guidance when applicable, and
changelog. Architecture and safety changes select their corresponding subsystem and
security documentation.

Deterministic checks reuse Orion's Markdown structure/local-link validation and audit
new command completion, interactive help, new/changed configuration keys, changelog,
and applicable architecture/safety coverage. One routed planning-model call may then
report bounded structured findings with severity, category, document, section,
implementation evidence, recommended correction, confidence, and whether the finding
blocks Documentation Passed.

Possible results are:

- `Documentation Passed` — required coverage is complete and accurate;
- `Documentation Warnings` — non-blocking or review-worthy gaps remain;
- `Documentation Failed` — material user/developer contract documentation is missing
  or inaccurate;
- `Documentation Not Required` — no meaningful documentation contract changed;
- `Documentation Unavailable` — the configured model cannot run;
- `Documentation Error` — the bounded review stopped safely; or
- `Documentation Not Run` — a compatible older run has no attempt.

Use `team docs <run-id>` to add one immutable attempt, `team docs last` to select the
newest eligible run in the active workspace, and `team docs show <run-id>` to display
the latest concise findings. These commands never rerun implementation or validation,
consume approval, or modify repository files. Findings are recommendations for human
review; this milestone has no Documentation Writer or automatic repair behavior.

Provider context is limited to sanitized plan/implementation summaries, changed-file
metadata, validation summaries, known command/configuration changes, applicable
documentation excerpts, and project rules. Raw diffs, source bodies, credentials,
environment variables, Vault/OAuth data, mailbox content, and unrelated workspaces are
not provided. The reviewer receives no tools and cannot run shell/Git/Codex/Tester
commands, edit files, alter roles or approvals, accept work, or roll back changes.

When no supported CLI is runnable, `team implement` shows the Execution Engine report
instead of a low-level “Codex not found” error. That preflight occurs before the
single-use approval is claimed, so the same approval remains valid after installation.

## Structured role output

Every active role must return one JSON object with this schema:

```json
{
  "summary": "Short role result",
  "recommendations": ["Ordered step"],
  "risks": ["Concise risk"],
  "next_action": "What should happen next"
}
```

All four fields are required, and unknown fields are rejected. Invalid JSON or any
schema violation stops the run, records a sanitized failure state, and does not call
the next role.

## Persistence

Each task is stored as an individual JSON file under:

```text
~/.orion/team/tasks/<task-id>.json
```

The file contains the goal, status, artifacts, messages, final plan, timestamps, usage
estimates, and an immutable snapshot of all five requested/resolved role assignments.
Each produced role artifact records the requested and actual provider/model, fallback
reason, token usage, estimated cost, and execution duration. Files are written
atomically with owner-only permissions where the platform supports them. They are
outside the application directory and survive Orion updates.

Persisted tasks are validated before saving and after loading. Orion rejects missing
or mismatched task identity, unknown status values, malformed or timezone-free
timestamps, invalid message and usage records, and unexpected fields rather than
loading a partially valid task.

Implementation, validation, and documentation artifacts are separate from planning
tasks under `~/.orion/codex/runs/<run-id>/`. `run.json` retains the latest bounded
validation and documentation summaries plus immutable history paths. Documentation
attempts use `documentation/documentation-NNNN.json` and `.log`, alongside the existing
`validation/validation-NNNN.*` history. Older run records without either field remain
readable as `Validation Not Run` or `Documentation Not Run`.

## Token and cost reporting

The existing provider contract returns text, so Phase 1 estimates tokens from request
and response length and labels them accordingly. Ollama's configured rate is zero.
Cloud cost remains `not configured` unless rates are supplied locally:

```yaml
team:
  pricing:
    openai:
      input_per_million: null
      output_per_million: null
```

This avoids embedding prices that may become outdated.
