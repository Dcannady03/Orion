# Orion v0.4 Explicit Retry with End-to-End Verification

## Purpose and boundary

`retry` makes an explicit, bounded retry of a failing operation and
then verifies the result end to end. Retries are never automatic: you
run the command, Orion makes a small number of foreground attempts,
and reports every attempt plus the verification outcome.

## Commands

```text
retry provider ollama
retry health providers --attempts 2
retry backup C:\Users\you\.orion\backups\orion-backup-2026-11-20.zip
```

## The three operations

| Operation | The attempt | The verification |
| --- | --- | --- |
| `retry provider <name>` | Connect to the provider and list its models. The name must be `ollama`, `openai`, or `gemini`. | The provider still reports configured, and the configured model (if one is set) is in the returned model list. This tests the connection; it does not switch providers. |
| `retry health [section]` | Run the health check (or one section); it must be OK. | Re-run the check and require it to still be OK, confirming the recovery holds. |
| `retry backup <path>` | Verify the archive against its manifest. | Re-read the archive independently and verify it again. |

## Attempts and verification

- Default 3 attempts; `--attempts N` sets 1 to 5. Attempts stop at
  the first success and run one after another with no delay between
  them.
- A failed attempt never crashes the retry: exceptions are recorded
  as failed attempts with their detail.
- Verification is a separate check, not a repeat of the attempt, and
  it runs only when an attempt succeeded. If every attempt fails, the
  report says there is nothing to verify.
- A retry only counts as successful when an attempt succeeded AND
  verification passed. An attempt that succeeds but fails
  verification is reported as "operation succeeded but verification
  failed."
- Every attempt is reported with its number, outcome, duration, and
  detail.

## What retry never does

- Nothing retries automatically in the background.
- Mission operations keep their existing no-automatic-retry policy;
  this command retries only what you explicitly ask it to.
- Retry does not change configuration, switch providers, or restore
  data.

## See also

- [Health Check and Health Graphs](HEALTH_CHECK.md)
- [Backup, Restore, and Recovery History](BACKUP_RESTORE.md)
- [Provider Switching](PROVIDER_SWITCHING.md)
- [Diagnostics and Diagnostic Runbooks](DIAGNOSTICS.md)
