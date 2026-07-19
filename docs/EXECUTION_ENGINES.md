# Execution Engines

Execution discovery reports local implementation CLIs and desktop applications as
separate capabilities. Discovery is read-only and never grants an engine approval,
workspace access, or permission to execute.

## Command

```text
execution status
```

Representative Windows output:

```text
Execution Engines
==================================================

Codex CLI
Status:
Ready
Executable:
C:\Users\name\AppData\Roaming\npm\codex.cmd
PATH Visibility:
No
Discovery Source:
npm global directory (%APPDATA%\npm)
Version:
codex-cli 0.144.5
Version Probe:
Succeeded

Codex Desktop
Status:
Installed
CLI Support:
Separate CLI detected
Discovery Source:
Store package

ChatGPT Desktop
Status:
Not Installed
CLI Support:
No
```

The full report also includes Claude Code, Gemini CLI, and Orion's Python runtime.

## Status meanings

- **Ready** — the CLI path resolved and completed a bounded `--version` probe.
- **Installed but not executable** — a path or wrapper exists, but the launch or
  version probe failed. The safe diagnostic distinguishes launch, timeout, and
  nonzero-version failures.
- **Not Installed** — no candidate was found through supported discovery sources.
- **Detection Error** — a bounded discovery mechanism was unavailable or failed, so
  Orion will not claim absence as certainty.
- **Unsupported as CLI** — the capability is a desktop application or has no CLI
  adapter. Desktop status remains independently Installed, Not Installed, or Detection
  Error and its `CLI Support` field explains the boundary.

Diagnostics include only current PATH visibility, the resolved executable, discovery
source, bounded version-probe result, and safe error category. Orion does not print the
PATH value, the full environment, process output from failed probes, or secrets.

## Reusable CLI resolution

`ExecutableResolver` handles Codex, Claude, and Gemini from engine-registry metadata.
Non-Windows systems keep their normal extensionless command lookup. Windows searches
these forms for each CLI:

```text
<command>.cmd
<command>.exe
<command>
<command>.ps1
```

All common forms are considered. A working `.cmd` or `.exe` is preferred over a
PowerShell shim when several variants resolve. `shutil.which()` remains the primary
PATH lookup and extensionless npm shims are accepted.

If PATH candidates are missing or present but broken, Windows discovery checks:

1. `%APPDATA%\npm`;
2. the global prefix returned by `npm prefix -g`, only when npm itself resolves.

Orion does not require npm to rediscover a wrapper already visible on PATH or in the
conventional per-user npm directory. When the host process has an incomplete PATH,
Orion also makes the conventional `%ProgramFiles%\nodejs` runtime visible only to a
discovered per-user npm wrapper; it does not modify the user's environment.

## Safe version and execution probes

Each candidate receives a short, bounded `<resolved executable> --version` probe.
Standard output and standard error are captured separately; a successful version
written to stderr is accepted. Timeouts, launch failures, and known broken-wrapper
messages become safe diagnostic categories.

Native executables run directly. On Windows, `.cmd` and `.ps1` shims are launched with
their platform interpreter, a fixed argument list, `shell=False`, captured output, and
a bounded timeout. Orion never builds a free-form command from user input.

The immutable `ExecutionEngine` retains the exact path, source, PATH visibility,
version, and probe result. `team implement` passes that object from the router to Codex
Bridge. The bridge does not call `which()`, run another probe, or fall back to a bare
`codex` command. Direct bridge callers must supply an equally validated snapshot.

## Desktop applications

`WindowsAppDetector` performs a bounded, no-shell PowerShell Appx identity query and
uses exact product matching:

- `OpenAI.Codex` identifies **Codex Desktop**;
- a ChatGPT package identity, Start menu entry, application-catalog record, or known
  ChatGPT install path identifies **ChatGPT Desktop**.

An OpenAI-branded package is never assumed to be ChatGPT. If the Appx query is blocked
and no independent evidence exists, status is Detection Error rather than a false Not
Installed. Desktop applications are informational and never become execution engines.
Codex Desktop may display `CLI Support: Separate CLI detected` when the independent
Codex CLI probe is Ready. ChatGPT Desktop always displays `CLI Support: No`.

## Adapter and approval boundary

Claude Code and Gemini CLI can be Ready, but this release has no implementation adapter
for them. Python readiness describes Orion's runtime and is not an AI implementation
adapter. Codex Bridge Phase 1 supports only a Ready Codex CLI.

`team implement` discovers Codex before claiming an immutable plan approval. When no
supported engine is Ready, Orion prints the capability summary and leaves the approval
unconsumed. If Codex disappears after preflight, the persisted run records the existing
sanitized `codex_cli_unavailable` category. Plan hashes, one-time claims, active-
workspace confinement, structured results, and the Awaiting Review stop remain
unchanged.

The configured default remains:

```yaml
execution:
  default_engine: codex
```
