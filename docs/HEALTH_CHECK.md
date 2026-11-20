# Orion v0.4.1 Health Check and Health Graphs

## Purpose and boundary

The unified health check answers one question: is Orion itself in good
shape right now? It inspects dependencies, providers, and storage in one
place and reports each area as OK, WARNING, ERROR, or UNKNOWN. The
overall result is the worst section.

The health check is read-only and never changes runtime state. The one
exception is `health --record`, which saves one snapshot so `graphs` can
show health over time.

Health graphs build on those snapshots: one ASCII sparkline per section,
plus a live dependency tree of what Orion's health depends on.

## Commands

```text
health
health disk
health providers
health services
health config
health workspace
health events
health --record
health --json
graphs
graphs disk
graphs dependencies
graphs --json
```

## The six sections

| Section | What it checks |
| --- | --- |
| `disk` | Free space on the user-data volume. Warning below 1 GiB or 10% free. Error below 256 MiB or 5% free. |
| `providers` | Every registered provider: enabled, configured, active, and model. Warning when no provider is active. Error when the active provider is not configured. |
| `services` | Every registered service that reports a status. Available maps to OK, degraded to WARNING, unavailable to ERROR; the section takes the worst reporting service. UNKNOWN when no service reports a status. |
| `config` | Whether the configuration loaded. Error when it is missing or empty. |
| `workspace` | The configured workspace path. Warning when it does not exist; error when it exists but is not a directory. |
| `events` | The Event Store. Warning when it is disabled or reports warnings; otherwise OK, with the latest event time. |

`health` with no section runs all six. `health <section>` runs one.

## States and isolation

Each section reports one of OK, WARNING, ERROR, or UNKNOWN. The overall
result is the worst section: ERROR is worst, then WARNING, then UNKNOWN,
then OK.

Every check is isolated: a check that fails to run reports UNKNOWN
instead of hiding the remaining sections.

## Snapshots and graphs

`health --record` appends the report to the snapshot file (below). At
most 1,000 snapshots are kept; older lines are dropped as new ones
arrive. Recording a single section saves only that section; timelines
for other sections simply skip that snapshot. Recording is the only
write the health command performs, and only with `--record`.

`graphs` renders one ASCII sparkline per section from the last 100
snapshots:

```text
= ok, ~ warning, X error, ? unknown (oldest to newest)
```

Sparklines longer than 80 characters are downsampled. Each timeline
also shows per-state counts, the number of state changes, and the first
and last snapshot times. Malformed snapshot lines are skipped and
counted, never reinterpreted.

`graphs <section>` shows one section's timeline. `graphs dependencies`
does not use snapshots: it runs a fresh health check and renders a live
dependency tree of providers, services, and subsystems with their
current states. The providers branch expands to one node per provider;
the services branch expands to one node per reporting service.

If no snapshots exist yet, `graphs` says so and points at
`health --record`.

## Data locations

Snapshots live outside the source repository:

```text
~/.orion/observability/health-snapshots.jsonl
```

`ORION_USER_DATA` relocates the same root.

## Safety guarantees

The health check and the graphs:

- never change runtime state, except for the one snapshot
  `health --record` appends;
- never launch agents or jobs;
- never invoke providers or execution engines;
- never create or consume approvals;
- never mutate workspaces.

## See also

- [Log Viewer](LOG_VIEWER.md)
- [Diagnostics and Diagnostic Runbooks](DIAGNOSTICS.md)
- [Security Audit](SECURITY_AUDIT.md)
- [Approval-Based Remediation](REMEDIATION.md)
- [Explicit Retry](RETRY.md)
- [Event Bus](EVENT_BUS.md)
