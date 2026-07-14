# Orion

Orion is a local-first personal intelligence operating system. It combines AI,
memory, project knowledge, plugins, discovery, and approval-based actions behind a
cohesive command-line companion.

## Current release

**v0.3.4 — Morning Star**

Morning Star adds a modular daily briefing that every future service can extend:

```text
Today's Briefing
--------------------------------------------------
  [OK] Workspace: Orion is ready
  [OK] AI: ollama:qwen3.6:35b is connected
  [OK] Applications: 207 discovered
```

The startup screen does not know about Weather, Email, Calendar, or Docker. Each service
will register its own provider, allowing Orion to grow without hardcoded startup logic.

## Quick start

```powershell
python -m pip install -r requirements.txt
python -m orion.main
```

## Core abilities

```text
help                         Show Orion's abilities
status                       Show the system dashboard
briefing                     Refresh the current briefing
ask <question>               Talk to the configured AI provider
workspace                    Inspect the active workspace
files                        List workspace files
code tree                    Inspect the source tree
remember <key> <value>       Store session memory
index build                  Build the structural knowledge index
apps scan                    Discover installed applications
apps find <name>             Search the application catalog
app alias <alias> = <name>   Teach Orion an application alias
open <application>           Safely launch an application
settings                     Show Companion settings
trust list                   Show trusted applications
```

Use the Up/Down arrows for command history and Tab for command/application
completion in supported terminals.

## Safety model

All operating-system actions use Orion's Action framework. Policies can allow,
require approval, or deny an action. Approval decisions and execution results are
auditable. “Always allow” trust is narrowly scoped and stored per project workspace.

## Project structure

- `orion/core` — runtime, configuration, profile, and routing
- `orion/intelligence` — Brain, identity, intents, and AI providers
- `orion/actions` — action models, policies, execution, and history
- `orion/services` — workspace, discovery, Companion, and Morning Star briefing services
- `orion/conversation` — persistent conversation context
- `orion/knowledge` — structural workspace index
- `orion/plugins` — plugin contracts and lifecycle
- `tests` — automated regression suite
- `docs` — architecture, constitution, roadmap, changelog, and release notes

## Testing

```powershell
python -m unittest discover -s tests -v
```

The v0.3.4 release contains **76 passing tests**.

## Roadmap

The next milestone is **v0.3.5 — Weather**, which contributes live conditions and forecasts through Morning Star. See `docs/ROADMAP.md` for the complete plan, including Weather, Calendar,
Email, Docker, Diagnostics & Recovery, Voice, Knowledge, and Orion OS.
