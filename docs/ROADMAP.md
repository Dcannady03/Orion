# Orion Roadmap

Orion is a local-first personal intelligence operating system built through small,
tested, documented milestones.

## Phase 1 — Foundation ✅

- Core runtime and configuration
- User profile and identity
- Command router and Brain
- Ollama and OpenAI provider architecture
- Logging and automated test foundation

## Phase 2 — Intelligence ✅

- Workspace Manager
- Read-only Code Skill
- Session Memory
- Portable Project Context
- Service Registry
- Plugin System
- File Search
- Persistent Conversation Context
- Structural Knowledge Index

## Phase 3 — Automation 🚧

### Completed releases

- [x] **v0.3.0 — Ignition:** unified Action framework and audit history
- [x] **v0.3.1 — Safeguard:** approval policies and protected execution
- [x] **v0.3.2 — Discovery:** application discovery, aliases, matching, and safe launch
- [x] **v0.3.3 — Companion:** conversational approvals, trust, Developer Mode, and polished CLI
- [x] **v0.3.4 — Morning Star:** modular briefing service and provider-driven startup dashboard

### Planned releases
- [x] **v0.3.5 — Weather:** current conditions, forecasts, location lookup, and briefing provider
- [x] **v0.3.6 — Calendar:** agenda access, availability, next-event queries, and briefing provider
- [ ] **v0.3.7 — Email:** inbox access, sending, and briefing provider
- [ ] **v0.3.8 — Docker:** container discovery, control, and health checks
- [ ] **v0.3.9 — Git:** repository status and approval-based operations
- [ ] **v0.3.10 — Home Assistant:** entity discovery and device control
- [ ] Approval-based shell execution
- [ ] Routines and multi-step workflows

## v0.4.x — Centers & Pathfinder

- [x] **v0.4.4 — Horizon:** first-class Home Center and interface-neutral dashboard snapshots
- [x] **v0.4.5 — Horizon:** Tasks, Project, Activity, and System cards with fault isolation

- Dependency and service health graphs
- Root-cause-oriented diagnostic runbooks
- Safe, approval-based remediation
- Retry and end-to-end verification
- Transparent recovery history and rollback where possible

## Phase 4 — Voice

- Wake Word
- Speech Recognition
- Natural Voice
- Continuous Conversation
- Mobile Integration
- Smart Notifications

## Phase 5 — Knowledge Engine

- Learn PDFs, Word documents, Markdown, text, and source code
- Knowledge collections and semantic retrieval
- Citations and source references
- Duplicate detection and version tracking
- Summaries, comparisons, study guides, teaching, and quizzes

## Phase 6 — Orion OS

- AI Command Center GUI
- Web and mobile interfaces
- Multi-agent coordination
- Personalized workspaces
- Plugin marketplace exploration

## Current Release

**v0.5.7 — Forge: COMPLETE**

Forge executes one immutable, workspace-bound AI Team plan through the local Codex
CLI, persists strict implementation results, and stops at `Awaiting Review`.

**Active milestone:** Review Gate and Workflow Engine Phase 1. Cross-platform
diagnostics through `orion doctor` remains planned after the AI Team orchestration
foundation.

## v0.5.7 — Codex Bridge Phase 1 ✅

- [x] Explicit approval binds an immutable AI Team plan SHA-256 and workspace
- [x] Each approval is external, immutable, explicit by ID, and single-use
- [x] Local `codex exec` is confined to the active Git workspace root
- [x] Network, web search, extra roots, MCP, apps, hooks, plugins, and sub-agents are disabled
- [x] Git metadata and all branch, commit, push, merge, tag, and PR actions remain blocked
- [x] Strict implementation and test results persist under `~/.orion/codex/`
- [x] Successful execution stops at `Awaiting Review`
- [x] CLI exposes `team approve`, `team implement`, and `team run`
- [x] `execution status` distinguishes desktop apps, runnable CLIs, and Python readiness
- [x] One shared resolver launches the exact Codex executable reported by discovery
- [x] Missing engines are explained before an immutable approval is consumed

## v0.5.6 — Task Manager Phase 1 ✅

- [x] Strict first-class tasks under workspace-local `.orion/tasks.json`
- [x] Explicit proposed, ready, terminal, and future workflow states
- [x] Approval and cancellation remain direct user decisions
- [x] Dependencies reject missing references, duplicates, self-reference, and cycles
- [x] AI Team plans link as artifacts without automatic execution
- [x] Append-only task events support later workflow and streaming consumers
- [x] CLI exposes create, list, show, approve, cancel, events, and link-plan commands
- [x] Workspace rebinding keeps project task stores isolated

## Next — Review Gate and Workflow Engine Phase 1

- [ ] Start only from an explicitly approved Task Manager task
- [ ] Link the project task, approved AI Team plan, and Codex run identities
- [ ] Move through bounded Architect → Engineer → Awaiting Review stages
- [ ] Record bridge transitions in the existing task event stream
- [ ] Add a read-only Reviewer that consumes the persisted run and workspace diff
- [ ] Require another explicit decision before any repair pass
- [ ] Keep commits, pushes, merges, tags, and pull requests disabled

## v0.5.5 — Agent Registry Phase 1 ✅

- [x] Strict YAML definitions under external user data at `~/.orion/agents/`
- [x] Provider, model, instructions, declared tools, limits, and permissions
- [x] List, show, guided create, enable, disable, and bounded test commands
- [x] Built-in Agent files are seeded once without overwriting user changes
- [x] AI Team roles resolve to assigned configurable agents
- [x] Phase 1 grants no tools and performs no file, shell, or Git actions
- [ ] Phase 2: explicitly approved read-only tool dispatch

## v0.5.5 — AI Team Phase 1 ✅

- [x] Architect role produces a structured implementation plan
- [x] Engineer role reviews the Architect artifact and consolidates the final plan
- [x] Team tasks persist outside the application under `~/.orion/team/tasks/`
- [x] CLI exposes `team`, `team roles`, `team plan`, and `team status`
- [x] Token estimates and configurable cost estimates are displayed
- [x] Planning stops at `Awaiting Approval` with no implementation or PR actions
- [x] Phase 2: approval-bound `team implement <task-id> <approval-id>` with structured artifacts
- [ ] Phase 3: read-only reviewer and explicit repair approval

## v0.3.6.1 — Constellation ✅

- Multi-provider calendar support
