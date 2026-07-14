# Orion v0.3.2 — Discovery

Discovery gives Orion a safe, extensible application-launch capability without hardcoding every installed program.

## Highlights

- Scans Windows Start Menu and desktop shortcuts into a project-local catalog.
- Matches natural names using exact, partial, fuzzy, and personal-alias resolution.
- Falls back to Windows Search when Orion cannot confidently identify an installed application.
- Sends every launch through the existing Action and Safeguard approval pipeline.

## Commands

- `apps scan`
- `apps list`
- `apps find <name>`
- `app alias <alias> = <application name>`
- `open <application>`
- `action approve <id>`
