# Runtime Data Boundaries

Orion separates distributable source from mutable or private state.

## Repository source

The repository root contains Python source, the non-secret
`config/default.yaml`, documentation, scripts, and deterministic tests. Source files
must not require a developer's local Orion state.

## User runtime data

User-owned application state belongs under:

```text
~/.orion/
```

This includes configuration overrides, profiles, Vault data, OAuth caches, permanent
agents, Command Center records, Team tasks, Codex artifacts, image artifacts, caches,
logs, Goal Proposals, Event Bus history, and approval artifacts.
`ORION_USER_DATA` may explicitly override this root. A
repository-local `.orion/` directory is never used as the global user-data root.

Goal Proposal records use:

```text
~/.orion/goals/proposals/<proposal-id>.json
```

They contain reviewed goal text, workspace and department identity, ordered capability
metadata, fixed expiry, integrity hashes, lifecycle actors/timestamps, and bounded
safe dispatch summaries. They contain no Vault values, provider credentials, raw
Python objects, stack traces, or workspace file content. Proposal records use atomic
replacement and owner-only permissions where supported. Rejected, expired, invalid,
superseded, consumed, and failed records remain as audit history.

Event Bus records use:

```text
~/.orion/events/YYYY-MM-DD.jsonl
```

Each valid line is one canonical, bounded, schema-versioned event. Appends use a
per-day cross-process lock, a single append, flush, `fsync`, owner-only permissions
where supported, and symlink guards. A malformed crash-truncated final line is
reported and ignored; the next append separates it before writing another valid
event. Event data contains selected IDs and lifecycle summaries, never Vault values,
raw provider prompts, environment dumps, stack traces, or arbitrary file content.

## Workspace-generated state

Some project-scoped features intentionally create `<workspace>/.orion/` metadata,
such as project context, Task Manager records, workspace agents, conversations, and
knowledge indexes. Action history, trusted actions, application catalogs, aliases, and
project settings are also scoped to the active workspace. This is generated runtime
state, not application source. Orion's own repository ignores it. Another project may
choose its own versioning policy, but secrets, tokens, approvals, trusted actions, and
private conversation data must never be committed.

## Development fixtures

Sanitized deterministic samples belong under:

```text
<repository-root>/tests/fixtures/
```

Tests should prefer temporary directories for mutable records. Fixtures must contain
no real identity, workspace history, credentials, tokens, approvals, or service
responses.

## Repository cleanup

The stabilization milestone removed tracked repository copies of `.orion/`, `.vs/`,
and `config/profile.yaml` from Git's index. The local files were deliberately left in
place and are now ignored. `.gitignore` also covers Python caches and environments,
IDE state, local environment files, common credential/token names, Vault files, and
private key formats.
