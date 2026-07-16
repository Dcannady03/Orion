# Orion v0.4.7 — Relay

Relay establishes Orion's Git and self-update foundation.

## Added

- `git status`, `git log`, and `git diff [staged]` for safe repository inspection.
- Approval prompts for `git pull` and `git push`.
- `update check` to fetch and compare the installed Orion revision with its upstream branch.
- `update` to back up `config/` and `.orion/`, reject dirty working trees, and apply only fast-forward updates.
- Separate Git contexts for the active project and the Orion installation.
- Workspace rebinding for project Git commands.
- Regression tests for repository status, dirty-tree protection, backups, and non-repository errors.

## Safety

Relay never force-resets a repository, never automatically commits changes, and never updates across a dirty working tree. Update backups are written beneath `.orion/backups/` and exclude prior backups to prevent recursion.
