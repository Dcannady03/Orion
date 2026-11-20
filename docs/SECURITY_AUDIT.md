# Orion v0.4.7 Security Audit

## Purpose and boundary

`audit` runs a read-only security audit: Vault and token storage,
secrets in configuration files and recent events, provider credential
sources, outbound interfaces, loaded plugins, and explicit approval
policies.

The report never contains secret values — findings name files, keys,
and counts only — so it is safe to paste into an issue report.

## Commands

```text
audit
audit --json
```

## The ten checks

| Check | Category | What it looks at |
| --- | --- | --- |
| `vault-file` | permissions | Whether the Vault file exists and who can read it. Missing is a WARNING: no credentials are stored yet. |
| `tokens-dir` | permissions | Whether the tokens directory exists and who can read it. Missing is OK: no tokens are stored. |
| `secrets-in-config` | secrets | Scans `config.yaml` and `profile.yaml` for secret key names (`api_key`, `secret`, `token`, `password`, and similar) and secret-like values. Any found is an ERROR: move credentials to Vault. |
| `provider-sources` | secrets | Where each cloud provider's credential comes from. Keys from environment variables are a WARNING: child processes can read them, so Vault storage is preferred. |
| `ollama-url` | interfaces | Whether Ollama is on a local address. A non-local address is a WARNING: prompts leave this machine. |
| `cloud-endpoints` | interfaces | Which cloud providers are enabled and their endpoints. Informational: prompts leave this machine by design when a cloud provider is enabled. |
| `discord` | interfaces | Whether a Discord bot token is configured — presence only, never the value. |
| `plugins` | interfaces | Loaded and failed plugin counts and names. Failed plugins are a WARNING. Plugins run as code inside Orion. |
| `approval-policies` | policies | Actions explicitly allowed without approval. Any are a WARNING, listing them. |
| `event-redaction` | secrets | Scans the latest event file for secret-like values. Any found is an ERROR: events must be redacted before storage. |

## Permissions: Windows vs POSIX

On POSIX systems the audit checks permission bits: a Vault file or
tokens directory readable by other users is an ERROR; owner-only is
OK. On Windows, file access is controlled by ACLs, which the audit
does not verify: instead it checks that the Vault and tokens live
under the user profile, warning if they are outside it.

## Report rules

- Each check is isolated: a failing check reports UNKNOWN instead of
  hiding the rest.
- The overall result is the worst finding.
- Configuration files larger than 1 MiB are skipped when scanning for
  secrets. If the latest event file is larger than 1 MiB, the
  event-redaction check reports UNKNOWN.
- Only the latest event file is scanned for secret-like values.
- No finding ever contains a secret value: matches are counted, key
  names are listed, values are never shown.

## See also

- [Health Check and Health Graphs](HEALTH_CHECK.md)
- [Diagnostics and Diagnostic Runbooks](DIAGNOSTICS.md)
- [Provider Switching](PROVIDER_SWITCHING.md)
