# Orion Agent System

Orion agents are reusable, provider-neutral worker profiles. An agent describes a job,
specialty, communication personality, instructions, routing preferences, eligible
capabilities, and conservative permission policies. Agents extend Orion's existing AI
Team, routing, workspace, approval, Vault, and execution-engine systems; they do not
replace those systems and are not built around Codex.

## Scope and storage

Orion supports two durable scopes:

- **Permanent** agents are available to every workspace and live under
  `~/.orion/agents/`.
- **Workspace** agents are visible only in the active workspace and live under
  `<workspace>/.orion/agents/`.

“Workspace” describes local scope, not a short lifetime. Workspace agents survive
Orion restarts until deleted or promoted. The active folder does not need to be a Git
repository. Agent writes use validated IDs, workspace containment checks, symlink
checks, temporary files, `fsync`, and atomic replacement.

Application-owned starter templates live under `orion/agents/templates/`. Creating
from a template copies a user-owned definition to one of the storage locations above;
templates are never immutable live agents.

## Commands

```text
agent list
agent list --scope permanent
agent list --scope workspace
agent show <agent>
agent templates
agent create
agent create --from-template software-engineer
agent create --from-template website-designer --scope workspace
agent create --name "Release Reviewer" --job "Code Reviewer" --scope permanent
agent edit <agent>
agent delete <agent>
agent enable <agent>
agent disable <agent>
agent validate <agent>
agent promote <workspace-agent>
agent copy <agent> <new-name>
agent test <agent>
```

`agent create` without options starts a guided flow. The option form calls the same
management service and is suitable for future GUI, Discord, API, or automation
adapters. `agent delete` asks for confirmation unless `--yes` is supplied.

The built-in templates are Planner, Software Engineer, Reviewer, Tester,
Documentation Writer, Security Reviewer, Researcher, Marketing Specialist, Sales
Specialist, and Website Designer.

## Version 1 YAML schema

Unknown top-level and nested fields are tolerated so a newer definition does not crash
an older Orion. Orion preserves unknown top-level fields when it rewrites a definition.
Known security-sensitive fields remain validated. Legacy Phase 1 definitions are read
and migrated in memory.

```yaml
schema_version: 1
id: software-engineer
name: Software Engineer
description: Implements well-tested, maintainable software.
scope: permanent
enabled: true

role:
  job: Software Engineer
  specialty: Python application development
  personality: Careful, practical, direct, and collaborative.
  instructions: |
    Study the existing architecture before changing code.
    Prefer small, maintainable changes.
    Run relevant tests and explain important tradeoffs.

execution:
  provider: auto
  model: auto
  routing_profile: coding
  temperature: 0.2
  generation: {}

capabilities:
  - read_workspace
  - write_workspace
  - run_tests

permissions:
  network: denied
  shell: approval
  write_files: approval
  git_write: denied
  calendar: denied
  email: denied
  image_generation: denied

workspace_access: read_write
metadata:
  created_at: '2026-07-25T00:00:00+00:00'
  updated_at: '2026-07-25T00:00:00+00:00'
```

IDs are stable 2–64 character lowercase slugs. Timestamps must include a timezone.
Provider IDs are not closed to the three current chat providers, which keeps the
schema ready for future registered providers. Model names and generation settings
cannot contain credential-shaped values or secret-bearing fields.

Recognized capabilities are:

```text
read_workspace   write_workspace   run_tests       run_commands
use_network      web_research      inspect_git     write_git
access_calendar  access_email      generate_images
```

Permission values are `denied`, `approval`, or `allowed`. “Allowed” means the profile
is eligible to request the operation; it never disables Orion's approval engine.

## Selecting agents for a job

Pass a comma-separated ordered list:

```text
team run "Build a product landing page" --agents planner,website-designer,marketing-specialist,reviewer
```

If `--agents` is omitted for a quoted job goal, Orion presents an interactive ordered
selection. A draft can also be assembled incrementally:

```text
team create "Build a product landing page"
team agents add planner
team agents add website-designer
team agents add reviewer
team run
```

