# Orion v0.5.4.1 — Sentinel

Sentinel v0.5.4.1 is a maintenance release that preserves Discord bot credentials and
access settings across Orion application updates.

## Update-safe credential storage

Orion now resolves relative vault paths beneath its persistent user-data directory,
placing the live vault under `~/.orion/vault/`. Package updates replace application
files but do not replace this external data.

## Safe automatic recovery

When the persistent vault is missing a credential, Orion searches legacy vault files
and application update backups and restores the newest available value. Recovery only
fills missing secrets: an existing Discord token is never overwritten by a backup.

Discord bot access settings can also be recovered when no current local configuration
exists. If a current local configuration is present, its values remain authoritative.

## Verification

The v0.5.4.1 regression suite contains 195 passing tests, including explicit coverage
that existing Discord tokens and settings are not overwritten during recovery.
