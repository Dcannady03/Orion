# Orion v0.7.0 — Conductor

Conductor turns Orion's fixed AI Team roles into persistent, provider-neutral routing
assignments while preserving Orion as the authoritative orchestrator.

## Five workflow roles

Orion now manages five explicit assignments:

- Architect — planning model;
- Engineering Reviewer — planning validation model;
- Implementation Engine — execution engine, Codex by default;
- Tester — execution validation engine, Codex by default;
- Documentation Reviewer — planning validation model.

`team roles` reports requested and actual assignments, readiness, capability, fallback,
and whether the assignment is an Orion default or user override. `team role show`,
`team role set`, and `team role reset` provide persistent management without editing
YAML.

## Provider and engine validation

Planning assignments validate the provider, configured credential state, discovered
model, and enabled agent before a provider call. `active-planning-model` reuses Orion's
existing routing profile and reports any actual fallback. Explicit provider/model
assignments do not silently change.

Implementation and Tester validate the installed CLI and Orion adapter before
execution. They do not use planning fallbacks. An unavailable implementation engine
stops before approval consumption or workspace changes.

## Immutable role artifacts

Persisted AI Team tasks now snapshot all five role assignments. Produced planning
artifacts record the requested and actual provider/model, fallback reason, estimated
tokens and cost, and execution duration. The approval hash covers the assignment
snapshot for new plans. Older task and approval documents remain readable, and no
provider credential enters configuration or artifacts.

## Documentation as product behavior

The living User Guide now includes a five-minute Quick Start, complete Gmail and
Microsoft Mail setup, current feature and command coverage, representative workflows,
best practices, safety, and troubleshooting. Orion's evergreen Definition of Done now
requires tests, help, User Guide, feature documentation, changelog, manual verification,
and final credential/Vault review for every applicable change.

## Safety compatibility

Conductor does not weaken Gatekeeper. Immutable one-use approvals, plan hashes, exact
workspace binding, Codex CLI capability checks, noninteractive execution,
workspace-write sandboxing, network isolation, structured results, Awaiting Review,
and conflict-safe rollback remain unchanged. Codex Bridge still does not commit, push,
merge, tag, or open a pull request.

## Verification

The complete automated suite passes **362 tests**. Focused role-routing and bridge
coverage passes **88 tests**. Manual release verification covers role display,
assignment persistence/reset, fallback reporting, fail-closed engine behavior, and
credential-free task artifacts.
