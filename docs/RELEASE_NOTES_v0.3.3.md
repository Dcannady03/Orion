# Orion v0.3.3 — Companion

**Status:** Complete  
**Phase:** 3 — Automation  
**Purpose:** Make Orion feel natural to use without weakening its Action and Approval architecture.

## Highlights

- Conversational application approval with `Y`, `N`, `A`, and `D`
- Internal UUIDs hidden during normal use
- Persistent, narrowly scoped “always allow” trust for applications
- Numbered pending-action queue
- Developer Mode for action IDs and diagnostics
- Workspace-isolated Companion settings and trust decisions
- Up/Down command history and Tab completion
- Semantic colored output with a safe fallback
- Time-aware startup, task-oriented help, and compact status dashboard
- Graceful interruption and shutdown handling

## Example

```text
Orion> open chrome
I found Google Chrome.
Open it? [Y] Yes  [N] No  [A] Always allow  [D] Details: a
Got it. I'll open Google Chrome without asking next time.
Opening Google Chrome.
```

The action still travels through Discovery, Action Service, Approval Policy, Trust,
and History. Companion changes the presentation—not the safety model.

## Commands

```text
open <application>
action pending
action approve <number>
action deny <number>
developer on
developer off
settings
trust list
trust revoke <application>
```

## Installation

```powershell
python -m pip install -r requirements.txt
python -m orion.main
```

## Compatibility and migration

No data migration is required from v0.3.2. Existing project context, application
catalogs, aliases, action history, and memory remain compatible. Companion creates
or updates workspace-local settings as needed.

## Quality gate

```text
Ran 71 tests
OK
```

All Companion, Discovery, Safeguard, Action, Conversation, Knowledge, Plugin,
Workspace, and Memory tests pass.

## Next milestone

**v0.3.4 — Morning Star:** a modular Briefing Service and dynamic startup dashboard.
