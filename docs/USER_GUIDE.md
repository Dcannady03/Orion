# Orion User Guide

**Version:** Living document

**Project:** Orion — Personal AI Operating System

**Documentation baseline:** v0.7.0 — Conductor plus unreleased Automatic Validation
and Documentation Review

Orion is a local-first personal intelligence operating system. It coordinates local
and cloud AI providers, project knowledge, communication services, applications, and
approval-gated execution behind one identity and command interface.

Orion's core principle is simple:

> You tell Orion your goal. Orion coordinates the right services and models while you
> retain control of credentials, workspaces, approvals, and final changes.

## Contents

0. [Quick Start (5 Minutes)](#0-quick-start-5-minutes)
1. [Installation and updates](#1-installation-and-updates)
2. [First Contact](#2-first-contact)
3. [Core concepts and data locations](#3-core-concepts-and-data-locations)
4. [Home and basic commands](#4-home-and-basic-commands)
5. [AI Center and Orion Vault](#5-ai-center-and-orion-vault)
6. [Workspaces, projects, and tasks](#6-workspaces-projects-and-tasks)
7. [Memory, conversations, search, and knowledge](#7-memory-conversations-search-and-knowledge)
8. [Connect Center](#8-connect-center)
9. [Email setup and use](#9-email-setup-and-use)
10. [Calendar, Discord, weather, and network](#10-calendar-discord-weather-and-network)
11. [Applications, actions, and plugins](#11-applications-actions-and-plugins)
12. [Agent Registry](#12-agent-registry)
13. [AI Team and Codex Bridge](#13-ai-team-and-codex-bridge)
14. [Real-world examples](#14-real-world-examples)
15. [Best practices](#15-best-practices)
16. [Safety model](#16-safety-model)
17. [Troubleshooting](#17-troubleshooting)
18. [Command reference](#18-command-reference)

## 0. Quick Start (5 Minutes)

This path gets a new installation from zero to a useful first Orion workflow. Email
connection takes longer if you still need to create an OAuth application.

### 1. Install and start Orion

```powershell
git clone https://github.com/Dcannady03/Orion
cd Orion
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m orion.main
```

### 2. Complete First Contact

Choose your identity, workspace, AI provider, and optional services. You can safely
skip anything and rerun setup later:

```powershell
.\.venv\Scripts\python.exe -m orion.main --first-contact
```

### 3. Connect an AI provider

If First Contact did not already connect one:

```text
ai connect openai
ai provider use openai
```

Ollama and Gemini are equally valid; OpenAI is only the shortest cloud example.

### 4. Connect Gmail (optional)

If you already have a Google Desktop OAuth JSON file with the Gmail API enabled:

```text
email configure gmail
email connect gmail
email status
```

See [Gmail setup](#gmail-setup) before this step if the OAuth client does not exist.

### 5. Ask your first question

```text
ask Give me a short overview of this workspace.
```

### 6. Create or select a workspace

```text
workspace C:\Projects\MyProject
project init
project status
```

Use one workspace per project whenever practical.

### 7. Run your first AI Team plan

```text
team roles
team plan "Add structured logging"
```

Read the plan and use `D` for details. Only explicit `Y` starts implementation. You may
choose `N` to finish the quick start without changing any files.

## 1. Installation and updates

### Windows

Requirements:

- Python 3.11 or newer
- Git for development and Git workspaces
- Ollama only if you want local AI
- Codex CLI only if you want AI Team implementation

```powershell
git clone https://github.com/Dcannady03/Orion
cd Orion
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m orion.main
```

### Linux

Ubuntu, Fedora, and Bazzite are supported through:

```bash
./scripts/install-linux.sh
```

macOS support is still under development.

### Updating Orion

Stable installations update from a package rather than requiring a Git pull:

```text
update check
update
update rollback
```

`update` shows the incoming version, asks for approval, creates an application backup,
and replaces application files. Mutable user data under `~/.orion` remains outside the
application and is not replaced. Restart Orion after an update or rollback.

Git workspaces also expose `git status`, `git log`, `git diff`, `git pull`, and
`git push`. Pull and push require explicit confirmation. Standard workspaces do not
need Git.

## 2. First Contact

First Contact is Orion's supported initial setup and reconfiguration workflow. Run it
again at any time with:

```powershell
python -m orion.main --first-contact
```

It collects or reviews:

- name, location, timezone, language, and intended use;
- active workspace;
- Ollama/local AI, OpenAI, Gemini, multiple providers, or Skip for now;
- Fast, Balanced, Coding, or Research routing when multiple providers are selected;
- Calendar, Discord, Gmail, and Microsoft Mail choices;
- detected execution engines and desktop applications.

Ollama setup checks reachability, discovers installed models, and lets you choose a
default. An unavailable local server does not block the rest of setup.

OpenAI and Gemini credentials are entered through hidden input, verified before saving,
and committed only through Orion Vault. A blank, invalid, cancelled, or unavailable
cloud connection does not replace a working credential or active provider.

First Contact can configure Gmail, Outlook/Microsoft 365, or both. Mail requests its
own explicit read-only consent even when Calendar already uses the same Google or
Microsoft application registration.

Rerunning First Contact is non-destructive. Existing identity, workspace, credentials,
providers, routing, and connected services remain the defaults and are preserved unless
you explicitly change them. The final summary shows AI, routing, workspace, services,
and execution engines. ChatGPT Desktop is identified as a desktop application and is
never presented as a CLI execution engine.

## 3. Core concepts and data locations

### Orion, providers, models, roles, and agents

- **Orion** is the orchestrator and the identity that communicates with you.
- A **provider** supplies AI, such as Ollama, OpenAI, or Google Gemini.
- A **model** is a specific AI exposed by a provider.
- A **role** is a job in an Orion workflow, such as Architect or Tester.
- An **agent** is a configurable worker that can perform a planning role.
- An **execution engine** is a local CLI adapter capable of bounded implementation.

Providers do not communicate directly with you or with one another. Orion creates the
prompts, controls handoffs, stores artifacts, applies approval rules, and presents the
results.

### External user data

Mutable private data lives outside the application directory:

```text
~/.orion/config.yaml              User configuration overrides
~/.orion/profile.yaml             User profile
~/.orion/vault/vault.yaml         AI and Discord secrets
~/.orion/tokens/                  Calendar and Email OAuth caches
~/.orion/agents/                  Custom agent definitions
~/.orion/team/tasks/              Persisted AI Team plans
~/.orion/codex/                   Approvals and implementation artifacts
~/.orion/cache/                   Bounded routing and service caches
~/.orion/logs/                    Service logs, including Network Watch
```

Project-local context belongs to the active workspace:

```text
<workspace>/.orion/tasks.json
<workspace>/.orion/task-events.jsonl
<workspace>/.orion/project.yaml and related project context
```

Never put API keys or OAuth tokens in normal configuration, project files, prompts,
task artifacts, or logs.

## 4. Home and basic commands

```text
home
help
status
briefing
profile
config
services
plugins
about
exit
```

- `home` refreshes the provider-neutral command center.
- `status` shows Orion and service health.
- `briefing` refreshes Morning Star information such as weather, calendar, unread mail,
  workspace, and AI status.
- `help` shows the current interactive command menu.
- `profile`, `config`, `services`, and `plugins` inspect the corresponding state.
- `exit` and `quit` shut down Orion.

The interactive console supports command history with Up/Down arrows and completion
with Tab when the optional terminal UI is available.

## 5. AI Center and Orion Vault

### List and select providers

```text
ai providers
ai status
ai provider models ollama
ai provider models openai
ai provider models gemini
ai provider use ollama
ai provider use openai
ai provider use gemini
```

`ai status` shows the active provider, active/default model, routing profile, session
overrides, and capabilities.

### Connect OpenAI

```text
ai connect openai
ai provider configure openai
ai test openai
ai provider models openai
ai provider use openai
ai disconnect openai
```

`ai connect openai` is an alias for the normal provider configuration flow. The key is
hidden, verified against the provider before persistence, and stored only in Vault.
Failed verification preserves the previous key and provider.

### Connect Gemini

```text
ai provider configure gemini
vault health
ai provider models gemini
ai provider use gemini
```

The same verify-before-save Vault transaction is used for Gemini.

### Ollama and local models

Start Ollama if it is not already running:

```text
ollama serve
```

Then discover and select a model:

```text
change ollama model
ollama model
ai models
ai use <model>
```

The model picker marks the current model and can save the new default.

### Routing profiles and health

```text
ai profiles
ai profile fast
ai profile balanced
ai profile coding
ai profile research
ai profile creative
ai profile lightweight
ai profile vision
ai route status
ai route on
ai route off
ai route explain last
ai stats
ai stats clear
ai health
ai benchmark
```

Routing profiles determine provider preference by task. Adaptive routing can demote an
unhealthy provider/model after enough evidence while retaining the selected profile's
policy. Orion keeps only the 100 most recent outcomes per provider/model and stores safe
error categories—not prompts, responses, or raw provider errors.

### Vault commands

```text
vault
vault add openai
vault add gemini
vault health
vault remove openai
vault remove gemini
```

Environment variables such as `OPENAI_API_KEY` and `GEMINI_API_KEY` take precedence
when present. The external Vault file is excluded from Git and uses owner-only
permissions where supported.

## 6. Workspaces, projects, and tasks

### Workspaces

```text
workspace
workspace C:\Projects\Example
files
files src
code tree
```

The active workspace is Orion's operational boundary. Standard workspaces require no
Git repository. Git workspaces additionally provide repository, branch, and commit
context, but Git never broadens the active folder Orion is allowed to use.

### Project context

```text
project init
project status
project info
project set <field> <value>
project note <text>
project checkpoint <summary>
project resume
project rules
project rule add <rule>
project rule remove <id>
```

Project context stores the goal, notes, checkpoints, and mandatory rules for one
workspace. `project resume` displays the latest checkpoint and rules so Orion can
continue from a durable handoff.

### Task Manager

Initialize project context before creating durable project tasks:

```text
project init
task create "Add Discord image generation"
task list
task show <task-id>
task approve <task-id>
task cancel <task-id>
task events <task-id>
task link-plan <task-id> <team-task-id>
```

A new task is Proposed. `task approve` moves it to Ready but does not call an AI,
execute code, or change files. Every task mutation appends a strict project event.
`task link-plan` links an AI Team plan as an artifact; it does not approve or implement
that plan. AI Team execution uses its own immutable approval.

## 7. Memory, conversations, search, and knowledge

### Session memory

```text
remember <key> <value>
recall <key>
memory
forget <key>
clear memory
```

### Conversation context

```text
conversation
conversation recent
conversation recent <count>
conversation search <text>
conversation clear
history
```

### Workspace search and structural knowledge

```text
search <text>
find <text>
index build
index status
index find <text>
index classes
index functions
index todos
index imports
```

Search is read-only and workspace-bound. It skips generated directories, binary files,
and oversized files. The knowledge index records current workspace structure so fresh
project facts take precedence over older conversational context.

## 8. Connect Center

```text
connect
connect status
connect health
connect add gmail
connect add microsoft
connect add discord
connect add discord bot
```

Connect Center unifies Gmail, Microsoft Mail, Calendar, Discord, and other services.
`connect health` performs explicit bounded health refreshes. Home uses cached status
where practical so startup does not unexpectedly query every external service.

Gmail and Microsoft Mail are supported now; they are not future placeholders. Both are
read-only in the current Email phase.

## 9. Email setup and use

Orion Email is one provider-neutral read-only service with Gmail and Microsoft Graph
adapters. You can connect either provider or both. When both are connected, commands
without a provider merge bounded normalized results.

You may configure Email during First Contact or later with the commands below.

### Gmail setup

1. Open Google Cloud Console and choose or create a project.
2. Enable the **Gmail API**.
3. Configure the OAuth consent screen for the Google accounts that may use Orion.
4. Create an OAuth client with application type **Desktop app**.
5. Download the client JSON file. If a Google Calendar desktop client is also allowed
   to request Gmail access, you may reuse that file; otherwise keep a separate client.
6. In Orion, run:

   ```text
   email configure gmail
   ```

   Enter the downloaded JSON path when prompted.
7. Authorize read-only access:

   ```text
   email connect gmail
   ```

   Review the displayed scope and explicitly approve opening Google's authorization
   flow.

Orion requests only:

```text
https://www.googleapis.com/auth/gmail.readonly
```

It does not request Gmail send, compose, or modify access. Gmail Mail consent and token
storage remain separate from Google Calendar authorization.

### Microsoft Outlook and Microsoft 365 setup

Installing Outlook does not authorize Orion. Orion connects through Microsoft Graph
delegated OAuth:

1. Open Microsoft Entra admin center and register an application.
2. Choose the account types needed for your installation. To support Outlook.com,
   Hotmail, Live, and work/school Microsoft 365, allow personal Microsoft accounts and
   organizational directories.
3. Configure the registration as a public desktop/native client. Allow the localhost
   redirect used by interactive MSAL sign-in and enable public client flows if the
   registration requires it.
4. Add delegated Microsoft Graph permissions `User.Read` and `Mail.Read`. MSAL also
   uses `offline_access`, `openid`, and `profile` for sign-in and refresh.
5. Copy the **Application (client) ID**. This identifier is normal configuration and is
   not a client secret.
6. In Orion, run:

   ```text
   email configure microsoft
   ```

   Enter the client ID. Tenant `common` is normally appropriate for personal plus
   work/school account selection.
7. Authorize read-only access:

   ```text
   email connect microsoft
   ```

Microsoft Calendar configuration can supply the same client ID and tenant, but Mail
still uses separate explicit `Mail.Read` consent and a separate token cache.

### Verify and manage connections

```text
email status
email providers
email accounts
email use gmail
email use microsoft
email disconnect gmail
email disconnect microsoft
```

Disconnecting removes only Orion's local Mail authorization for that provider. It does
not remove Calendar authorization or another Email provider. Remote authorization can
be revoked separately in the Google or Microsoft account security page.

### Read, search, and summarize mail

```text
email inbox
email inbox gmail
email inbox microsoft
email unread
email unread gmail
email unread microsoft
email search "deployment report"
email search "deployment report" gmail
email read <number|provider:message-id>
email thread <number|provider:message-id>
email summarize
email summarize gmail
email summarize microsoft
```

Inbox and search results show a numbered entry and a provider-qualified reference such
as `gmail:...` or `microsoft:...`. After listing mail, `email read 1` and
`email thread 1` may be used as shortcuts.

Orion converts HTML into safe plain text, limits rendered content, and shows attachment
names, content types, and sizes without downloading attachment bytes. Summaries inspect
only a bounded set of recent or unread messages and are formatted locally rather than
sending mailbox contents to the active AI provider.

### Current Email safety boundary

The current Email release cannot send, reply, forward, archive, trash, mark mail,
create provider drafts, or download attachments. Commands such as these stop safely:

```text
email draft
email send
email reply <message>
email forward <message>
email archive <message>
email trash <message>
email mark read <message>
email mark unread <message>
```

Drafting text never implies permission to send it. Mail write actions will remain
disabled until Orion can bind a persisted one-use approval to the exact provider,
account, recipients, subject, full body, attachments, and action.

OAuth tokens and MSAL caches are stored beneath `~/.orion/tokens/`, never in project
configuration, task artifacts, logs, or terminal output.

## 10. Calendar, Discord, weather, and network

### Calendar

Orion supports Google Calendar and Microsoft Calendar simultaneously:

```text
calendar providers
calendar configure google
calendar configure microsoft
calendar connect google
calendar connect microsoft
calendar enable google
calendar enable microsoft
calendar disable google
calendar disable microsoft
calendar
calendar today
calendar tomorrow
calendar next
```

For Google, create a Desktop OAuth client in Google Cloud and enable the Google Calendar
API, then use `calendar configure google` and `calendar connect google`. For Microsoft,
register a public-client Entra application with delegated `User.Read` and
`Calendars.Read`, then use `calendar configure microsoft` and
`calendar connect microsoft`.

Calendar and Mail use separate token caches and consent, even when they share the same
OAuth client registration.

### Discord

Orion supports an approval-gated outbound webhook and a restricted two-way bot.

Webhook setup and use:

```text
connect add discord
discord send <message>
```

`discord send` shows the exact message and asks before posting it.

Two-way bot setup:

```text
connect add discord bot
discord bot status
connect enable discord bot
connect disable discord bot
```

Create a bot in Discord Developer Portal, enable Message Content Intent, invite it only
to the intended server/channel, and provide the hidden bot token plus approved numeric
user IDs. Orion can also restrict allowed channel IDs and required human role IDs.
Direct messages and mentioned server messages are rejected unless the configured
identity and channel rules pass. Keep Discord-side channel permissions restrictive as
an additional layer.

The bot token lives in external Orion Vault and survives application updates.

### Weather

```text
weather
weather tomorrow
weather <location>
```

Weather uses Open-Meteo and requires no API key. Reports include conditions, high/low,
humidity, wind, and rain chance. A recent cache can keep Orion useful during temporary
network failures.

### Network Watch

```text
network
network status
network watch [seconds]
network report
network stop
network config
```

Network Watch checks the configured router, Cloudflare, and Google targets to
distinguish local gateway problems from likely Internet/ISP outages. Background logs
are JSON Lines under `~/.orion/logs/network/` and include outages, packet loss, average
latency, peak latency, and a plain-language diagnosis.

## 11. Applications, actions, and plugins

### Applications

```text
apps scan
apps list
apps find <name>
app alias <alias> = <application name>
open <application>
launch <application>
```

Orion discovers applications, supports user aliases, and routes launches through the
Action safety framework.

### Actions and trust

```text
action pending
action approve <id>
action deny <id>
action history
trust list
trust revoke <application>
settings
developer on
developer off
```

Actions may be allowed, require approval, or be denied. “Always allow” trust is narrowly
scoped and reviewable. Developer mode adds diagnostics but does not bypass policy.

### Plugins

```text
plugins
plugins info <name>
```

Plugins register services and commands without taking control of Orion's core safety
model. Plugin failures are isolated so one broken plugin does not prevent startup.
Search and Network Watch are built-in plugins.

## 12. Agent Registry

A role is a workflow job. An agent is a configurable worker that may perform a
planning role.

```text
agent list
agent show <name>
agent create
agent enable <name>
agent disable <name>
agent test <name>
```

Agent definitions are strict YAML files under `~/.orion/agents/`. They can declare a
provider, model, specialist instructions, future tools, limits, and permissions. Orion
does not replace existing definitions during updates.

In the current planning phase, declared tools and permissions are metadata only.
`agent test` makes one bounded structured-output provider call. It cannot read or write
files, run commands or tests, commit, push, or open a pull request. Disabled agents
cannot be assigned to active AI Team planning roles.

## 13. AI Team and Codex Bridge

### Workflow

```text
Goal
  ↓
Architect
  ↓
Engineering Reviewer
  ↓
Immutable approval
  ↓
Implementation Engine (Codex by default)
  ↓
Automatic Tester (bounded and read-only)
  ↓
Documentation Reviewer (bounded and read-only)
  ↓
Awaiting Review
  ↓
Keep changes or roll back
```

### Role assignments

The five persistent roles are Architect, Engineering Reviewer, Implementation Engine,
Tester, and Documentation Reviewer.

```text
team roles
team role show <role>
team role set <role> <provider:model|engine>
team role reset <role>
```

Defaults:

```text
architect            active-planning-model
engineer_reviewer    active-planning-model
implementation       codex
tester               codex
documentation        active-planning-model
```

`active-planning-model` follows the active provider and existing routing profile. Any
dynamic fallback is reported. Explicit provider/model assignments are validated and do
not silently change. Implementation requires an installed Orion adapter and stops
before approval consumption when unavailable. An unavailable Tester launches no check
and records `Validation Unavailable` after implementation. Assignments live in external
user configuration, never the project or Vault. The Documentation Reviewer uses the
same planning-model routing contract as Architect; requested/actual model, fallback,
usage, cost, and duration are recorded with each attempt.

Examples:

```text
team role set architect openai:<available-model>
team role set engineer_reviewer gemini:<available-model>
team role set implementation codex
team role reset architect
```

### Interactive planning and approval

```text
team plan "Create a README"
```

After showing the exact plan, Orion asks:

```text
Approve this exact plan?
[Y] Yes  [N] No  [D] Details
>
```

- Only explicit `Y` or `Yes` approves.
- `N`, empty input, or Ctrl+C performs no implementation.
- `D` shows the plan, risks, hash, workspace, engine, sandbox, and permissions, then
  returns to the prompt.

Approval is immutable, one-use, bound to the exact plan SHA-256, and bound to the exact
active workspace and execution engine.

### Manual workflow

Use manual mode for scripting, recovery, or noninteractive callers:

```text
team plan --manual "Create a README"
team status <team-task-id>
team approve <team-task-id>
team implement <team-task-id> <approval-id>
team run <run-id>
team test <run-id>
team test last
team docs <run-id>
team docs last
team docs show <run-id>
team rollback <run-id>
```

`team run` displays separate implementation, Automatic Validation, Documentation
Review, and overall human-review sections plus saved artifacts. `team test <run-id>`
validates the completed implementation again and then runs Documentation Review;
`team test last` selects the newest eligible run in the active workspace. `team docs`
reruns only Documentation Review, `team docs last` selects the newest eligible run, and
`team docs show` displays the latest concise findings. None reruns implementation or
consumes another approval.

There is no `team accept` command: if you approve the result, leave the reviewed
changes in place and continue your normal development workflow. Codex Bridge itself
never commits, pushes, merges, tags, or opens a pull request.

`team rollback` asks for confirmation and restores only the recorded run when affected
files have not received conflicting later changes. It does not use destructive Git
reset or checkout.

### Automatic validation

After successful implementation, Orion resolves the persisted Tester role and builds a
deterministic plan from the actual created, modified, and deleted files. It selects only
relevant checks:

- changed Python receives compile validation and matching targeted tests;
- broad shared Python changes, or changes without a target match, receive full test
  discovery;
- changed JSON, YAML, and TOML are parsed locally;
- changed Markdown receives heading, fence, and practical local-link checks;
- expected created/deleted files, snapshot integrity, and protected `.git`, `.codex`,
  and `.agents` metadata are checked.

The Tester is read-only toward implementation files. Its allowlisted Python commands
run with a temporary home/cache, no inherited credentials, blocked network access, no
nested commands, and bounded time/output. Orion checks the workspace again afterward;
an attempted write is a failed safety check. Temporary validation data is removed.
The Tester cannot repair failures, update documentation, change plans or roles, consume
approvals, access Vault/OAuth data, or perform Git operations.

Review statuses are:

- `Awaiting Review — Validation Passed` — every selected check passed;
- `Awaiting Review — Validation Warnings` — review non-blocking findings;
- `Awaiting Review — Validation Failed` — one or more checks failed;
- `Validation Unavailable` — no configured Tester engine was ready;
- `Validation Error` — the bounded validation process could not complete safely;
- `Awaiting Review — Validation Not Run` — a compatible older run has no attempt.

Validation never accepts or rolls back changes automatically, including for warnings,
failures, unavailable engines, and errors. The user always chooses whether to keep the
implementation or run `team rollback <run-id>`.

### Automatic Documentation Review

After every validation outcome—Passed, Warnings, Failed, Unavailable, or Error—Orion
runs the configured Documentation Reviewer before human review. A deterministic
classifier first records whether documentation is required and why. Commands,
configuration, providers/services/plugins, setup, safety/approval/credential behavior,
public contracts, artifact formats, troubleshooting, release behavior, architecture,
visible output, platforms, and features normally require coverage. Test-only and
explicit internal changes with no observable behavior may be `Not Required`.

Orion selects applicable documents rather than demanding every guide for every change.
It audits new commands against completion, interactive help, the User Guide, feature
guides, and changelog; configuration keys against defaults and Configuration; and
architecture/safety changes against their subsystem documents. Markdown structure and
local-link checks are reused from Automatic Validation.

When documentation is required, the routed planning model receives only a bounded,
sanitized approved plan, implementation summary, changed-file metadata/safe summaries,
validation summary, known command/configuration changes, project rules, headings, and
applicable documentation excerpts. It never receives raw diffs, source bodies,
credentials, environment variables, Vault/OAuth/mail data, or unrelated workspace
content. The role has no file, shell, Codex, Tester, Git, approval, role, repair,
acceptance, or rollback tools.

Documentation statuses are:

- `Documentation Passed` — required coverage is complete and accurate;
- `Documentation Warnings` — non-blocking or review-worthy findings remain;
- `Documentation Failed` — material command, setup, configuration, safety,
  architecture, or user/developer contract coverage is missing or inaccurate;
- `Documentation Not Required` — no meaningful documentation contract changed;
- `Documentation Unavailable` — the configured provider/model cannot run;
- `Documentation Error` — the bounded review stopped safely; and
- `Documentation Not Run` — a compatible older run has no attempt.

Findings include severity, category, document/section, implementation evidence,
recommended correction, confidence, and whether the issue blocks Passed. They never
edit or repair files automatically. Reruns append immutable attempts under external
user data and preserve every prior attempt.

Representative output:

```text
AI Team Run
Status: Awaiting Review

Implementation
Status: Complete

Files Changed
  Created:  2
  Modified: 4
  Deleted:  0

Automatic Validation
PASS  Python compile
PASS  Targeted Python tests: 18 test(s)
WARN  Markdown local links: docs/guide.md -> missing.md
SKIP  Full Python test suite: Targeted tests were sufficient

Validation Summary
  Checks:   4
  Passed:   2
  Warnings: 1
  Failed:   0
  Skipped:  1

Documentation Review
Status: Documentation Warnings
Documents inspected: 8
Warnings: 1
Errors: 0
WARN  docs/USER_GUIDE.md
      New `team docs` syntax is missing from one example.

Overall Review Status
Awaiting Review — Validation Warnings — Documentation Warnings
```

### Execution-engine diagnostics

```text
execution status
```

The report distinguishes Codex CLI, Codex Desktop, ChatGPT Desktop, Claude Code,
Gemini CLI, and Python. A desktop application is not automatically a CLI engine.
ChatGPT Desktop always reports no CLI execution support. Claude Code and Gemini CLI may
be detected, but the current implementation adapter is Codex CLI.

Codex execution remains noninteractive, network-disabled, and confined to the exact
approved workspace with the workspace-write sandbox. If the engine is unavailable,
Orion stops before consuming the approval or changing the workspace.

## 14. Real-world examples

The output below is representative. IDs, hashes, models, costs, and file names will
differ on each installation.

### Plan and implement a small code change

```text
Orion> workspace C:\Projects\Example
Orion> team plan "Add structured application logging"

AI Team Plan
--------------------------------------------------------------
Architect
  - Add a provider-neutral logging configuration
  - Update startup wiring
  - Add focused tests and documentation

Engineering Reviewer
  - Keep secrets and message bodies out of logs
  - Add bounded rotation and failure handling

Approve this exact plan?
[Y] Yes  [N] No  [D] Details
> y

Starting one approval-bound local Codex execution...
Status: Awaiting Review
Automatic Validation: Passed
```

Review the result:

```text
team run <run-id>
```

If you need to repeat validation after a local dependency is restored, without running
implementation again:

```text
team test <run-id>
```

If the result is correct, leave the reviewed files in place and continue your normal
Git workflow. If the run should be undone and no conflicting later edits exist:

```text
team rollback <run-id>
```

### Track work without starting implementation

```text
project init
task create "Document the deployment process"
task list
task approve <task-id>
team plan --manual "Document the deployment process"
task link-plan <task-id> <team-task-id>
```

Task approval and plan linking organize the work but do not run Codex. Implementation
still requires the separate immutable AI Team approval.

### Review morning information

```text
briefing
calendar today
email unread
email summarize
weather
```

This combines bounded service data while keeping Email summaries local and Mail access
read-only.

### Use provider routing resilience

```text
ai providers
ai profile balanced
ai route status
ask Compare the risks in the current implementation plan.
ai route explain last
```

The last command explains the requested and actual provider/model and any health-based
fallback without exposing prompt content or raw provider errors.

## 15. Best practices

- **Keep one workspace per project.** Approval and execution boundaries stay easier to
  understand, inspect, and recover.
- **Use Git for software projects.** Git is not required, but it adds independent
  history and makes reviewed Orion changes easier to compare.
- **Review every AI Team plan before approval.** Use `D` to inspect the plan hash,
  risks, workspace, engine, sandbox, and expected permissions.
- **Review every implementation result.** Run `team run <run-id>` and inspect the actual
  diff plus automatic validation before committing anything. PASS is evidence, not
  automatic acceptance; FAIL is not automatic rollback.
- **Create checkpoints before major changes.** `project checkpoint <summary>` produces
  a durable handoff for `project resume`.
- **Connect multiple AI providers when useful.** Existing routing profiles can select
  an appropriate provider and provide planning resilience when a dynamic default is
  unavailable.
- **Keep credentials out of projects and version control.** Use Vault for AI and bot
  secrets and the external token stores for OAuth.
- **Grant the least external permission possible.** Mail is read-only; Discord users
  and channels should be allowlisted; OAuth registrations should contain only required
  delegated scopes.
- **Treat task approval and execution approval as different decisions.** A project task
  can be Ready without authorizing code changes.
- **Use rollback promptly and cautiously.** Rollback is conflict-aware; later manual
  edits may intentionally prevent it from overwriting newer work.
- **Keep documentation in the Definition of Done.** New features are incomplete until
  help, this guide, feature docs, and the changelog agree with the implementation.

## 16. Safety model

### Explicit approval

Sensitive actions require a visible decision. Natural-language agreement elsewhere in
Orion never counts as an AI Team plan approval.

### Workspace isolation

Code execution is bound to the exact active workspace. Parent folders, the user
profile, temporary locations, unrelated projects, `.git`, `.codex`, and `.agents` are
not granted as extra writable roots.

### Credential isolation

API keys live in Vault. OAuth tokens live under `~/.orion/tokens/`. Credentials are not
placed in prompts, configuration, task artifacts, implementation artifacts, logs, or
test output.

### Immutable and one-use approvals

AI Team implementation approval binds:

- task and exact plan hash;
- active workspace and workspace capabilities;
- execution engine and intended operation;
- one implementation attempt.

Changing the plan or workspace invalidates the approval. Reuse is rejected.

### Review and rollback

Orion snapshots workspace state, records actual created/modified/deleted files, redacts
sensitive diff content, and stops at Awaiting Review. Rollback refuses to overwrite
conflicting later work.

## 17. Troubleshooting

### OpenAI or Gemini will not connect

```text
ai providers
vault health
ai provider configure <openai|gemini>
```

Check Internet access and the candidate credential. Failed verification preserves the
previous working key and active provider.

### Ollama is offline or has no models

```text
ollama serve
change ollama model
ai status
```

### Email is not connected

```text
email status
email providers
email configure <gmail|microsoft>
email connect <gmail|microsoft>
```

For Gmail, verify the Gmail API, consent screen, Desktop OAuth JSON, and
`gmail.readonly` grant. For Microsoft, verify the public-client registration, client
ID/tenant, and delegated `User.Read` plus `Mail.Read` permissions. Installing Outlook
alone is not authorization.

### Calendar is empty

```text
calendar providers
calendar connect <google|microsoft>
calendar
```

Mail and Calendar authorizations are separate; connecting one does not authorize the
other.

### Discord bot is not configured after an update

```text
discord bot status
connect add discord bot
```

Current Orion stores the bot token in external Vault and settings in external user
configuration. Updates should not replace them. If status is missing, reconfigure once
and confirm `~/.orion` is available to the same Windows user.

### Codex is installed but implementation cannot start

```text
execution status
team roles
```

Confirm Codex CLI—not only Codex Desktop or ChatGPT Desktop—is Ready. Orion supports
Windows npm wrappers including extensionless, `.cmd`, `.exe`, and `.ps1` forms.

### Automatic validation is unavailable or failed

```text
team roles
team role show tester
execution status
team run <run-id>
```

`Validation Unavailable` means the configured Tester assignment or execution engine is
not ready. Restore that engine, then run `team test <run-id>` or `team test last`.
`Validation Failed` means a selected check found a real issue; review the named check
and workspace diff. `Validation Error` means a timeout, bounded-output limit, safety
guard, or other sanitized validator failure stopped the attempt. None of these states
automatically changes or rolls back implementation files.

### Documentation Review is unavailable, failed, or found issues

```text
team roles
team role show documentation
ai providers
team docs show <run-id>
team docs <run-id>
```

`Documentation Unavailable` means the assigned provider/model is disabled,
unconfigured, or otherwise unavailable. Restore that planning provider and rerun the
review. `Documentation Failed` means material coverage is missing or inaccurate;
`Documentation Warnings` means a human should inspect non-blocking findings; and
`Documentation Error` means bounded classification, artifacts, provider output, or
workspace integrity stopped safely. None edits files, reruns implementation/testing,
consumes approval, accepts work, or rolls back changes.

### Workspace is read-only or mismatched

```text
workspace
execution status
```

Select the intended active folder and confirm the current user can write to it. A plan
approved for another workspace must receive a new approval.

### A plugin or service is unavailable

```text
plugins
services
connect health
developer on
```

Developer mode can expose safe diagnostics. It does not bypass approval or policy.

## 18. Command reference

### General

| Command | Purpose |
| --- | --- |
| `home` | Show the Orion Home command center |
| `help` | Show the current command menu |
| `status` | Show Orion system health |
| `briefing` | Refresh the Morning Star briefing |
| `profile` | Show the user profile |
| `config` | Show normal configuration |
| `services` | Show registered services |
| `plugins` | Show loaded plugins |
| `about` | Show Orion version information |
| `exit` / `quit` | Shut down Orion |

### AI and Vault

| Command | Purpose |
| --- | --- |
| `ai providers` | List Ollama, OpenAI, and Gemini status |
| `ai status` | Show active provider/model and capabilities |
| `ai provider configure <provider>` | Verify and securely connect a cloud provider |
| `ai provider use <provider>` | Select the active provider |
| `ai provider models <provider>` | Discover provider models |
| `ai profile <name>` | Activate a routing/behavior profile |
| `ai stats` / `ai health` | Inspect adaptive routing evidence |
| `ai route explain last` | Explain the last provider choice/fallback |
| `change ollama model` | Select an installed Ollama model |
| `vault` / `vault health` | Inspect configured secret providers safely |

### Project and knowledge

| Command | Purpose |
| --- | --- |
| `workspace [path]` | View or change the active workspace |
| `files [path]` | List workspace files |
| `project init` | Initialize workspace project context |
| `project status` / `project resume` | Inspect or resume project context |
| `task create "<goal>"` | Create a Proposed project task |
| `task list` / `task show <id>` | Inspect project tasks |
| `task approve <id>` / `task cancel <id>` | Make an explicit task decision |
| `task events <id>` | Show append-only task events |
| `task link-plan <id> <plan>` | Link an AI Team plan without executing it |
| `search <text>` | Search the active workspace |
| `index build` / `index status` | Build or inspect structural knowledge |

### Email and Connect

| Command | Purpose |
| --- | --- |
| `connect` / `connect health` | Inspect connected services |
| `email configure <provider>` | Save non-secret OAuth client settings |
| `email connect <provider>` | Authorize read-only Mail access |
| `email disconnect <provider>` | Remove only local Mail authorization |
| `email accounts` | Show connected account identities |
| `email inbox [provider]` | List a bounded recent inbox |
| `email unread [provider]` | Count and list bounded unread mail |
| `email search "<query>" [provider]` | Search connected mail |
| `email read <number|provider:id>` | Read safe plain text and attachment metadata |
| `email thread <number|provider:id>` | Read a bounded conversation |
| `email summarize [provider]` | Locally summarize bounded unread mail |

### AI Team

| Command | Purpose |
| --- | --- |
| `team roles` | Show all model and engine assignments |
| `team role show <role>` | Inspect one assignment |
| `team role set <role> <assignment>` | Persist a provider:model or engine |
| `team role reset <role>` | Restore the default assignment |
| `team plan "<goal>"` | Plan and offer interactive Y/N/D approval |
| `team plan --manual "<goal>"` | Plan without prompting |
| `team status <task-id>` | Reopen a persisted plan |
| `team approve <task-id>` | Create an immutable manual approval |
| `team implement <task-id> <approval-id>` | Run one bounded implementation |
| `team run <run-id>` | Show implementation, validation, Documentation Review, and overall status |
| `team test <run-id>` | Add validation then Documentation Review without implementation |
| `team test last` | Validate and review the newest eligible run in the active workspace |
| `team docs <run-id>` | Add one immutable Documentation Review without implementation/testing |
| `team docs last` | Review the newest eligible run in the active workspace |
| `team docs show <run-id>` | Show the latest concise documentation findings |
| `team rollback <run-id>` | Safely restore one reviewed run |
| `execution status` | Diagnose execution engines and desktop apps |

### System and utilities

| Command | Purpose |
| --- | --- |
| `calendar [today|tomorrow|next]` | Show merged calendars |
| `weather [location]` | Show current conditions and forecast |
| `network status` / `network watch` | Check or monitor connectivity |
| `apps scan` / `apps find <name>` | Discover applications |
| `open <application>` | Launch through Orion's safety framework |
| `action pending` / `action history` | Review action state |
| `trust list` / `trust revoke <app>` | Review or revoke application trust |
| `update check` / `update` | Check or apply an Orion package update |
| `update rollback` | Restore the previous application backup |

## Keeping this guide current

This is Orion's living user guide. Every shipped feature, provider, setup path, safety
boundary, and user-facing command should be added here when it changes. Detailed
implementation contracts remain in the focused files under `docs/`, including
`EMAIL.md`, `AI_TEAM.md`, `CODEX_BRIDGE.md`, `EXECUTION_ENGINES.md`,
`AGENT_REGISTRY.md`, `TASK_MANAGER.md`, and `CONFIGURATION.md`.
