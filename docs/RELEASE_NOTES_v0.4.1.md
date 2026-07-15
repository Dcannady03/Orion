# Orion v0.4.1 — Polaris Vault

Orion now provides one credential-management workflow for cloud AI providers. Use `vault add gemini` or `vault add openai` to securely enter, verify, and store a key outside normal configuration. `vault health` verifies configured providers, while `vault remove` removes local credentials and safely returns Orion to Ollama when necessary.

## Security model

- Secrets are never written to `config/default.yaml`.
- `.orion/vault.yaml` is ignored by Git and protected with owner-only permissions where supported.
- Environment variables take precedence over local storage.
- Legacy `.orion/secrets.yaml` data migrates automatically.
- Native OS credential-store support is planned as a compatible backend.

## Validation

131 automated tests passing.
