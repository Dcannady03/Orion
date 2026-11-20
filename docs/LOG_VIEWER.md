# Orion v0.4.0 Log Viewer

## Purpose and boundary

`logs` searches Orion's runtime logs as one unified log. Orion writes
no traditional file logs: the Event Store is the runtime log, and plain
text files under `logs/` (for example the network monitor's logs) are
searched too, so one command covers both sources.

Log searches are read-only and never modify any log file.

## Commands

```text
logs
logs --severity error
logs --type goal.proposal.accepted
logs --search "disk"
logs --start 2026-11-20T10:00:00Z --end 2026-11-20T11:00:00Z
logs --source files --limit 20
logs --json
```

## Filters

Every filter is optional. Entries are returned newest first.

| Option | Meaning |
| --- | --- |
| `--severity S` | Exact severity match: `debug`, `info`, `notice`, `warning`, `error`, or `critical`. |
| `--type T` | Event type, for Event Store entries only (for example `goal.proposal.accepted`). |
| `--search TEXT` | Case-insensitive text search over the event type, source, and message (events) or the file path and message (plain files). |
| `--start ISO` | Earliest timestamp, ISO-8601. A timestamp without an offset is read as UTC. |
| `--end ISO` | Latest timestamp, ISO-8601. Start must not follow end. |
| `--limit N` | Maximum entries returned. Default 50, maximum 1,000. |
| `--source S` | `all` (the default), `events`, or `files`. |

## How entries are read

Event Store entries carry their recorded severity and event type.
Plain log lines get their severity from the first severity keyword in
the line (`fatal` counts as critical, `warn` as warning, `trace` as
debug); a line with no severity keyword is `info`.

Plain lines with a parseable timestamp get it; a time filter excludes
lines without one. Messages longer than 500 characters are truncated.

Only `.log` and `.txt` files are searched, and only the 50 newest
files; files larger than 10 MiB are skipped. Warnings — skipped files,
Event Store warnings — are reported with the results, not hidden.

## Data locations

```text
~/.orion/events/YYYY-MM-DD.jsonl   (Event Store, the runtime log)
~/.orion/logs/                     (plain text log files)
```

`ORION_USER_DATA` relocates the same root.

## Safety guarantees

Log searches:

- never modify any log file;
- never publish events;
- never launch agents or jobs;
- never invoke providers or execution engines.

## See also

- [Event Bus](EVENT_BUS.md)
- [Health Check and Health Graphs](HEALTH_CHECK.md)
- [Diagnostics and Diagnostic Runbooks](DIAGNOSTICS.md)
