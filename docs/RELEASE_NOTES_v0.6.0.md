# Orion v0.6.0 — Courier

Courier gives Orion one read-only Email subsystem for Gmail and Microsoft Outlook /
Microsoft 365. It replaces the provider-specific legacy Gmail path with normalized
models and a single `EmailService` used by the CLI, Connect Center, Home, First Contact,
shared question routing, and future interfaces.

## Provider-neutral read access

Both adapters return the same account, folder, message summary, full message, thread,
attachment metadata, outbound request, and provider-status records. Provider IDs are
kept on every message and qualified references prevent a Gmail ID from being sent to
Microsoft Graph or vice versa.

Courier supports bounded inbox, unread, search, message, thread, and local-summary
operations. Default pages contain 10 messages and every service path is capped at 50.
Attachments remain metadata-only. Gmail multipart and Microsoft HTML messages become
safe bounded plain text; raw HTML, scripts, and styles are never rendered.

## Explicit read-only OAuth

Gmail requests only `gmail.readonly`. Microsoft requests delegated `User.Read` and
`Mail.Read`; MSAL automatically adds its reserved `offline_access`, `openid`, and
`profile` scopes. No send, modify, archive, trash, mark-state, draft,
or attachment-download permission is requested.

Calendar and Email now use shared OAuth helpers, while Mail retains separate external
token caches. Existing Google OAuth client-file configuration and Microsoft Entra client
ID/tenant values are reused where possible. Separate tokens make incremental Mail
consent explicit and allow `email disconnect` to remove Mail locally without damaging
Calendar.

OAuth caches live beneath `~/.orion/tokens/`, use atomic writes and owner-only
permissions where supported, and never enter normal configuration, output, logs, task
artifacts, or normalized models. Errors are reduced to safe corrective messages.

## Commands

```text
email status
email providers
email configure <gmail|microsoft>
email connect <gmail|microsoft>
email disconnect <gmail|microsoft>
email accounts
email inbox [provider]
email unread [provider]
email search "<query>" [provider]
email read <provider:message-id>
email thread <provider:message-id>
email summarize [provider]
email use <provider>
```

Explicit `email status` and `connect health` refresh provider identity, capability,
unread count, health, and last-success time. Home never makes a mailbox call at startup;
it uses a cached count when available and otherwise reports connected accounts.

## First Contact

First Contact can connect Gmail, Outlook / Microsoft 365, both, or neither. Existing
working connections are defaults and are not reauthorized. Failed or cancelled Mail
consent preserves previous Email, Calendar, profile, workspace, and AI settings.

When Calendar is already configured, First Contact explains that the same client
configuration can be reused but Mail still needs explicit additional read consent.

## Deliberate Phase B boundary

The legacy Gmail code requested send permission during initial consent and sent after a
simple yes/no prompt. Courier removes that path from the runtime. `email send`, reply,
forward, archive, trash, mark-state, provider-draft, and attachment commands stop before
the provider.

Phase B must first add persisted, auditable, one-use approvals bound to the exact
provider, account, operation, recipients, CC/BCC, subject, complete body, attachment
names/digests, and reply or forward context. Payload changes must invalidate approval,
and attachment destinations must be explicitly approved and path-safe.

## Reliable Windows execution discovery

Courier also fixes Windows execution discovery for npm-installed Codex, Claude, and
Gemini CLIs. One reusable resolver searches extensionless, `.cmd`, `.exe`, and `.ps1`
forms, then checks `%APPDATA%\npm` and a bounded `npm prefix -g` fallback. Every
candidate must pass a short no-shell version probe; successful stderr version output is
accepted, while broken wrappers retain their resolved path and a safe diagnostic.

The router carries the exact Ready executable in its immutable `ExecutionEngine`
handoff. Codex Bridge uses that supplied path and never rediscovers or falls back to a
bare command. Windows wrappers are launched through fixed interpreter arguments with
`shell=False`, including the same npm runtime environment used by discovery.

Codex Desktop and ChatGPT Desktop now have independent Appx, catalog, and known-location
detection. The Store identity `OpenAI.Codex` is reported only as Codex Desktop, desktop
apps remain informational, and an unavailable Appx query produces Detection Error
rather than a false absence. `execution status` includes Ready, Installed but not
executable, Not Installed, and Detection Error states plus executable, source, PATH
visibility, version-probe result, and sanitized diagnostics.

## Manual setup

Gmail requires a Google Desktop OAuth client with the Gmail API enabled. Microsoft Mail
requires an Entra public-client application supporting the desired personal and/or
work/school accounts plus delegated `User.Read` and `Mail.Read`. Installing Outlook on
the computer does not authorize Orion. See `docs/EMAIL.md` for step-by-step instructions.

## Verification

The complete suite passes **334 tests**, including provider registration, normalized
conversion, limits, selection, no-provider state, OAuth success/cancellation/scope/
refresh behavior, Gmail and Graph reads, error sanitization, bounded question context,
Connect and Home status, onboarding reruns/failures, separate Calendar/Mail tokens, and
write-action shutdown, Windows npm wrapper resolution, Store identity separation,
version probing, and router-to-bridge executable handoff.
