# Orion v0.5.0 — Foundation

Orion now separates application code from personal data.

## User data location

Mutable application data lives under `~/.orion` (or `%USERPROFILE%\.orion` on Windows):

- `config.yaml` — private settings and provider choices
- `profile.yaml` — user identity and First Contact profile
- `vault/vault.yaml` — local secrets
- `tokens/` — OAuth tokens
- `backups/` — update backups
- `logs/`, `cache/`, and `memory/` — runtime data

The Git repository contains code, tests, documentation, plugins, and read-only defaults only.

## Migration

On first launch, Orion migrates the previous `~/.orion/config/local.yaml` file to `~/.orion/config.yaml` and copies a legacy repository profile to the external profile location when necessary.

Workspace-specific project context remains inside each workspace by design.
