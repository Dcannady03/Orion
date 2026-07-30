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

Before this milestone, Command Center's terminal handler combined request parsing,
service coordination, error mapping, and direct output. The router instantiated that
terminal handler directly. The application handler now owns parsing and coordination,
returns a structured result, and has no direct `print()` dependency. The terminal
class remains as a compatibility adapter and renderer.

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

The initial catalog intentionally covers the Command Center job family plus a small
representative set of existing operations. It is not a claim that every CLI command
has been migrated. Capability metadata never grants permission and never bypasses
Vault, workspace, sandbox, validation, or approval controls.

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
rendering. The next sensible extraction is the AI Team command family because it
already exposes explicit plan, approve, implement, validate, document, review, and
rollback stages.

Before adding a REST API or GUI:

1. migrate additional command families to typed application requests;
2. replace legacy preformatted messages with fully semantic renderer views where
   compatibility permits;
3. complete capability schemas and permission mapping for migrated operations;
4. define authentication, concurrency, cancellation, and streaming contracts;
5. add interface-independent integration tests.

REST, GUI, voice, mobile, and always-on server features remain planned and are not
implemented here.
