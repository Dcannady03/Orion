# Orion v0.5.1 — Lifeline

Lifeline replaces Git-based stable updates with a package updater.

## Highlights

- `update check` queries GitHub for the latest pinned source package.
- `update` downloads and validates the package without running `git pull`.
- Application files are backed up before replacement.
- `~/.orion` user data is never replaced.
- `.git` and local virtual environments are preserved when present.
- A failed installation automatically restores the prior application.
- `update rollback` restores the newest application backup.
- Git commands remain available for development workspaces.

## Update architecture

Development copies continue to use Git for commits and pushes. Stable copies use HTTPS package downloads and no longer require a clean Git working tree.
