# Orion v0.6.1 — Gatekeeper

Gatekeeper completes Orion's first interactive approval-to-implementation workflow.
After `team plan` renders the consolidated Architect and Engineer plan, the interactive
console offers Y/N/D approval. Only explicit Y or Yes creates the existing immutable,
one-use approval and immediately hands it to the same bounded implementation path as
`team implement`. Details are read-only, No launches nothing, and empty input or Ctrl+C
cancels safely.

## One approval contract

Interactive and manual workflows use the same persisted approval service. Every
approval remains bound to the exact plan SHA-256, active workspace and capability,
Codex execution engine, operation, and one implementation. A changed plan, changed
workspace, mismatched execution context, or reused approval fails before Codex starts.
`team plan --manual`, `team approve`, and `team implement` remain available for scripts,
tests, and recovery.

## Codex CLI compatibility

Orion performs one bounded `codex exec --help` probe for the already-resolved
executable and caches its supported options for the process. Required sandbox,
configuration-isolation, workspace, JSONL, and result-schema arguments fail closed.
Optional compatibility flags, including Codex's duplicate approval option, are emitted
only when supported by the installed CLI.

Orion's external immutable approval remains authoritative. The noninteractive Codex
child receives `approval_policy="never"` only after Orion validates that approval, so an
unavailable duplicate Codex approval flag does not weaken the gate.

## Exact workspace-write boundary

The bridge passes the immutable approved workspace unchanged through `--cd` and uses
`--sandbox workspace-write` plus the matching strict configuration. It no longer
supplies an empty writable-roots override, and it never grants a parent folder, user
profile, `--add-dir`, or unrelated directory.

Native Windows runs explicitly select Codex's preferred elevated sandbox inside the
isolated configuration. This preserves working file edits even though Orion continues
to ignore broader user Codex configuration. Network access, temporary-directory root
discovery, web search, MCP, apps, plugins, hooks, sub-agents, and Git release actions
remain unavailable.

## Review and diagnostics

Every run persists structured implementation results, sanitized diagnostics, a bounded
workspace baseline, observed changes, and a redacted diff beneath external Orion user
data. Raw stdout and stderr are never persisted. Execution always stops at Awaiting
Review; it does not commit, push, merge, tag, or open a pull request.

## Verification

The complete automated suite passes **351 tests**. Manual Windows verification with
Codex CLI 0.144.5 created exactly one approved UTF-8 file in a Standard workspace,
reported it as the only observed change, preserved every sandbox restriction, exited
successfully, and stopped at Awaiting Review.
