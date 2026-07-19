# Email — Courier Phase A

Orion Email is one provider-neutral, read-only service with two adapters:

```text
EmailService
├── GmailAdapter
└── MicrosoftGraphEmailAdapter
```

Connect Center, Home, First Contact, the CLI, natural-language request routing, and
future interfaces consume `EmailService`. They do not call Google or Microsoft APIs
directly.

## What Phase A can do

- connect and disconnect Gmail or Microsoft Mail;
- report account identity, authorization health, read-only capability, unread count,
  last successful check, and sanitized provider problems;
- list bounded inbox and unread pages;
- search bounded provider results;
- read safe plain-text messages and bounded conversations;
- show attachment names, types, and sizes without downloading them;
- produce a local bounded summary of relevant recent or unread messages.

Phase A cannot send, reply, forward, archive, trash, mark messages, save provider
drafts, or download attachments. Those actions stop before the provider and identify
the Phase B immutable-approval requirement. A local proposed draft will never imply
permission to send.

## Commands

```text
email status
email providers
email configure gmail
email configure microsoft
email connect gmail
email connect microsoft
email disconnect gmail
email disconnect microsoft
email accounts
email inbox [gmail|microsoft]
email unread [gmail|microsoft]
email search "<query>" [gmail|microsoft]
email read <provider:message-id>
email thread <provider:message-id>
email summarize [gmail|microsoft]
email use <gmail|microsoft>
```

When both accounts are connected, inbox, unread, search, and summarize merge their
bounded normalized results. Message and thread references include the provider, such
as `gmail:17c...` or `microsoft:AQM...`, so Orion cannot send an ID to the wrong API.

## Gmail setup

1. Open Google Cloud Console and choose or create a project.
2. Enable the **Gmail API**.
3. Configure the OAuth consent screen for the accounts that may use Orion.
4. Create an OAuth client with application type **Desktop app**.
5. Download the client JSON. If the existing Google Calendar OAuth client is allowed
   to use Gmail, it may be reused; otherwise keep a separate client file.
6. Run `email configure gmail` and enter the downloaded file path.
7. Run `email connect gmail` and approve the clearly displayed read-only request.

Courier requests only:

```text
https://www.googleapis.com/auth/gmail.readonly
```

It does not request `gmail.send`, `gmail.modify`, or `gmail.compose`. Existing Google
Calendar consent is not silently expanded. Mail uses a separate external token cache,
so `email disconnect gmail` does not remove Calendar authorization.

Existing Orion Gmail users retain the conventional `google-gmail-token.json` cache and
OAuth client path when present. Courier can use a previously granted token that includes
read access, but it no longer exercises or requests the legacy send capability.

## Microsoft Outlook / Microsoft 365 setup

Installing Outlook on the computer does not authorize Orion. Orion accesses mail only
through Microsoft Graph delegated OAuth.

1. Open the Microsoft Entra admin center and register an application.
2. Select account types appropriate for the installation. To support Outlook.com,
   Hotmail, Live, and organizational Microsoft 365 accounts, include personal Microsoft
   accounts and organizational directories.
3. Configure it as a public desktop/native client and allow the localhost redirect used
   by MSAL interactive sign-in. Enable public client flows if the registration requires
   that setting.
4. Add delegated Microsoft Graph permissions `User.Read` and `Mail.Read`. MSAL
   automatically adds its reserved `offline_access`, `openid`, and `profile` scopes so
   it can refresh authorization without storing a password.
5. Copy the Application (client) ID. This ID is configuration, not a client secret.
6. Run `email configure microsoft`, enter the client ID, and normally keep tenant
   `common` for personal plus work/school account selection.
7. Run `email connect microsoft` and approve the read-only request.

If Microsoft Calendar already has a client ID and tenant configured, Courier reuses
those values automatically. Mail still has a separate token cache and explicit
`Mail.Read` consent, so Mail disconnect cannot damage Calendar.

## Token and configuration safety

- OAuth client paths, provider choices, limits, and non-secret Microsoft client IDs live
  in normal layered Orion configuration.
- OAuth tokens and MSAL caches live beneath `~/.orion/tokens/`, never in repository
  configuration, task artifacts, logs, or terminal output.
- Token files use atomic replacement and owner-only permissions where supported.
- `.gitignore` excludes legacy local token and OAuth state files plus supported Google
  OAuth client filenames.
- Provider errors are normalized. Authorization codes, access tokens, refresh tokens,
  response bodies, and client secrets are never included in diagnostics.
- `email disconnect <provider>` deletes only that local Mail token cache and disables
  the adapter. It does not delete Calendar authorization. Use the Google or Microsoft
  account security page when remote revocation is also desired.

## Privacy and bounded processing

Default and maximum result counts are configured under `email.result_limit` and capped
at 50. Local summaries inspect at most `email.summary_limit` relevant messages. Orion
does not send mailbox contents to the active AI provider for these commands; it formats
the normalized bounded result locally. Provider-marked importance is reported as an
observable signal rather than a subjective certainty.

Full messages expose safe plain text. HTML is converted to text without terminal
rendering, scripts, or styles. Provider body processing is capped at 250 KB and a
single terminal rendering is capped at 20,000 characters. Attachment bytes are never
requested in Phase A.

Home performs no mailbox network request during startup. It uses recently cached counts
when available and otherwise shows the number of connected accounts. `email status` or
`connect health` performs the explicit bounded refresh.

## Phase B safety contract

Before any write support is enabled, Orion must add persisted, one-use approvals bound
to the exact provider, account, action, recipients, CC/BCC, subject, complete body,
attachment names and digests, and reply/forward context. Payload changes must invalidate
approval; failed preflight must not consume it. Bulk changes and attachment downloads
must be separately approval-gated, workspace-bounded, path-traversal-safe, audited, and
reviewable.
