# Orion v0.5.7.1 — Forge

Forge v0.5.7.1 fixes the final integration gap between Execution Engine discovery and
Codex Bridge startup.

## Single resolved-engine handoff

Previously, `team implement` resolved and validated Codex in the command router, then
Codex Bridge immediately repeated the availability probe. If those two probes
disagreed, Orion printed that execution was starting and then reported that no engine
was available.

The router now hands its validated `ExecutionEngine` snapshot directly to the bridge.
The exact executable path that passed preflight becomes the subprocess executable,
with no second detection or registration lookup. Direct bridge callers that do not
provide a snapshot still perform one equivalent engine check before approval claiming.

## Safety behavior

- Missing engines are rejected before an approval claim is created.
- Plan hashes and workspace bindings are still revalidated before execution.
- Approvals remain immutable and single-use once a real run is claimed.
- Launch failures remain sanitized and persisted without raw process errors.
- Codex still stops at `Awaiting Review` and cannot perform Git or pull-request actions.

## Verification

The v0.5.7.1 regression suite contains **256 passing tests**. New coverage reproduces
a successful router probe followed by a failing second probe and verifies that Orion
uses the first validated engine exactly once through structured result persistence.
