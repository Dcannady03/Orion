# Orion v0.4 Approval-Based Remediation

## Purpose and boundary

`remediate` is the capstone of the observability commands: it runs the
health check, proposes fixes for the problems it finds, and applies
them only with your explicit approval. Each proposal states its risk
and whether it can be undone; every applied fix is verified afterwards
and recorded in the recovery history.

## Commands

```text
remediate
remediate apply switch-provider-ollama
remediate apply --all
```

## The fixes

| ID | Trigger | Risk | Reversible | What it does |
| --- | --- | --- | --- | --- |
| `switch-provider-ollama` | Providers section is WARNING or ERROR, and the active provider is not Ollama | low | yes | Activates Ollama as the live provider. Reversible with `ai provider use <name>`. |
| `prune-old-backups` | Disk section is WARNING or ERROR, and more than 2 backups exist | medium | no | Deletes old backup archives, keeping the newest 2. The plan lists the exact files and total size. |
| `prune-old-events` | Disk section is WARNING or ERROR, and event files older than 90 days exist | medium | no | Deletes event history files older than 90 days, by file age. The plan lists the exact files and total size. |
| `create-workspace` | Workspace section is WARNING or ERROR, and the path does not exist | low | yes | Creates the missing workspace directory. Reversible by deleting it. |
| `start-container-<name>` | A container is stopped (exited or created) and publishes a host port | low | yes | Starts the container with `docker start`. Reversible with `docker stop <name>`. |

A proposal appears only when its problem is live and the fix applies:
no disk problem, no prune proposals; only two backups, no
prune-backups proposal; Ollama already active, no switch proposal.
When nothing is wrong, `remediate` reports that there is nothing to
remediate.

The `start-container-<name>` proposal is the one fix that does not
come from the health check: it comes from Docker. It appears only for
a stopped container with a real published host port (a host binding
in inspect, not merely an exposed port) — a stopped container with no
published port was probably stopped on purpose and never gets a
proposal. When Docker is unavailable, no container proposals appear
and the other fixes are unaffected. A container start applied through
remediation is recorded once, as kind `remediation`, not as kind
`docker` — it was applied as a remediation. See
[Docker integration](DOCKER.md) and `docker diagnose <name>` for the
diagnosis side.

## Applying a fix

1. `remediate` shows the plan. Nothing has been applied.
2. `remediate apply <id>` — or `apply --all` for the whole plan with
   one approval — shows the plan again and asks `Approve? [y/N]`.
   Anything other than `y` or `yes` cancels. There is no `--yes`
   bypass; approval is always interactive.
3. Before applying, Orion re-diagnoses: a fix whose problem is already
   gone reports "No longer needed" instead of acting on stale
   information.
4. Each applied fix is verified afterwards: a health re-check for the
   provider and workspace fixes, a targeted re-read for the pruning
   fixes. A fix that applies but does not verify is reported as
   "applied, not verified."
5. Applied fixes are recorded in the recovery history (`recovery`)
   as kind `remediation` — success when verified, failed when not.
   Fixes that were not applied are not recorded.

## Safety guarantees

- Nothing is applied without explicit approval.
- Destructive fixes (backup and event pruning) are flagged
  medium-risk and non-reversible, and the plan lists the exact files
  before you approve.
- A failed fix reports its error instead of crashing.
- Remediation only proposes the fixes above; it never invents
  actions.
- Applying re-diagnoses first, so a stale plan can never be applied
  to a system that has already healed.

## See also

- [Health Check and Health Graphs](HEALTH_CHECK.md)
- [Backup, Restore, and Recovery History](BACKUP_RESTORE.md)
- [Diagnostics and Diagnostic Runbooks](DIAGNOSTICS.md)
- [Explicit Retry](RETRY.md)