The draft is workspace-local at `<workspace>/.orion/team-draft.yaml`. Running it
clears the draft only after the team task is successfully created.

The selected order is preserved in `selected_agents`. Each task also contains one
`agent_snapshots` entry per selected agent, in the same order. A snapshot includes the
effective role text, permissions, routing request, actual provider and model, assigned
responsibility, and source definition timestamp. It contains no credentials. Later
agent edits do not change historical tasks.

## Prompt and handoff behavior

For every selected agent Orion builds a clearly delimited instruction set containing:

1. Orion safety and approval rules
2. The job goal and confined workspace
3. The agent's job and specialty
4. Its personality and custom instructions
5. Its assigned responsibility
6. Capability eligibility and permission restrictions
7. Structured outputs from earlier selected agents

Agent text and earlier model output are marked as untrusted data. Orion's rules are
placed first and explicitly override any conflicting personality or custom
instructions. The current selected-agent phase is planning-only: providers receive no
tool dispatcher, so file, shell, network, Git, calendar, email, and image operations
cannot be performed during the call.

The final contribution becomes the ordered plan awaiting Orion's existing immutable
approval. Implementation, Automatic Tester, Documentation Review, rollback, and human
review continue through the existing AI Team and execution-engine workflow.

## Provider and model resolution

For each selected agent Orion resolves:

1. Explicit `team run --provider` / `--model` overrides
2. Explicit agent provider and model preferences
3. The agent routing profile through `AIRoutingService`
4. The configured active/default provider

Current registered chat providers are OpenAI, Gemini, and Ollama. Provider creation
continues through `AIProviderFactory`; routing readiness and model availability come
from `ProviderManager`. Python and Codex remain execution engines for approved
implementation rather than hard-coded agent providers.

If a requested provider or model is unavailable, fallback occurs only when current
routing policy permits it. Artifacts and snapshots record both the request and the
actual provider/model. When routing is disabled or no safe candidate exists, the job
fails before a provider call.

## Safety and approvals

- Definitions never store API keys, OAuth tokens, or raw secrets. Provider credentials
  continue to come from Vault.
- A capability is eligibility, not authority. Agent profiles cannot approve their own
  actions.
- Workspace definitions cannot traverse outside the active workspace or use a symlink
  to escape it.
- A workspace agent cannot silently override a permanent agent. Duplicate IDs across
  scopes produce a conflict that must be resolved.
- Disabled agents cannot be selected.
- Selected-agent provider calls are bounded, structured-output planning calls with no
  tools and no mutations.
- Existing Git-write, network, workspace, secret-redaction, approval, and execution
  policies remain authoritative.

## Example workflows

Software:

```text
Planner -> Software Engineer -> Tester -> Reviewer -> Documentation Writer
```

Marketing:

```text
Researcher -> Marketing Specialist -> Sales Specialist -> Reviewer
```

Website:

```text
Planner -> Website Designer -> Software Engineer -> Tester -> Reviewer
```

Only agents explicitly selected for the job participate.

## Troubleshooting

- **Agent not found:** run `agent list` and check the current workspace. Workspace
  agents do not appear elsewhere.
- **Ambiguous/conflicting ID:** rename, copy, promote, or delete one definition. Orion
  will not guess between scopes.
- **Invalid definition:** run `agent validate <agent>`. The error names the invalid
  definition and field without rewriting it.
- **Provider/model unavailable:** run `ai providers`, inspect the agent with
  `agent show`, and check the selected routing profile. Orion reports actual fallback.
- **Agent disabled:** use `agent enable <agent>` before selection.
- **Workspace storage escape:** replace a symlinked `.orion` metadata path with a real
  directory inside the workspace.
- **No team draft:** run `team create "<goal>"` before `team agents add` or a bare
  `team run`.

## Current boundary

The first production version makes reusable agents first-class in planning, handoffs,
routing, manifests, and historical snapshots. It deliberately does not let arbitrary
agent text execute tools directly. Approved implementation still uses Orion's existing
execution-engine workflow, which currently has a production Codex adapter and detects
other engines, including Python, for future adapters.
