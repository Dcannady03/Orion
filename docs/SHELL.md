# Shell Execution

## Purpose and boundary

The `shell` command runs one shell command at a time and reports its
outcome: exit code, duration, and captured stdout and stderr. It is the
most safety-sensitive feature in Orion, so it is gated twice: an
explicit opt-in permission, and an interactive approval for every single
execution. There is no way to run a command without both.

Orion runs the command through the platform shell — PowerShell on
Windows (`powershell.exe -NoProfile -NonInteractive -Command`), `sh`
on POSIX (`sh -c`) — invoked with an explicit argument list and no
`shell=True`. Standard input is closed; there is no interactive
session. The `ORION_POWERSHELL` environment variable overrides the
PowerShell binary (PowerShell 7 vs the inbox Windows PowerShell), the
same override the execution engines use for PowerShell scripts.

The working directory is the Orion workspace. Commands run as the same
user Orion runs as, with that user's full privileges — the approval
prompt exists because the command can do anything that user can do.

## Commands

```text
shell <command>
shell --timeout 60 <command>
shell --timeout=60 <command>
shell --json <command>
```

`--json` and `--timeout N` are recognized only as leading options,
before the command starts. Everything after them is the command,
verbatim: pipes, quotes, and `--flags` inside the command survive
exactly as typed, because the approval preview must show the exact
command that will run.

```text
shell hostname
shell Get-Date
shell Get-ChildItem --force | Sort-Object Name
shell --timeout 120 Get-ChildItem -Recurse C:\Backups
shell --json hostname
```

## Approval

Every execution asks first. The flow:

1. Orion resolves the command into a plan and shows it: the exact
   command text, the shell that will run it, the working directory,
   and the effective timeout.
2. Orion asks `Approve? [y/N]`. Anything but `y` or `yes` cancels
   with nothing run — and the refusal is recorded (see Audit).
3. On approval, Orion runs the command and reports the result.

There is no `--yes` bypass. `--json` affects the result output only;
the approval prompt is always an interactive text flow. When the
command completes (exit 0, or a non-zero exit — see Results), `--json`
prints the full result as JSON.

## Permissions

Shell execution is off by default. Two things must both be true for a
command to run:

1. **The permission is on.** `shell.arbitrary_commands` must be `true`
   in Orion's configuration (`config/default.yaml` ships it as
   `false`). To enable it, add this to `~/.orion/config.yaml` and
   restart Orion:

   ```yaml
   shell:
     arbitrary_commands: true
   ```

   Enabling only permits Orion to run shell commands at all; every
   command still asks for approval. Only a literal boolean `true`
   permits execution — a missing value, `false`, or any non-boolean
   value refuses with a clear message. When no configuration is loaded,
   execution is refused.
2. **The command is approved** at the prompt, as above.

### How this relates to agent permissions

Orion's agent permission model defines `arbitrary_commands` per agent
(`ShellPermissions` in the agent registry, `run_commands` capability
plus the `shell` policy in the managed model). Those permissions govern
agents — and agents execute no tools in Phase 1, so no agent can run a
shell command through them today. The console operator is not an agent,
so the console `shell` command is governed by the operator-level
`shell.arbitrary_commands` setting instead, which mirrors the agent
model's boolean exactly: same name, same default-deny, same explicit
opt-in. If agent tool execution ever lands, it will consult the agent's
own permissions, not this setting.

## Results

A command that completes returns its full outcome:

| Outcome | Result | Recorded as |
| --- | --- | --- |
| Exit code 0 | success | success |
| Non-zero exit | success with a warning naming the exit code | failed |
| Timeout | failure ("timed out ... and was killed") | failed |
| Shell missing / cannot start | failure | failed |

A non-zero exit is a complete, reportable outcome, not a crash: the
command ran, and its exit code and output are the answer. A timeout
kills the process (there is no orphaned command left running) and
reports whatever partial output was captured.

## Limits

| Limit | Value |
| --- | --- |
| Default timeout | 30 seconds |
| Maximum timeout | 300 seconds (`--timeout` above 300 is clamped; the plan shows the effective value) |
| Output per stream | Last 8,192 characters, line-preserving; the result says when output was truncated |

The recovery-history record keeps a shorter sample of each stream (500
characters); the full (truncated) output lives in the command result,
including `--json` output.

## Audit

Every execution — and every refusal — is recorded in the recovery
history with kind `shell`:

```text
recovery --kind shell
```

Each record carries the command (target, capped at 200 characters),
the working directory, the exit code or timeout, the duration, and a
sample of the output. Refusals are recorded too: a permission-off
refusal names the setting, and an approval refusal says "approval not
given". Unlike docker control (where a refused action changes nothing
and is not recorded), a refused shell execution is itself a
security-relevant event worth auditing — it shows someone, or
something, tried to run a command.

Validation failures (empty command, bad timeout, missing working
directory) are not recorded: nothing was attempted.

## Unavailable shell

When the shell binary cannot be found or started, the command fails
closed with a clear message and the failed attempt is recorded. Nothing
is retried automatically.

## Safety guarantees

- No silent execution: every run needs the permission on **and** an
  explicit `y`/`yes` at the prompt.
- The approval preview shows the exact command, directory, and
  timeout — approval is informed, never blind.
- Fail closed: permission off, unclear, or unreadable means refused.
- Bounded: timeouts kill the process; output is capped; stdin is
  closed.
- Audited: every attempt and every refusal is recorded in the
  recovery history (`recovery --kind shell`).

## See also

- [User guide](USER_GUIDE.md) — `shell` in the command reference.
- [Roadmap](ROADMAP.md) — "Approval-based shell execution".
- [Docker integration](DOCKER.md) — the approval pattern `shell`
  follows (`docker start`/`stop`/`restart`, `remediate apply`,
  `backup import`).
