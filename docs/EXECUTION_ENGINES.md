# Execution Engines

Execution Engine discovery explains which local runtimes Orion can see and which of
them can currently implement an approved AI Team plan.

## Command

```text
execution status
```

Example:

```text
Execution Engines
==================================================

Codex CLI
Status:
Not Installed

ChatGPT Desktop
Status:
Installed
CLI Support:
No

Claude Code
Status:
Not Installed

Gemini CLI
Status:
Not Installed

Python Executor
Status:
Ready
```

Desktop installation and CLI availability are different capabilities. ChatGPT Desktop
can be installed and usable as an application without providing a command-line engine
that Orion can invoke. Orion therefore never treats a desktop-app shortcut as a Codex
CLI installation.

## Detection

Orion checks engines without modifying the host:

- **Codex CLI:** on Windows, Orion resolves `codex.cmd`, `codex.exe`, then `codex`; on
  other platforms it resolves `codex`. The resolved executable must complete
  `--version` successfully.
- **ChatGPT Desktop:** detected from Orion's application catalog, common Windows or
  macOS installation locations, and bundled OpenAI Windows desktop package aliases.
  It is reported with `CLI Support: No`.
- **Claude Code:** the `claude` command must exist and complete `--version`.
- **Gemini CLI:** the `gemini` command must exist and complete `--version`.
- **Python Executor:** Orion's current Python executable must be a readable file.

A command found on `PATH` but blocked, broken, or unable to run is reported as `Not
Installed`. This prevents Windows application aliases from being mistaken for usable
CLI engines. Probe errors are reduced to internal safe categories and are not printed.

## Detection versus adapter support

Detection does not grant execution permission and does not mean Orion has an adapter
for that engine. Codex Bridge Phase 1 supports only a runnable Codex CLI. Claude Code
and Gemini CLI are detected now so future adapters can be added without redesigning
the status experience. Python readiness describes Orion's local Python runtime; it is
not an AI implementation adapter.

The configured default is reserved under:

```yaml
execution:
  default_engine: codex
```

Selecting unsupported adapters is intentionally deferred until those adapters have
their own sandbox, approval, output-schema, and persistence contracts.

## AI Team behavior

`team implement` performs engine discovery before Codex Bridge claims the immutable
plan approval. When Codex is unavailable, Orion reports the detected engines and
points to `execution status`. No local process starts, no run or claim artifact is
created, and the approval remains valid after the CLI is installed.

Discovery and implementation use the same resolver. Codex Bridge passes the exact
resolved path directly to the no-shell subprocess call; it never discards that path
and falls back to a bare `codex` command.

```text
No execution engine is currently available.

Detected:

✓ ChatGPT Desktop
✗ Codex CLI
✗ Claude Code
✗ Gemini CLI

Use:

  execution status

to configure an execution engine.
```

If Codex disappears after preflight but before the process starts, the existing Codex
Bridge failure record still captures the race as `codex_cli_unavailable` without raw
process output.
