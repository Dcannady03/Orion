"""Provider-neutral Email Phase A regression and security tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orion.core.router import CommandRouter
from orion.services.email import (
    AttachmentMetadata,
    EmailAccount,
    EmailAddress,
    EmailBriefingProvider,
    EmailError,
    EmailProvider,
    EmailProviderUnavailable,
    EmailService,
    EmailThread,
    FullMessage,
    GmailAdapter,
    MessagePage,
    MessageSummary,
    MicrosoftGraphEmailAdapter,
    OutboundEmailRequest,
    build_email_service,
    redact_email_text,
)
from orion.services.oauth import (
    GoogleInstalledAppOAuth,
    MicrosoftPublicClientOAuth,
    OAuthCancelledError,
    OAuthScopeError,
)


def summary(provider="gmail", message_id="message-1", *, unread=True, importance="normal"):
    return MessageSummary(
        provider,
        f"{provider}-account",
        message_id,
        "thread-1",
        EmailAddress("Jess", "jess@example.test"),
        (EmailAddress("Daniel", "daniel@example.test"),),
        (),
        (),
        "Appointment update",
        "2026-07-18T12:00:00+00:00",
        unread,
        importance,
        ("INBOX",),
        "The appointment is tomorrow at 10.",
        False,
    )


def full_message(provider="gmail", message_id="message-1"):
    item = summary(provider, message_id)
    return FullMessage(
        item,
        "Complete safe plain-text body.",
        False,
        (AttachmentMetadata("attachment-1", "details.pdf", "application/pdf", 1200),),
    )


class FakeAdapter:
    def __init__(self, key, *, connected=True, messages=(), error=None):
        self.key = key
        self.display_name = "Gmail" if key == "gmail" else "Outlook / Microsoft 365"
        self.configured = True
        self.connected = connected
        self.messages = tuple(messages) or (summary(key),)
        self.error = error
        self.calls = []
        self.account_value = EmailAccount(key, f"{key}-account", f"{key}@example.test", key.title())

    def connect(self):
        self.calls.append(("connect",))
        if self.error:
            raise self.error
        self.connected = True
        return self.account_value

    def disconnect(self):
        self.calls.append(("disconnect",))
        self.connected = False

    def account(self):
        if self.error:
            raise self.error
        return self.account_value

    def folders(self, *, limit=10):
        return ()

    def unread_count(self):
        if self.error:
            raise self.error
        return sum(item.unread for item in self.messages)

    def list_messages(self, *, query="", unread=False, limit=10, page_token=""):
        self.calls.append(("list", query, unread, limit, page_token))
        if self.error:
            raise self.error
        values = tuple(item for item in self.messages if not unread or item.unread)
        return MessagePage(values[:limit], "next-page" if len(values) > limit else "")

    def read_message(self, message_id):
        self.calls.append(("read", message_id))
        if self.error:
            raise self.error
        return full_message(self.key, message_id)

    def read_thread(self, message_id, *, limit=10):
        self.calls.append(("thread", message_id, limit))
        if self.error:
            raise self.error
        item = full_message(self.key, message_id)
        return EmailThread(self.key, item.summary.account_id, "thread-1", (item,))


class EmailServiceTests(unittest.TestCase):
    def build(self, *, gmail=True, microsoft=False, summary_limit=10):
        google = FakeAdapter("gmail", connected=gmail)
        outlook = FakeAdapter("microsoft", connected=microsoft)
        service = EmailService(
            (
                EmailProvider("gmail", google.display_name, google, gmail),
                EmailProvider("microsoft", outlook.display_name, outlook, microsoft),
            ),
            default_provider="gmail" if gmail else "microsoft" if microsoft else "",
            summary_limit=summary_limit,
        )
        return service, google, outlook

    def test_provider_registration_and_normalized_models_expose_no_tokens(self):
        service, _, _ = self.build(gmail=True, microsoft=True)
        self.assertEqual(tuple(service.providers), ("gmail", "microsoft"))
        request = OutboundEmailRequest(
            "gmail",
            "account",
            (EmailAddress("Jess", "jess@example.test"),),
            (),
            (),
            "Subject",
            "Body",
        )
        self.assertFalse(any("token" in name for name in request.__dataclass_fields__))
        self.assertFalse(any("token" in name for name in summary().__dataclass_fields__))

    def test_provider_selection_merges_both_accounts_and_uses_namespaced_ids(self):
        service, google, outlook = self.build(gmail=True, microsoft=True)
        page = service.inbox()
        self.assertEqual({item.provider for item in page.messages}, {"gmail", "microsoft"})
        service.read("microsoft:graph-id")
        self.assertIn(("read", "graph-id"), outlook.calls)
        self.assertFalse(any(call[:2] == ("read", "graph-id") for call in google.calls))

    def test_no_provider_state_is_clear(self):
        service, _, _ = self.build(gmail=False, microsoft=False)
        with self.assertRaises(EmailProviderUnavailable) as raised:
            service.inbox()
        self.assertIn("No email provider is connected", str(raised.exception))

    def test_pagination_and_result_limits_are_bounded(self):
        values = tuple(summary("gmail", f"message-{index}") for index in range(80))
        adapter = FakeAdapter("gmail", messages=values)
        service = EmailService((EmailProvider("gmail", "Gmail", adapter, True),))
        page = service.inbox("gmail", limit=500)
        self.assertEqual(len(page.messages), 50)
        self.assertEqual(adapter.calls[-1][3], 50)
        service.inbox("gmail", limit=5, page_token="bounded-token")
        self.assertEqual(adapter.calls[-1][-1], "bounded-token")

    def test_error_is_normalized_without_provider_secret(self):
        secret = "access-token-that-must-not-leak"
        adapter = FakeAdapter("gmail", error=EmailError("Provider request failed."))
        service = EmailService((EmailProvider("gmail", "Gmail", adapter, True),))
        with self.assertRaises(EmailError) as raised:
            service.inbox()
        self.assertNotIn(secret, str(raised.exception))
        status = service.provider_statuses()[0]
        self.assertNotIn(secret, status.error)

    def test_local_summary_is_bounded_and_explains_importance_signal(self):
        values = (
            summary("gmail", "important", importance="high"),
            summary("gmail", "normal"),
            summary("gmail", "third"),
        )
        adapter = FakeAdapter("gmail", messages=values)
        service = EmailService(
            (EmailProvider("gmail", "Gmail", adapter, True),),
            summary_limit=2,
        )
        rendered = service.summarize(important_only=True)
        self.assertIn("provider marked high importance", rendered)
        self.assertIn("at most 2 relevant messages", rendered)
        self.assertEqual(adapter.calls[-1][3], 2)

    def test_email_question_routing_uses_only_relevant_bounded_results(self):
        adapter = FakeAdapter("gmail", messages=tuple(
            summary("gmail", f"message-{index}") for index in range(20)
        ))
        service = EmailService(
            (EmailProvider("gmail", "Gmail", adapter, True),), summary_limit=3
        )
        result = service.handle_request("Summarize my unread email")
        self.assertTrue(result.success)
        self.assertEqual(adapter.calls[-1][3], 3)
        self.assertNotIn("access_token", result.output)

    def test_latest_thread_question_requires_and_uses_one_qualified_message(self):
        service, adapter, _ = self.build(gmail=True)
        missing = service.handle_request("What was the latest message in this thread?")
        self.assertFalse(missing.success)
        result = service.handle_request(
            "What was the latest message in this thread gmail:message-1?"
        )
        self.assertTrue(result.success)
        self.assertIn("Complete safe plain-text body", result.output)
        self.assertEqual(adapter.calls[-1], ("thread", "message-1", 10))

    def test_briefing_is_compact_and_never_performs_startup_network_check(self):
        service, adapter, _ = self.build(gmail=True)
        item = EmailBriefingProvider(service).get_briefing()[0]
        self.assertEqual(item.title, "Email")
        self.assertIn("1 account connected", item.message)
        self.assertEqual(adapter.calls, [])

    def test_failed_connect_does_not_enable_or_replace_existing_default(self):
        google = FakeAdapter("gmail", connected=False, error=EmailError("connection failed"))
        outlook = FakeAdapter("microsoft", connected=True)
        service = EmailService(
            (
                EmailProvider("gmail", "Gmail", google, False),
                EmailProvider("microsoft", outlook.display_name, outlook, True),
            ),
            default_provider="microsoft",
        )
        with self.assertRaises(EmailError):
            service.connect("gmail")
        self.assertFalse(service.providers["gmail"].enabled)
        self.assertEqual(service.default_provider, "microsoft")


class FakeGoogleCall:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def execute(self):
        if self.error:
            raise self.error
        return self.value


class FakeGmailAPI:
    def __init__(self):
        self.list_calls = []
        self.message = {
            "id": "message-1",
            "threadId": "thread-1",
            "labelIds": ["INBOX", "UNREAD", "IMPORTANT"],
            "snippet": "Safe preview",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Gmail subject"},
                    {"name": "From", "value": "Jess <jess@example.test>"},
                    {"name": "To", "value": "Daniel <daniel@example.test>"},
                    {"name": "Date", "value": "Fri, 18 Jul 2026 12:00:00 +0000"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": "U2FmZSBib2R5"}},
                    {
                        "mimeType": "application/pdf",
                        "filename": "details.pdf",
                        "body": {"attachmentId": "attachment-secret-id", "size": 42},
                    },
                ],
            },
        }

    def users(self): return self
    def messages(self): return self
    def labels(self): return self
    def threads(self): return self
    def getProfile(self, **_kwargs): return FakeGoogleCall({"emailAddress": "gmail@example.test"})

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        if "q" in kwargs:
            return FakeGoogleCall({"messages": [{"id": "message-1"}], "nextPageToken": "next"})
        return FakeGoogleCall({"labels": []})

    def get(self, **kwargs):
        if kwargs.get("id") == "INBOX":
            return FakeGoogleCall({"messagesUnread": 2})
        if kwargs.get("id") == "thread-1":
            return FakeGoogleCall({"messages": [self.message]})
        return FakeGoogleCall(self.message)


class GmailAdapterTests(unittest.TestCase):
    def build(self):
        adapter = GmailAdapter("credentials.json", "token.json")
        adapter._service = FakeGmailAPI()
        adapter._account = EmailAccount("gmail", "gmail@example.test", "gmail@example.test")
        return adapter

    def test_inbox_unread_search_read_and_thread_are_normalized(self):
        adapter = self.build()
        page = adapter.list_messages(query="from:jess", unread=True, limit=5)
        self.assertEqual(page.messages[0].provider, "gmail")
        self.assertEqual(page.messages[0].importance, "high")
        query = adapter._service.list_calls[-1]["q"]
        self.assertIn("in:inbox", query)
        self.assertIn("is:unread", query)
        self.assertIn("from:jess", query)
        self.assertEqual(adapter.unread_count(), 2)

        message = adapter.read_message("message-1")
        self.assertEqual(message.body_text, "Safe body")
        self.assertEqual(message.attachments[0].filename, "details.pdf")
        self.assertNotIn("attachment-secret-id", message.body_text)
        thread = adapter.read_thread("message-1")
        self.assertEqual(len(thread.messages), 1)

    def test_api_failure_is_sanitized(self):
        adapter = self.build()
        adapter._service = SimpleNamespace(
            users=lambda: SimpleNamespace(
                messages=lambda: SimpleNamespace(
                    list=lambda **kwargs: FakeGoogleCall(error=RuntimeError("token=secret-value"))
                )
            )
        )
        with self.assertRaises(EmailError) as raised:
            adapter.list_messages()
        self.assertNotIn("secret-value", str(raised.exception))


class FakeMicrosoftAdapter(MicrosoftGraphEmailAdapter):
    def __init__(self):
        super().__init__("client-id", "mail-cache.json", tenant="common")
        self.values = {}
        self.calls = []
        self.oauth.token_path = Path("mail-cache.json")

    @property
    def connected(self):
        return True

    def _request(self, method, path_or_url, *, params=None):
        self.calls.append((method, path_or_url, params))
        if path_or_url == "/me":
            return {
                "id": "account-id", "displayName": "Daniel",
                "mail": "outlook@example.test",
            }
        if path_or_url == "/me/mailFolders/inbox":
            return {"unreadItemCount": 4}
        if path_or_url == "/me/mailFolders/inbox/messages":
            return {"value": [self.message_value()], "@odata.nextLink": f"{self.GRAPH_ROOT}/next"}
        if path_or_url.startswith("/me/messages/"):
            return self.message_value(full=True)
        if path_or_url == "/me/messages":
            return {"value": [{"id": "graph-message"}]}
        if path_or_url == "/me/mailFolders":
            return {"value": []}
        raise AssertionError(path_or_url)

    @staticmethod
    def message_value(full=False):
        value = {
            "id": "graph-message",
            "conversationId": "conversation-1",
            "subject": "Microsoft subject",
            "from": {"emailAddress": {"name": "Jess", "address": "jess@example.test"}},
            "toRecipients": [{"emailAddress": {"name": "Daniel", "address": "daniel@example.test"}}],
            "ccRecipients": [],
            "bccRecipients": [],
            "receivedDateTime": "2026-07-18T12:00:00Z",
            "isRead": False,
            "importance": "high",
            "parentFolderId": "inbox",
            "bodyPreview": "Microsoft preview",
            "hasAttachments": True,
        }
        if full:
            value.update({
                "body": {"contentType": "html", "content": "<p>Safe <b>body</b></p><script>bad()</script>"},
                "attachments": [{
                    "id": "attachment-id", "name": "agenda.docx",
                    "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "size": 200, "isInline": False,
                }],
            })
        return value


class MicrosoftAdapterTests(unittest.TestCase):
    def test_personal_or_work_configuration_and_normalized_operations(self):
        adapter = FakeMicrosoftAdapter()
        self.assertEqual(adapter.oauth.tenant, "common")
        page = adapter.list_messages(query="appointment", unread=True, limit=5)
        self.assertEqual(page.messages[0].provider, "microsoft")
        params = adapter.calls[-2][2] if adapter.calls[-1][1] == "/me" else adapter.calls[-1][2]
        listing = next(call for call in adapter.calls if call[1] == "/me/mailFolders/inbox/messages")
        self.assertEqual(listing[2]["$filter"], "isRead eq false")
        self.assertIn("appointment", listing[2]["$search"])
        self.assertEqual(adapter.unread_count(), 4)

        message = adapter.read_message("graph-message")
        self.assertEqual(message.body_text, "Safe body")
        self.assertNotIn("bad()", message.body_text)
        self.assertEqual(message.attachments[0].filename, "agenda.docx")
        thread = adapter.read_thread("graph-message")
        self.assertEqual(thread.conversation_id, "conversation-1")

    def test_graph_pagination_rejects_external_url(self):
        adapter = MicrosoftGraphEmailAdapter("client", "token.json")
        adapter.oauth.token = lambda interactive=False: "secret-token"
        with self.assertRaises(EmailError):
            adapter._request("GET", "https://attacker.example/messages")


class OAuthTests(unittest.TestCase):
    class Credentials:
        def __init__(self, scopes, *, expired=False, valid=True):
            self.scopes = tuple(scopes)
            self.expired = expired
            self.valid = valid
            self.refresh_token = "refresh-secret"
            self.refreshed = False

        def has_scopes(self, scopes):
            return set(scopes).issubset(self.scopes)

        def refresh(self, _request):
            self.expired = False
            self.valid = True
            self.refreshed = True

        def to_json(self):
            return json.dumps({"token": "stored-secret", "scopes": list(self.scopes)})

    def test_google_success_cancel_missing_scope_and_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            credentials_file = root / "client.json"
            credentials_file.write_text("{}", encoding="utf-8")
            token = root / "token.json"
            token.write_text("{}", encoding="utf-8")
            cached = self.Credentials(("calendar.read",), expired=False)

            class CredentialsFactory:
                @staticmethod
                def from_authorized_user_file(_path): return cached

            flow_credentials = self.Credentials(("mail.read",))
            flow = SimpleNamespace(run_local_server=lambda **_kwargs: flow_credentials)
            flow_factory = SimpleNamespace(
                from_client_secrets_file=lambda *_args: flow
            )
            oauth = GoogleInstalledAppOAuth(
                credentials_file, token, ("mail.read",),
                service_name="Mail", connect_command="email connect gmail",
            )
            oauth._imports = lambda: (object, CredentialsFactory, flow_factory)
            with self.assertRaises(OAuthScopeError):
                oauth.credentials(interactive=False)
            self.assertIs(oauth.credentials(interactive=True), flow_credentials)
            self.assertIn("stored-secret", token.read_text(encoding="utf-8"))

            refreshed = self.Credentials(("mail.read",), expired=True, valid=False)
            CredentialsFactory.from_authorized_user_file = staticmethod(lambda _path: refreshed)
            self.assertIs(oauth.credentials(interactive=False), refreshed)
            self.assertTrue(refreshed.refreshed)

            cancelled_flow = SimpleNamespace(
                run_local_server=lambda **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
            )
            flow_factory.from_client_secrets_file = lambda *_args: cancelled_flow
            cached.valid = False
            CredentialsFactory.from_authorized_user_file = staticmethod(lambda _path: cached)
            with self.assertRaises(OAuthCancelledError):
                oauth.credentials(interactive=True)

    def test_microsoft_silent_cache_interactive_success_and_cancellation(self):
        with tempfile.TemporaryDirectory() as temp:
            token = Path(temp) / "cache.json"
            cache = SimpleNamespace(
                has_state_changed=True,
                serialize=lambda: "serialized-secret-cache",
            )
            silent_scopes = []
            app = SimpleNamespace(
                get_accounts=lambda: [{"id": "account"}],
                acquire_token_silent=lambda scopes, account: (
                    silent_scopes.extend(scopes) or {"access_token": "access-secret"}
                ),
                acquire_token_interactive=lambda **kwargs: None,
            )
            oauth = MicrosoftPublicClientOAuth(
                "client", token, ("Mail.Read", "offline_access"),
                service_name="Microsoft Mail", connect_command="email connect microsoft",
            )
            oauth._cache_and_app = lambda: (app, cache)
            self.assertEqual(oauth.token(), "access-secret")
            self.assertEqual(silent_scopes, ["Mail.Read"])
            self.assertEqual(token.read_text(encoding="utf-8"), "serialized-secret-cache")

            app.get_accounts = lambda: []
            app.acquire_token_interactive = lambda **kwargs: {"error": "user_cancelled"}
            with self.assertRaises(OAuthCancelledError):
                oauth.token(interactive=True)


class ConfigurationAndSafetyTests(unittest.TestCase):
    class Config:
        def __init__(self, values):
            self.values = dict(values)
            self.saved = 0

        def get(self, key, default=None): return self.values.get(key, default)
        def set(self, key, value): self.values[key] = value
        def save(self): self.saved += 1

    class Paths:
        def __init__(self, root): self.root = Path(root)
        def user_file(self, value, *, category=None): return self.root / (category or "") / Path(value).name

    def test_phase_a_oauth_scopes_are_read_only(self):
        gmail = GmailAdapter("client.json", "gmail-token.json")
        microsoft = MicrosoftGraphEmailAdapter("client", "microsoft-token.json")
        self.assertEqual(gmail.oauth.scopes, ("https://www.googleapis.com/auth/gmail.readonly",))
        self.assertIn("Mail.Read", microsoft.oauth.scopes)
        self.assertNotIn("Mail.ReadWrite", microsoft.oauth.scopes)
        self.assertNotIn("Mail.Send", microsoft.oauth.scopes)
        self.assertFalse(any("send" in scope.lower() for scope in gmail.oauth.scopes))

    def test_terminal_and_summary_redaction_removes_credential_shaped_mail_content(self):
        secret = "sk-this-secret-must-not-render"
        rendered = redact_email_text(f"API key: {secret}\nPassword=do-not-show")
        self.assertNotIn(secret, rendered)
        self.assertNotIn("do-not-show", rendered)
        self.assertIn("<redacted>", rendered)
        self.assertEqual(len(redact_email_text("x" * 30_000)), 20_000)

    def test_calendar_client_configuration_is_reused_but_mail_tokens_are_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self.Config({
                "calendar.google.credentials_path": "config/shared-google-client.json",
                "calendar.google.token_path": "google-calendar-token.json",
                "calendar.microsoft.client_id": "shared-microsoft-client",
                "calendar.microsoft.tenant": "common",
                "calendar.microsoft.token_path": "microsoft-calendar-token.json",
            })
            service = build_email_service(config, self.Paths(temp))
            gmail = service.providers["gmail"].adapter
            microsoft = service.providers["microsoft"].adapter
            self.assertEqual(gmail.credentials_path, Path("config/shared-google-client.json"))
            self.assertEqual(microsoft.client_id, "shared-microsoft-client")
            self.assertNotEqual(gmail.token_path.name, "google-calendar-token.json")
            self.assertNotEqual(microsoft.token_path.name, "microsoft-calendar-token.json")

    def test_disconnect_removes_only_mail_token_and_preserves_calendar_token(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calendar_token = root / "google-calendar-token.json"
            mail_token = root / "google-mail-token.json"
            calendar_token.write_text("calendar", encoding="utf-8")
            mail_token.write_text("mail", encoding="utf-8")
            adapter = GmailAdapter(root / "client.json", mail_token)
            adapter.disconnect()
            self.assertTrue(calendar_token.exists())
            self.assertFalse(mail_token.exists())

    def test_write_and_attachment_commands_stop_before_provider_action(self):
        service = EmailService((EmailProvider("gmail", "Gmail", FakeAdapter("gmail"), True),))
        router = CommandRouter(SimpleNamespace(email_service=service))
        commands = (
            "email send",
            "email reply gmail:message-1",
            "email forward gmail:message-1",
            "email trash gmail:message-1",
            "email archive gmail:message-1",
            "email mark read gmail:message-1",
            "email mark unread gmail:message-1",
            "email attachment gmail:message-1 ../../outside.exe",
        )
        for command in commands:
            with self.subTest(command=command), patch("builtins.print") as output:
                router.handle(command)
                rendered = "\n".join(str(call.args[0]) for call in output.call_args_list if call.args)
                self.assertIn("not enabled", rendered)
                self.assertIn("immutable approval", rendered)


if __name__ == "__main__":
    unittest.main()
