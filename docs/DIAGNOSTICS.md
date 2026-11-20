# Orion v0.4.6 Diagnostics and Diagnostic Runbooks

## Purpose and boundary

`diagnostics` builds an environment report for issue reports: one
snapshot of the running Orion instance — version, platform, providers,
health, storage, services, events, and configuration. The report
contains configuration flags only and never includes API keys, tokens,
or environment values, so it is safe to paste into an issue report.

Runbooks are the companion: step-by-step procedures for common
symptoms, built on the same commands.

Both commands are read-only.

## Commands

```text
diagnostics
diagnostics --json
runbooks
runbooks diagnose
runbooks for provider unavailable
runbooks disk-full
```

## The eight report sections

| Section | Contents |
| --- | --- |
| `orion` | Orion version, git commit, whether the working tree is dirty, install root, user-data root. |
| `platform` | Python version and implementation, operating system and release, machine type, CPU count. |
| `providers` | Per-provider enabled, configured, active, and model flags, from the health check. |
| `health` | Overall health state and each section's state and detail. |
| `storage` | Disk state, path, total and free bytes, free percent. |
| `services` | Registered and reporting service counts, per-service states, and the list of services with problems. |
| `events` | Event Store state, latest event time, warnings, and the number of daily event files. |
| `config` | Config state, key count, config path, workspace path and state. |

The git commit and dirty flag are best-effort: a 5-second timeout or
a non-git install reports `unknown`. Each section is isolated: a
failing section reports an error instead of hiding the rest.

## Runbooks

A runbook is a structured procedure for one symptom: the checks to run
(mapped to real Orion commands), the likely causes with the evidence
that confirms each one, and the fix for each cause. Runbooks are
descriptive only — they never change anything.

| ID | Title | Symptom |
| --- | --- | --- |
| `provider-unavailable` | AI provider unavailable | Orion cannot reach its AI provider, or the provider is not configured. |
| `disk-full` | Disk space warnings | The health check warns about low disk space on the user-data volume. |
| `events-unhealthy` | Event Store warnings | The health check reports Event Store warnings, or event history looks wrong. |
| `service-degraded` | Service degraded or unavailable | A registered service reports degraded or unavailable status. |
| `startup-failure` | Orion fails to start | Orion exits during startup or immediately after launching. |
| `slow-responses` | Slow AI responses | Orion answers, but responses take much longer than usual. |
| `container-unreachable` | Cannot reach a container | A container's web page or service can't be accessed: the browser shows connection refused, the page won't load or keeps loading, or the service is unreachable. |

`runbooks` lists all seven. `runbooks <id>` shows one runbook in full:
its checks, likely causes, and fixes.

`runbooks for <symptom>` matches by keyword. Runbooks are scored by
how many of the query words appear in their title, symptom, and
causes; the best match comes first.

`runbooks diagnose` runs the health check and suggests the runbooks
matching the problems it finds, in a fixed order: providers, disk,
events, services, config. An all-OK report suggests nothing.

Note: one cause in the startup-failure runbook mentions restoring the
Vault from a backup. Orion's own backups never include the Vault (see
[Backup, Restore, and Recovery History](BACKUP_RESTORE.md)), so that
means a Vault copy made outside Orion; without one, credentials are
re-entered, not recovered.

## Safety guarantees

Diagnostics and runbooks:

- never read secrets or environment values;
- never modify configuration, data, or runtime state;
- never launch agents or jobs;
- never invoke providers or execution engines.

## See also

- [Health Check and Health Graphs](HEALTH_CHECK.md)
- [Log Viewer](LOG_VIEWER.md)
- [Security Audit](SECURITY_AUDIT.md)
- [Backup, Restore, and Recovery History](BACKUP_RESTORE.md)
