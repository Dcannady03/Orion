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

**v0.5.4.1 — Sentinel: COMPLETE**

This Sentinel maintenance release makes Discord configuration update-safe by moving
the live vault into external user data and safely recovering missing tokens and access
settings from update backups without replacing newer current values.

**Active milestone:** AI Team Phase 1 multi-role planning. Cross-platform diagnostics
through `orion doctor` remains planned after the team-planning foundation.

## In Development — AI Team Phase 1

- [x] Architect role produces a structured implementation plan
- [x] Engineer role reviews the Architect artifact and consolidates the final plan
- [x] Team tasks persist outside the application under `~/.orion/team/tasks/`
- [x] CLI exposes `team`, `team roles`, `team plan`, and `team status`
- [x] Token estimates and configurable cost estimates are displayed
- [x] Planning stops at `Awaiting Approval` with no implementation or PR actions
- [ ] Phase 2: `team implement <approved-task-id>` with patch and review safeguards

## v0.3.6.1 — Constellation ✅

- Multi-provider calendar support
