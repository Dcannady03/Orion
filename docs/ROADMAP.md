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
- [x] **v0.6.0 — Email Phase A:** provider-neutral Gmail and Microsoft read-only mail
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

**v0.7.0 — Conductor: COMPLETE**

Conductor adds persistent AI Team role assignments, provider/model and execution-engine
validation, routing-policy planning fallbacks, role metadata in immutable artifacts,
and a complete living user/documentation process. Gatekeeper's approval, workspace,
execution, review, and rollback protections remain intact.

**Active milestone:** AI Team Automatic Validation (unreleased). Email Phase B and
cross-platform diagnostics through `orion doctor` remain planned after this bounded
Tester milestone.

## Unreleased — AI Team Automatic Validation

- [x] Successful implementation automatically enters a separate Tester stage
- [x] Deterministic checks cover Python, JSON, YAML, TOML, Markdown, and file integrity
- [x] Targeted Python discovery avoids full-suite execution when focused tests suffice
- [x] Tester commands use isolated temporary state, no network, bounded output, and no Git
- [x] `team test <run-id>` and `team test last` preserve immutable attempt history
- [x] Validation Passed, Warnings, Failed, Unavailable, Error, and Not Run are distinct
- [x] Human Keep Changes or Roll Back remains the only final decision
- [x] Existing approval, workspace, snapshot, rollback, Vault, and run-artifact boundaries remain intact

## v0.7.0 — Conductor Role-Based AI Team Routing ✅

- [x] Five persistent assignments cover Architect, Engineering Reviewer,
      Implementation Engine, Tester, and Documentation Reviewer
- [x] `team roles` and role show/set/reset commands report assignment, availability,
      capability, fallback, and source
- [x] Planning assignments validate configured providers, available models, and enabled agents
- [x] Dynamic planning roles reuse Fast, Balanced, Coding, and Research routing fallbacks
- [x] Implementation and Tester validate the installed Orion execution adapter and fail closed
- [x] Requested and actual assignments, fallback, tokens, cost, and duration persist in artifacts
- [x] Role assignments remain in external user configuration and never include Vault secrets
- [x] Existing task documents and approval hashes remain backward compatible
- [x] The living User Guide covers setup, commands, real workflows, best practices, and safety
- [x] An evergreen Definition of Done requires synchronized code, tests, help, docs, and verification
- [x] The complete regression suite passes with 362 tests

## v0.6.1 — Gatekeeper Interactive Approval and Codex Compatibility ✅

- [x] Interactive `team plan` offers explicit Y/N/D approval without copied IDs
- [x] Manual planning, approval, and implementation commands remain available
- [x] Approvals remain immutable, single-use, plan-hash-bound, and workspace-bound
- [x] Codex CLI options are detected from a cached bounded help probe
- [x] Unsupported optional flags are omitted without weakening required protections
- [x] The exact approved Standard or Git workspace receives `workspace-write`
- [x] Native Windows execution explicitly uses the elevated Codex sandbox
- [x] Network, user configuration, extra writable roots, Git actions, and raw output remain unavailable
- [x] Structured implementation and test artifacts stop at Awaiting Review
- [x] The complete regression suite passes with 351 tests

## v0.6.0 — Courier Email and Windows Discovery ✅

- [x] Gmail uses Google OAuth with `gmail.readonly` only
- [x] Outlook / Microsoft 365 uses MSAL and Graph with `Mail.Read`
- [x] Calendar client configuration is reused without silently expanding Calendar tokens
- [x] Mail token caches are external, owner-only, and independently disconnectable
- [x] Connect Center and explicit status show provider, account, capability, unread count, health, and last check
- [x] Inbox, unread, search, read, thread, and local summaries share normalized models
- [x] Result counts and AI-facing context are bounded centrally
- [x] Raw HTML is not rendered; attachments are metadata-only and never downloaded
- [x] Home uses cached status and performs no mailbox network call during startup
- [x] First Contact supports Gmail, Microsoft, both, skip, rerun, and failure preservation
- [x] Legacy direct send is disabled until persisted one-use outbound approvals exist
- [x] One resolver covers Codex, Claude, and Gemini extensionless/`.cmd`/`.exe`/`.ps1` forms
- [x] `%APPDATA%\npm` and bounded `npm prefix -g` fallbacks recover npm-installed CLIs
- [x] Version probes and bridge execution safely support Windows wrappers without `shell=True`
- [x] Codex Desktop and ChatGPT Desktop are detected and reported independently
- [x] Status reports executable path, discovery source, PATH visibility, probe result, and safe diagnostics
- [x] The complete regression suite passes with 334 tests

## v0.5.9 — Standard and Git Workspace Execution ✅

- [x] Ordinary directories support Team approval, implementation, review, and rollback
- [x] Git repositories and active subdirectories are detected through one capability model
- [x] Approvals bind plan, workspace capability, engine, scope, and operation
- [x] The router passes one validated immutable execution context to Codex Bridge
- [x] Bounded baselines independently report created, modified, and deleted files
- [x] Text changes receive redacted unified diffs; binary changes receive metadata only
- [x] Snapshot limits and incomplete baselines fail before Codex or approval consumption
- [x] Rollback restores preimages only when no affected file changed again
- [x] Missing workspaces can be created after confirmation without automatic `git init`
- [x] Git-only commands reject Standard mode without blocking Team execution
- [x] The complete regression suite passes with 292 tests

## v0.5.8 — Provider-Neutral First Contact ✅

- [x] First Contact offers Ollama, OpenAI, Gemini, multiple providers, and skip
- [x] Cloud credentials are verified in memory before external Vault persistence
- [x] Failed and cancelled setup preserves working credentials and the active provider
- [x] Ollama reachability and installed models are discovered dynamically
- [x] Multiple-provider setup reuses Fast, Balanced, Coding, and Research routing
- [x] Forced reruns merge profile, workspace, services, provider, and routing settings
- [x] Completion reports providers, models, routing, services, and execution engines
- [x] ChatGPT Desktop is identified as a desktop app rather than a CLI execution engine
- [x] The obsolete First Light setup script delegates to the supported onboarding path
- [x] The complete regression suite passes with 273 tests

## v0.5.7.1 — Execution Engine Handoff ✅

- [x] `team implement` resolves its implementation engine exactly once
- [x] The validated engine and executable path are handed directly to Codex Bridge
- [x] Direct bridge callers must supply a validated immutable engine snapshot
- [x] Pass-then-fail duplicate-probe behavior is covered by regression tests
- [x] Immutable approvals, workspace binding, and single-use claims remain unchanged

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
