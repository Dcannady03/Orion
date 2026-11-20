# Orion v0.4.3 Provider Switching

## Purpose and boundary

`ai provider use` changes Orion's active AI provider immediately. The
switch is live the moment the command returns — no restart is needed —
and the choice is saved as the default for future sessions.

## Commands

```text
ai provider use ollama
ai provider use openai
ai provider use gemini
```

On success Orion prints:

```text
[OK] Active AI provider: <name>
The new provider is live now; no restart needed.
```

## What the switch does

Activating a provider creates the provider instance and points every
live reference at it: Orion itself, the Brain, and AI Control.
Routing and team services create providers on demand from the same
manager, so those three references are the complete live set. The
choice is then persisted as `providers.default` and the configuration
saved.

The switch is reversible: run the command again with another provider.

## Fail-closed rules

- The provider name must be one of `ollama`, `openai`, or `gemini`;
  anything else is refused.
- A cloud provider whose API key is not configured is refused — the
  switch does not happen and the active provider stays as it was.
  Orion prints `Could not activate provider: <reason>`. Ollama needs
  no API key.
- If the provider cannot be created, the switch is refused the same
  way and the active provider is unchanged.

## Related commands

- `ai providers` — list providers with their enabled, configured,
  active, and model flags.
- `ai provider configure <provider>` — store and verify an API key
  in Vault.
- `ai test <provider>` — verify a provider's connection and list its
  models without generating a billable response.
- `health providers` — provider state in the unified health check.
- `retry provider <name>` — retry a failing provider connection test
  with end-to-end verification.

## See also

- [Health Check and Health Graphs](HEALTH_CHECK.md)
- [Explicit Retry](RETRY.md)
- [Security Audit](SECURITY_AUDIT.md)
