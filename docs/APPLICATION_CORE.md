# Orion Application Core

## Purpose

Orion's application layer gives every client one provider-neutral way to invoke
structured capabilities. It is an incremental boundary over the existing services,
not a replacement service graph and not a REST or GUI implementation.

The current flow is:

```text
CLI parser
  -> application command handler
  -> domain service
  -> ApplicationResult
  -> CLI renderer
```

Command Center and AI Team now use this boundary. AI Team terminal parsing lives in
`ai_team_cli.py`; typed lifecycle coordination lives in
`ai_team_commands.py`. The core router recognizes the `team` family and delegates it.
The application handler has no direct `print()` or `input()` dependency. Interactive
agent selection, Y/N/D approval, and rollback confirmation remain in the CLI adapter.

## Structured results

`orion/application/results.py` defines an immutable result with:

- `status`: `success`, `warning`, or `failure`;
- `message`: a concise human-readable summary;
- `data`: recursively copied, immutable, JSON-compatible payload data;
- `warnings` and `errors`: interface-neutral diagnostics;
- `next_actions`: safe follow-up descriptions.

`to_dict()` and `to_json()` produce ordinary JSON-safe structures. Construction
rejects live services, provider clients, paths, or other non-JSON objects so a future
API cannot accidentally serialize internal process state.

## Capability registry

`orion/application/capabilities.py` describes stable capability IDs, mutation and
approval metadata, required permissions, and input/output schemas. Registration
rejects duplicate or malformed IDs, listing is deterministic, and definitions execute
nothing.

The catalog covers the Command Center job family, the extracted AI Team lifecycle,
and a small representative set of other existing operations. The real Team capability
IDs are:

- `team.list` and `team.show` for read-only inspection;
- `team.plan` for bounded provider-backed planning;
- `team.approve` for explicit immutable-plan approval;
- `team.implement` for approval-bound workspace execution;
- `team.validate` for bounded read-only automatic validation;
- `team.documentation_review` for bounded read-only documentation assessment;
- `team.rollback` for explicitly confirmed safe restoration; and
- `team.sync` for linked Command Center reconciliation.

There is no `team.cancel`, `team.review`, or Team completion capability because the
authoritative Team services do not implement those operations. Capability metadata
never grants permission and never bypasses Vault, workspace, sandbox, validation, or
approval controls.

## AI Team lifecycle results

Team task results include their task ID, persisted status/stage, goal, resolved agents,
provider routes, approval state, risks, timestamps, and valid next actions. Run results
include their run/task/approval IDs, immutable plan hash, workspace identity, persisted
run status, projected review stage, implementation/validation/documentation status,
changed-file and test summaries where present, risks, timestamps, and next actions.

The persisted task states remain `planning`, `awaiting_approval`, and `failed`. Run
states remain `executing`, `awaiting_review`, `failed`, and `rolled_back`. Within
`awaiting_review`, the application result projects `validation`,
`documentation_review`, or `final_review` without changing stored schemas. A final
review is a human boundary; this milestone does not create an automatic accept command.

Command Center receives the shared `TeamPlanRequest` boundary in the complete Orion
runtime, then continues to reconcile its organization-facing job projection from
authoritative Team, approval, run, validation, and documentation records. Isolated
legacy service construction retains a direct Team fallback for compatibility. Neither
path routes Command Center through terminal syntax.

## Command Center compatibility

Both `cc` and `command-center` retain their existing subcommands and option syntax.
The application handler calls the same `CommandCenterService` and Team integration,
including dry-run, launch, sync, show, cancellation, workspace validation, route
resolution, immutable approval state, and provider/execution-engine neutrality.
Service exceptions become failure results. Launch warnings, preview errors, approval
requirements, and next actions remain structured.

The CLI renderer prints the result message and any diagnostic or next-action value not
already present in that message. JSON-request output remains recognizable to existing
scripts.

## Remaining work

The core router still owns many unrelated command families and their interactive
rendering. AI Team retains one `team_rollback()` compatibility wrapper for callers
that invoked that router method directly; all current CLI dispatch uses the adapter.
Large compatibility messages also remain application-result summaries while future
renderers can consume the semantic payload instead.

Before adding a REST API or GUI:

1. migrate additional command families to typed application requests;
2. replace legacy preformatted messages with fully semantic renderer views where
   compatibility permits;
3. complete capability schemas and permission mapping for migrated operations;
4. define authentication, concurrency, cancellation, and streaming contracts;
5. add interface-independent integration tests.

REST, GUI, voice, mobile, and always-on server features remain planned and are not
implemented here.
