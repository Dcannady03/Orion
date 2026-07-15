# Orion

Orion is a local-first personal intelligence operating system. It combines AI,
memory, project knowledge, plugins, discovery, and approval-based actions behind a
cohesive command-line companion.

## Current release

**v0.3.6.3 — Constellation: First Contact**

Weather gives Orion live current conditions and forecasts through Open-Meteo, with no
API key required. It also plugs into Morning Star through the provider architecture:

```text
Today's Briefing
--------------------------------------------------
  [WX] Weather: 75°F and clear sky; high 96°F, low 64°F
  [OK] Workspace: Orion is ready
  [OK] AI: ollama:qwen3.6:35b is connected
```

Weather failures are isolated, so a network outage never prevents Orion from starting.

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
weather                      Show live conditions and today's forecast
weather tomorrow             Show tomorrow's forecast
weather <location>           Check another location
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
- `orion/services` — workspace, discovery, Companion, Morning Star, and Weather services
- `orion/conversation` — persistent conversation context
- `orion/knowledge` — structural workspace index
- `orion/plugins` — plugin contracts and lifecycle
- `tests` — automated regression suite
- `docs` — architecture, constitution, roadmap, changelog, and release notes

## Testing

```powershell
python -m unittest discover -s tests -v
```

The v0.3.6.3 release contains **106 passing tests**.

## Roadmap

The next milestone is **v0.3.6 — Calendar**, which will contribute a daily agenda through Morning Star. See `docs/ROADMAP.md` for the complete plan, including Weather, Calendar,
Email, Docker, Diagnostics & Recovery, Voice, Knowledge, and Orion OS.

## v0.3.6.2 — Constellation Polish

- Added `calendar enable <provider>` and `calendar disable <provider>`.
- Added guided `calendar configure google|microsoft`.
- Calendar provider settings now persist to `config/default.yaml`.
- Both Google and Microsoft providers remain registered so they can be enabled without restarting or editing YAML.


## First Contact

A clean installation now begins with a guided, conversational setup instead of a
configuration error. Orion collects the user's identity, location, timezone, default
workspace, local AI settings, and initial service choices, then creates the normal
`config/default.yaml` and `config/profile.yaml` files atomically.

Run the experience manually at any time:

```powershell
python -m orion.main --first-contact
```

When rerun, Orion preserves the previous YAML files with a
`.before-first-contact` suffix before writing the new profile.

### Change the active Ollama model

Orion can scan the local Ollama server and switch models without restarting:

```text
Orion> change ollama model
```

The numbered picker marks the current model, saves the new selection to `config/default.yaml`, and activates it immediately. `ollama model` is available as a shorter alias.


## AI Control Center

Manage local Ollama models without editing YAML or restarting Orion:

```text
Orion> ai status
Orion> ai models
Orion> switch to qwen3.5:9b
Orion> use the fastest model
Orion> ai profile coding
Orion> ai benchmark
```

AI profiles and model choices persist in `config/default.yaml`. The quick benchmark is opt-in because loading several models can use substantial RAM and VRAM.

## Orion Home

Orion opens to a provider-neutral personal command center. Refresh it at any time:

```text
Orion> home
```

Home cards are contributed independently by Weather, Calendar, Workspace, AI, Applications, and future services.


### Current vs. default AI model

Use `ai use <model>` or `change ollama model` to switch immediately. Orion will ask whether the selection should be saved as the default for future launches. `ai status` shows both the current and default model.

## Reliability and Project Grounding

Orion preloads a newly selected Ollama model before returning to the prompt, reducing first-request failures after a model switch. `project status` reports live workspace metrics, and AI context automatically rebuilds a stale index while treating current workspace facts as authoritative over older conversation history.

```text
project status
ai use qwen3.5:9b
```

## AI Federation (Polaris)

Orion can now use Ollama, OpenAI, or Google Gemini through one provider-neutral Brain.

```text
Orion> ai providers
Orion> ai provider configure openai
Orion> ai provider configure gemini
Orion> ai provider use gemini
Orion> ai provider models gemini
```

API keys are not stored in `config/default.yaml`. Orion uses `OPENAI_API_KEY` or `GEMINI_API_KEY` when available, or a separately created `.orion/secrets.yaml` when the user explicitly chooses interactive configuration.

## Orion Vault

Polaris stores cloud-provider credentials outside normal configuration through a centralized Vault.

```text
vault
vault add gemini
vault add openai
vault health
vault remove gemini
```

The local vault is stored at `.orion/vault.yaml`, excluded from Git, and written with owner-only permissions where supported. Environment variables (`GEMINI_API_KEY` and `OPENAI_API_KEY`) still take precedence. Existing `.orion/secrets.yaml` credentials are migrated automatically on first launch. Native Windows Credential Manager, macOS Keychain, and Linux Secret Service backends are planned behind the same Vault interface.
