# Orion v0.4.2 Backup, Restore, and Recovery History

## Purpose and boundary

`backup` exports Orion's configuration and runtime data to a ZIP
archive with a SHA-256 manifest, and restores it on request. Every
recovery Orion performs — backup imports, update rollbacks, team
rollbacks, remediations — is recorded in a transparent history that
`recovery` reads back.

Export, list, and verify are safe to run any time. Import is the one
backup operation that changes data, and it always asks for approval
first.

## Commands

```text
backup export
backup export --name before-upgrade
backup list
backup verify backups/orion-backup-20261120-120000.zip
backup import backups/orion-backup-20261120-120000.zip
recovery
recovery --kind backup-import
recovery --status failed
recovery rec-a1b2c3d4e5f6
recovery --json
```

## What an export contains

Exported:

```text
config.yaml
profile.yaml
events
missions
goals
command-center
team
agents
memory
observability
```

Never exported, for security and hygiene: `vault`, `tokens`, `logs`,
`cache`, `backups`. The manifest lists the exclusions.

Because the Vault is never exported, a backup cannot restore
credentials. A lost Vault means re-entering API keys and tokens.

## Export

Archives land in `~/.orion/backups/`, named
`orion-backup-<YYYYMMDD-HHMMSS>.zip`, or
`orion-backup-<name>-<stamp>.zip` with `--name`. A name is 1–64
characters of letters, digits, dots, underscores, or dashes, starting
with a letter or digit. Exporting to a filename that already exists is
refused.

Each archive contains a `manifest.json`: format version (currently
`1`), Orion version, export time, the user-data directory it came
from, the exclusion list, and every file's path, size, and SHA-256
checksum.

## Verify and list

`backup list` shows the archives newest first, with size, modified
time, and format version.

`backup verify` checks an archive against its manifest without
changing anything: every listed file must be present with a matching
size and SHA-256 checksum, and the format version must be `1`. Extra
files in the archive that are not in the manifest are reported as a
warning and ignored on import. A backup exported from a different
user-data directory is flagged with a warning too.

## Import

Import replaces data, so it is approval-gated:

1. The archive is verified first. A failed verification aborts the
   import before anything changes.
2. A plan is shown: how many files, how many existing files it will
   replace, how many new files it will add, and the safety backup
   name.
3. Orion asks `Approve? [y/N]` — anything other than `y` or `yes`
   cancels. Import is interactive and does not accept `--json`.
4. A pre-import safety backup (`pre-import-<stamp>.zip`) is written
   first, holding the current versions of the files the import will
   replace. It is written only when the import would replace existing
   files.
5. The files are restored into the user-data directory.

The import is recorded in the recovery history, whether it succeeds
or fails.

## Recovery history

`recovery` lists recorded recoveries newest first, with what was
restored, whether it succeeded, and the safety backup when one was
written. Failed attempts are recorded too.

| Option | Meaning |
| --- | --- |
| `--kind K` | `backup-import`, `update-rollback`, `team-rollback`, or `remediation`. |
| `--status S` | `success` or `failed`. |
| `--limit N` | Maximum records. Default 20, maximum 500. |

`recovery <id>` shows one record in detail. Record IDs look like
`rec-a1b2c3d4e5f6`.

The history is append-only JSONL at:

```text
~/.orion/observability/recovery-history.jsonl
```

Recording never blocks the recovery itself: if the history write
fails, the recovery still completes and the failure is reported as a
warning.

## Data locations

```text
~/.orion/backups/                              (backup archives)
~/.orion/observability/recovery-history.jsonl   (recovery history)
```

`ORION_USER_DATA` relocates the same root.

## Safety guarantees

- Exports never include the Vault or tokens.
- Import verifies the manifest before touching anything, and always
  asks for explicit approval.
- Archive paths are validated on import: absolute paths and
  parent-directory escapes are rejected.
- The recovery history is append-only; nothing rewrites past records.

## See also

- [Health Check and Health Graphs](HEALTH_CHECK.md)
- [Approval-Based Remediation](REMEDIATION.md)
- [Diagnostics and Diagnostic Runbooks](DIAGNOSTICS.md)
