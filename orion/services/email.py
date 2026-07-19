"""Provider-neutral, read-only email for Gmail and Microsoft Graph."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Callable, Iterable, Protocol
from urllib.parse import quote

from orion.services.base import ServiceResult, ServiceState, ServiceStatus
from orion.services.briefing import BriefingItem, BriefingPriority
from orion.services.oauth import (
    GoogleInstalledAppOAuth,
    MicrosoftPublicClientOAuth,
    OAuthCancelledError,
    OAuthError,
    OAuthScopeError,
)


GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MICROSOFT_MAIL_READ_SCOPES = ("User.Read", "Mail.Read", "offline_access")
EMAIL_PROVIDERS = ("gmail", "microsoft")
EMAIL_PROVIDER_ALIASES = {
    "google": "gmail",
    "gmail": "gmail",
    "microsoft": "microsoft",
    "outlook": "microsoft",
    "office365": "microsoft",
    "m365": "microsoft",
}
DEFAULT_RESULT_LIMIT = 10
MAX_RESULT_LIMIT = 50
MAX_BODY_BYTES = 250_000
MAX_DISPLAY_BYTES = 20_000
MAIL_SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~+/=-]{8,}|sk-[a-z0-9_-]{8,}|"
    r"((api[_-]?key|secret|token|password|authorization)\s*[:=]\s*)\S+)"
)


class EmailError(ConnectionError):
    """Sanitized email failure safe for output and persisted diagnostics."""


class EmailProviderUnavailable(EmailError):
    """The requested provider is disabled, disconnected, or ambiguous."""


@dataclass(frozen=True, slots=True)
class EmailAddress:
    name: str
    address: str


@dataclass(frozen=True, slots=True)
class EmailAccount:
    provider: str
    account_id: str
    email_address: str
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class MailFolder:
    provider: str
    folder_id: str
    name: str
    unread_count: int = 0
    total_count: int = 0


@dataclass(frozen=True, slots=True)
class AttachmentMetadata:
    attachment_id: str
    filename: str
    content_type: str
    size_bytes: int
    inline: bool = False


@dataclass(frozen=True, slots=True)
class MessageSummary:
    provider: str
    account_id: str
    message_id: str
    conversation_id: str
    sender: EmailAddress
    recipients: tuple[EmailAddress, ...]
    cc: tuple[EmailAddress, ...]
    bcc: tuple[EmailAddress, ...]
    subject: str
    received_at: str
    unread: bool
    importance: str
    folders: tuple[str, ...]
    preview: str
    has_attachments: bool = False

    @property
    def reference(self) -> str:
        return f"{self.provider}:{self.message_id}"


@dataclass(frozen=True, slots=True)
class FullMessage:
    summary: MessageSummary
    body_text: str
    html_available: bool
    attachments: tuple[AttachmentMetadata, ...]

    @property
    def reference(self) -> str:
        return self.summary.reference


@dataclass(frozen=True, slots=True)
class EmailThread:
    provider: str
    account_id: str
    conversation_id: str
    messages: tuple[FullMessage, ...]


@dataclass(frozen=True, slots=True)
class OutboundEmailRequest:
    """Normalized Phase B payload; defining it does not authorize sending."""

    provider: str
    account_id: str
    to: tuple[EmailAddress, ...]
    cc: tuple[EmailAddress, ...]
    bcc: tuple[EmailAddress, ...]
    subject: str
    body: str
    attachment_names: tuple[str, ...] = ()
    reply_to_message_id: str = ""
    forward_message_id: str = ""


@dataclass(frozen=True, slots=True)
class ProviderConnectionStatus:
    provider: str
    display_name: str
    enabled: bool
    configured: bool
    connected: bool
    healthy: bool
    account: str
    capabilities: tuple[str, ...]
    unread_count: int | None = None
    last_checked: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class MessagePage:
    messages: tuple[MessageSummary, ...]
    next_page_token: str = ""


class EmailAdapter(Protocol):
    key: str
    display_name: str

    @property
    def configured(self) -> bool: ...
    @property
    def connected(self) -> bool: ...
    def connect(self) -> EmailAccount: ...
    def disconnect(self) -> None: ...
    def account(self) -> EmailAccount: ...
    def folders(self, *, limit: int = DEFAULT_RESULT_LIMIT) -> tuple[MailFolder, ...]: ...
    def unread_count(self) -> int: ...
    def list_messages(self, *, query: str = "", unread: bool = False,
                      limit: int = DEFAULT_RESULT_LIMIT, page_token: str = "") -> MessagePage: ...
    def read_message(self, message_id: str) -> FullMessage: ...
    def read_thread(self, message_id: str, *, limit: int = DEFAULT_RESULT_LIMIT) -> EmailThread: ...


@dataclass(slots=True)
class EmailProvider:
    key: str
    display_name: str
    adapter: EmailAdapter
    enabled: bool = False


def _clean_text(value: object, *, limit: int = 10_000) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value or ""))
    return text.strip()[:limit]


def redact_email_text(value: object, *, limit: int = MAX_DISPLAY_BYTES) -> str:
    """Return bounded mail content safe for terminal or AI-facing summaries."""
    return MAIL_SECRET_PATTERN.sub("<redacted>", _clean_text(value, limit=limit))


def _addresses(value: object) -> tuple[EmailAddress, ...]:
    parsed = []
    for name, address in getaddresses([str(value or "")]):
        normalized = _clean_text(address, limit=320)
        if normalized:
            parsed.append(EmailAddress(_clean_text(name, limit=320), normalized))
    return tuple(parsed)


def _iso_date(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError):
        return _clean_text(raw, limit=100)


def _decode_websafe(value: str, *, max_bytes: int = MAX_BODY_BYTES) -> str:
    if not value:
        return ""
    try:
        padded = value + "=" * (-len(value) % 4)
        data = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeError):
        return ""
    return data[:max_bytes].decode("utf-8", errors="replace")


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._blocked = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style"}:
            self._blocked += 1
        elif tag.lower() in {"p", "div", "br", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style"} and self._blocked:
            self._blocked -= 1
        elif tag.lower() in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._blocked:
            self.parts.append(data)


def _html_to_text(value: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(value[:MAX_BODY_BYTES])
        parser.close()
    except Exception:
        return ""
    lines = (re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)[:MAX_BODY_BYTES]


class GmailAdapter:
    key = "gmail"
    display_name = "Gmail"

    def __init__(self, credentials_path: str | Path, token_path: str | Path) -> None:
        self.oauth = GoogleInstalledAppOAuth(
            credentials_path,
            token_path,
            (GMAIL_READ_SCOPE,),
            service_name="Gmail",
            connect_command="email connect gmail",
        )
        self._service = None
        self._account: EmailAccount | None = None

    @property
    def credentials_path(self) -> Path:
        return self.oauth.credentials_path

    @credentials_path.setter
    def credentials_path(self, value: str | Path) -> None:
        self.oauth.credentials_path = Path(value)

    @property
    def token_path(self) -> Path:
        return self.oauth.token_path

    @property
    def configured(self) -> bool:
        return self.oauth.configured

    @property
    def connected(self) -> bool:
        return self.oauth.connected

    @staticmethod
    def _build(credentials):
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - dependency environment
            raise EmailError(
                "Google Gmail dependencies are not installed. "
                "Run: python -m pip install -r requirements.txt"
            ) from exc
        try:
            return build("gmail", "v1", credentials=credentials, cache_discovery=False)
        except Exception as exc:
            raise EmailError("Gmail client could not start.") from exc

    def _client(self, *, interactive: bool = False):
        if self._service is None:
            try:
                credentials = self.oauth.credentials(interactive=interactive)
                self._service = self._build(credentials)
            except OAuthError as exc:
                raise EmailError(str(exc)) from exc
        return self._service

    def connect(self) -> EmailAccount:
        self._service = None
        self._client(interactive=True)
        return self.account()

    def disconnect(self) -> None:
        try:
            self.oauth.disconnect()
        except OAuthError as exc:
            raise EmailError(str(exc)) from exc
        self._service = None
        self._account = None

    def account(self) -> EmailAccount:
        if self._account is not None:
            return self._account
        try:
            value = self._client().users().getProfile(userId="me").execute()
        except EmailError:
            raise
        except Exception as exc:
            raise EmailError("Gmail account check failed.") from exc
        address = _clean_text(value.get("emailAddress"), limit=320)
        self._account = EmailAccount(self.key, address or "me", address, address)
        return self._account

    def profile(self) -> dict[str, str]:
        """Compatibility view for older Connect callers."""
        account = self.account()
        return {"emailAddress": account.email_address}

    def folders(self, *, limit: int = DEFAULT_RESULT_LIMIT) -> tuple[MailFolder, ...]:
        limit = _limit(limit)
        try:
            response = self._client().users().labels().list(userId="me").execute()
        except Exception as exc:
            raise EmailError("Gmail folder listing failed.") from exc
        folders = []
        for item in response.get("labels", [])[:limit]:
            folders.append(MailFolder(
                self.key,
                _clean_text(item.get("id"), limit=500),
                _clean_text(item.get("name"), limit=500),
                int(item.get("messagesUnread") or 0),
                int(item.get("messagesTotal") or 0),
            ))
        return tuple(folders)

    def unread_count(self) -> int:
        try:
            response = self._client().users().labels().get(userId="me", id="INBOX").execute()
            return max(0, int(response.get("messagesUnread") or 0))
        except Exception as exc:
            raise EmailError("Gmail unread count failed.") from exc

    def list_messages(
        self,
        *,
        query: str = "",
        unread: bool = False,
        limit: int = DEFAULT_RESULT_LIMIT,
        page_token: str = "",
    ) -> MessagePage:
        limit = _limit(limit)
        query_parts = ["in:inbox"]
        if unread:
            query_parts.append("is:unread")
        if query.strip():
            query_parts.append(query.strip()[:500])
        try:
            call = self._client().users().messages().list(
                userId="me",
                q=" ".join(query_parts),
                maxResults=limit,
                pageToken=page_token or None,
            )
            response = call.execute()
            messages = tuple(
                self._message(item["id"], full=False)
                for item in response.get("messages", [])[:limit]
                if isinstance(item, dict) and item.get("id")
            )
        except EmailError:
            raise
        except Exception as exc:
            raise EmailError("Gmail message listing failed.") from exc
        return MessagePage(messages, _clean_text(response.get("nextPageToken"), limit=2_000))

    def _raw_message(self, message_id: str, *, full: bool) -> dict:
        try:
            parameters = {
                "userId": "me",
                "id": _message_id(message_id),
                "format": "full" if full else "metadata",
            }
            if not full:
                parameters["metadataHeaders"] = [
                    "Subject", "From", "To", "Cc", "Bcc", "Date", "Importance", "X-Priority",
                ]
            return self._client().users().messages().get(
                **parameters,
            ).execute()
        except Exception as exc:
            raise EmailError("Gmail message could not be read.") from exc

    def _summary(self, value: dict) -> MessageSummary:
        payload = value.get("payload") or {}
        headers = {
            str(item.get("name", "")).lower(): str(item.get("value", ""))
            for item in payload.get("headers", [])
            if isinstance(item, dict)
        }
        labels = tuple(_clean_text(label, limit=200) for label in value.get("labelIds", []))
        importance = "high" if "IMPORTANT" in labels or headers.get("importance", "").lower() == "high" else "normal"
        if headers.get("x-priority", "").startswith("1"):
            importance = "high"
        account = self.account()
        sender = _addresses(headers.get("from")) or (EmailAddress("", "Unknown sender"),)
        return MessageSummary(
            provider=self.key,
            account_id=account.account_id,
            message_id=_clean_text(value.get("id"), limit=2_000),
            conversation_id=_clean_text(value.get("threadId"), limit=2_000),
            sender=sender[0],
            recipients=_addresses(headers.get("to")),
            cc=_addresses(headers.get("cc")),
            bcc=_addresses(headers.get("bcc")),
            subject=_clean_text(headers.get("subject") or "(No subject)", limit=1_000),
            received_at=_iso_date(headers.get("date")),
            unread="UNREAD" in labels,
            importance=importance,
            folders=labels,
            preview=_clean_text(value.get("snippet"), limit=2_000),
            has_attachments=self._has_attachments(payload),
        )

    def _message(self, message_id: str, *, full: bool) -> MessageSummary | FullMessage:
        value = self._raw_message(message_id, full=full)
        summary = self._summary(value)
        if not full:
            return summary
        body, html_available, attachments = self._payload(value.get("payload") or {})
        return FullMessage(summary, body, html_available, attachments)

    @classmethod
    def _has_attachments(cls, payload: dict) -> bool:
        if payload.get("filename"):
            return True
        return any(cls._has_attachments(part) for part in payload.get("parts", []) if isinstance(part, dict))

    @classmethod
    def _payload(cls, payload: dict) -> tuple[str, bool, tuple[AttachmentMetadata, ...]]:
        plain: list[str] = []
        html_parts: list[str] = []
        attachments: list[AttachmentMetadata] = []

        def walk(part: dict) -> None:
            mime = _clean_text(part.get("mimeType"), limit=250).lower()
            filename = _clean_text(part.get("filename"), limit=1_000)
            body = part.get("body") or {}
            attachment_id = _clean_text(body.get("attachmentId"), limit=2_000)
            size = max(0, int(body.get("size") or 0))
            if filename or attachment_id:
                attachments.append(AttachmentMetadata(
                    attachment_id,
                    filename or "Unnamed attachment",
                    mime or "application/octet-stream",
                    size,
                    inline=not bool(filename),
                ))
            elif mime == "text/plain" and body.get("data"):
                plain.append(_decode_websafe(str(body["data"])))
            elif mime == "text/html" and body.get("data"):
                html_parts.append(_decode_websafe(str(body["data"])))
            for child in part.get("parts", []):
                if isinstance(child, dict):
                    walk(child)

        walk(payload)
        body = "\n".join(item.strip() for item in plain if item.strip())[:MAX_BODY_BYTES]
        if not body and html_parts:
            body = _html_to_text("\n".join(html_parts))
        return body, bool(html_parts), tuple(attachments)

    def read_message(self, message_id: str) -> FullMessage:
        value = self._message(message_id, full=True)
        if not isinstance(value, FullMessage):  # pragma: no cover - type guard
            raise EmailError("Gmail returned an invalid message.")
        return value

    def read_thread(self, message_id: str, *, limit: int = DEFAULT_RESULT_LIMIT) -> EmailThread:
        limit = _limit(limit)
        first = self.read_message(message_id)
        conversation_id = first.summary.conversation_id
        try:
            value = self._client().users().threads().get(
                userId="me", id=conversation_id, format="full"
            ).execute()
            messages = tuple(
                self._full_from_value(item)
                for item in value.get("messages", [])[:limit]
                if isinstance(item, dict)
            )
        except Exception as exc:
            raise EmailError("Gmail thread could not be read.") from exc
        return EmailThread(self.key, first.summary.account_id, conversation_id, messages)

    def _full_from_value(self, value: dict) -> FullMessage:
        summary = self._summary(value)
        body, html_available, attachments = self._payload(value.get("payload") or {})
        return FullMessage(summary, body, html_available, attachments)


class MicrosoftGraphEmailAdapter:
    key = "microsoft"
    display_name = "Outlook / Microsoft 365"
    GRAPH_ROOT = "https://graph.microsoft.com/v1.0"

    def __init__(
        self,
        client_id: str,
        token_path: str | Path,
        *,
        tenant: str = "common",
        timeout: float = 10.0,
    ) -> None:
        self.oauth = MicrosoftPublicClientOAuth(
            client_id,
            token_path,
            MICROSOFT_MAIL_READ_SCOPES,
            tenant=tenant,
            service_name="Microsoft Mail",
            connect_command="email connect microsoft",
        )
        self.timeout = float(timeout)
        self._account: EmailAccount | None = None

    @property
    def client_id(self) -> str:
        return self.oauth.client_id

    @client_id.setter
    def client_id(self, value: str) -> None:
        self.oauth.client_id = str(value).strip()

    @property
    def token_path(self) -> Path:
        return self.oauth.token_path

    @property
    def configured(self) -> bool:
        return self.oauth.configured

    @property
    def connected(self) -> bool:
        return self.oauth.connected

    @staticmethod
    def _requests():
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - dependency environment
            raise EmailError(
                "Microsoft Graph dependencies are not installed. "
                "Run: python -m pip install -r requirements.txt"
            ) from exc
        return requests

    def _request(self, method: str, path_or_url: str, *, params: dict | None = None) -> dict:
        try:
            token = self.oauth.token(interactive=False)
        except OAuthError as exc:
            raise EmailError(str(exc)) from exc
        url = path_or_url if path_or_url.startswith("https://") else f"{self.GRAPH_ROOT}{path_or_url}"
        if not url.startswith(f"{self.GRAPH_ROOT}/"):
            raise EmailError("Microsoft pagination link was outside the Graph API boundary.")
        try:
            response = self._requests().request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "ConsistencyLevel": "eventual",
                },
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            value = response.json() if response.content else {}
        except Exception as exc:
            raise EmailError("Microsoft Mail request failed.") from exc
        if not isinstance(value, dict):
            raise EmailError("Microsoft Mail returned an invalid response.")
        return value

    def connect(self) -> EmailAccount:
        try:
            self.oauth.connect()
        except OAuthError as exc:
            raise EmailError(str(exc)) from exc
        self._account = None
        return self.account()

    def disconnect(self) -> None:
        try:
            self.oauth.disconnect()
        except OAuthError as exc:
            raise EmailError(str(exc)) from exc
        self._account = None

    def account(self) -> EmailAccount:
        if self._account is not None:
            return self._account
        value = self._request("GET", "/me", params={"$select": "id,displayName,mail,userPrincipalName"})
        account_id = _clean_text(value.get("id"), limit=2_000)
        address = _clean_text(value.get("mail") or value.get("userPrincipalName"), limit=320)
        self._account = EmailAccount(
            self.key,
            account_id or address or "me",
            address,
            _clean_text(value.get("displayName"), limit=500),
        )
        return self._account

    def folders(self, *, limit: int = DEFAULT_RESULT_LIMIT) -> tuple[MailFolder, ...]:
        limit = _limit(limit)
        value = self._request("GET", "/me/mailFolders", params={
            "$top": str(limit),
            "$select": "id,displayName,unreadItemCount,totalItemCount",
        })
        return tuple(
            MailFolder(
                self.key,
                _clean_text(item.get("id"), limit=2_000),
                _clean_text(item.get("displayName"), limit=500),
                max(0, int(item.get("unreadItemCount") or 0)),
                max(0, int(item.get("totalItemCount") or 0)),
            )
            for item in value.get("value", [])[:limit]
            if isinstance(item, dict)
        )

    def unread_count(self) -> int:
        value = self._request("GET", "/me/mailFolders/inbox", params={
            "$select": "unreadItemCount",
        })
        return max(0, int(value.get("unreadItemCount") or 0))

    def list_messages(
        self,
        *,
        query: str = "",
        unread: bool = False,
        limit: int = DEFAULT_RESULT_LIMIT,
        page_token: str = "",
    ) -> MessagePage:
        limit = _limit(limit)
        path = page_token or "/me/mailFolders/inbox/messages"
        params = None
        if not page_token:
            params = {
                "$top": str(limit),
                "$select": (
                    "id,conversationId,subject,from,toRecipients,ccRecipients,bccRecipients,"
                    "receivedDateTime,isRead,importance,parentFolderId,bodyPreview,hasAttachments"
                ),
                "$orderby": "receivedDateTime desc",
            }
            if unread:
                params["$filter"] = "isRead eq false"
            if query.strip():
                escaped = query.strip()[:500].replace('"', "")
                params["$search"] = f'"{escaped}"'
                params.pop("$orderby", None)
        value = self._request("GET", path, params=params)
        account = self.account()
        messages = tuple(
            self._summary(item, account)
            for item in value.get("value", [])[:limit]
            if isinstance(item, dict)
        )
        return MessagePage(messages, _clean_text(value.get("@odata.nextLink"), limit=10_000))

    @staticmethod
    def _graph_addresses(values: object) -> tuple[EmailAddress, ...]:
        results = []
        for item in values if isinstance(values, list) else []:
            address = item.get("emailAddress") if isinstance(item, dict) else None
            if not isinstance(address, dict):
                continue
            value = _clean_text(address.get("address"), limit=320)
            if value:
                results.append(EmailAddress(_clean_text(address.get("name"), limit=320), value))
        return tuple(results)

    @classmethod
    def _summary(cls, value: dict, account: EmailAccount) -> MessageSummary:
        sender_values = cls._graph_addresses([value.get("from") or {}])
        return MessageSummary(
            provider=cls.key,
            account_id=account.account_id,
            message_id=_clean_text(value.get("id"), limit=2_000),
            conversation_id=_clean_text(value.get("conversationId"), limit=2_000),
            sender=sender_values[0] if sender_values else EmailAddress("", "Unknown sender"),
            recipients=cls._graph_addresses(value.get("toRecipients")),
            cc=cls._graph_addresses(value.get("ccRecipients")),
            bcc=cls._graph_addresses(value.get("bccRecipients")),
            subject=_clean_text(value.get("subject") or "(No subject)", limit=1_000),
            received_at=_clean_text(value.get("receivedDateTime"), limit=100),
            unread=not bool(value.get("isRead", True)),
            importance=_clean_text(value.get("importance") or "normal", limit=50).lower(),
            folders=(_clean_text(value.get("parentFolderId"), limit=2_000),),
            preview=_clean_text(value.get("bodyPreview"), limit=2_000),
            has_attachments=bool(value.get("hasAttachments")),
        )

    def read_message(self, message_id: str) -> FullMessage:
        value = self._request(
            "GET",
            f"/me/messages/{quote(_message_id(message_id), safe='')}",
            params={
                "$select": (
                    "id,conversationId,subject,from,toRecipients,ccRecipients,bccRecipients,"
                    "receivedDateTime,isRead,importance,parentFolderId,bodyPreview,hasAttachments,body"
                ),
                "$expand": "attachments($select=id,name,contentType,size,isInline)",
            },
        )
        summary = self._summary(value, self.account())
        body_value = value.get("body") or {}
        content = _clean_text(body_value.get("content"), limit=MAX_BODY_BYTES)
        html_available = str(body_value.get("contentType", "")).lower() == "html"
        body = _html_to_text(content) if html_available else content
        attachments = tuple(
            AttachmentMetadata(
                _clean_text(item.get("id"), limit=2_000),
                _clean_text(item.get("name") or "Unnamed attachment", limit=1_000),
                _clean_text(item.get("contentType") or "application/octet-stream", limit=250),
                max(0, int(item.get("size") or 0)),
                bool(item.get("isInline")),
            )
            for item in value.get("attachments", [])
            if isinstance(item, dict)
        )
        return FullMessage(summary, body, html_available, attachments)

    def read_thread(self, message_id: str, *, limit: int = DEFAULT_RESULT_LIMIT) -> EmailThread:
        limit = _limit(limit)
        first = self.read_message(message_id)
        conversation_id = first.summary.conversation_id
        escaped = conversation_id.replace("'", "''")
        value = self._request("GET", "/me/messages", params={
            "$filter": f"conversationId eq '{escaped}'",
            "$top": str(limit),
            "$select": "id",
        })
        messages = tuple(
            self.read_message(str(item["id"]))
            for item in value.get("value", [])[:limit]
            if isinstance(item, dict) and item.get("id")
        )
        ordered = tuple(sorted(messages, key=lambda item: item.summary.received_at))
        return EmailThread(self.key, first.summary.account_id, conversation_id, ordered)


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Email result limit must be an integer.")
    if value < 1:
        raise ValueError("Email result limit must be at least 1.")
    return min(value, MAX_RESULT_LIMIT)


def _message_id(value: str) -> str:
    normalized = str(value).strip()
    if not normalized or len(normalized) > 2_000 or any(ord(char) < 32 for char in normalized):
        raise ValueError("Email message ID is invalid.")
    return normalized


class EmailService:
    """One provider-neutral read-only interface for all Orion surfaces."""

    name = "Email"

    def __init__(
        self,
        providers: Iterable[EmailProvider],
        *,
        default_provider: str = "",
        result_limit: int = DEFAULT_RESULT_LIMIT,
        summary_limit: int = DEFAULT_RESULT_LIMIT,
        cache_seconds: float = 300.0,
        provider_state_writer: Callable[[str, bool], None] | None = None,
        default_provider_writer: Callable[[str], None] | None = None,
        provider_config_writer: Callable[[str, str, object], None] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.providers = {provider.key: provider for provider in providers}
        self.default_provider = self.normalize_provider(default_provider, allow_empty=True)
        self.result_limit = _limit(result_limit)
        self.summary_limit = _limit(summary_limit)
        self.cache_seconds = max(0.0, min(float(cache_seconds), 3_600.0))
        self._provider_state_writer = provider_state_writer
        self._default_provider_writer = default_provider_writer
        self._provider_config_writer = provider_config_writer
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._accounts: dict[str, EmailAccount] = {}
        self._unread: dict[str, tuple[float, int]] = {}
        self._last_checked: dict[str, str] = {}
        self._last_error: dict[str, str] = {}

    @staticmethod
    def normalize_provider(value: str, *, allow_empty: bool = False) -> str:
        key = str(value or "").strip().lower().replace(" ", "")
        if not key and allow_empty:
            return ""
        normalized = EMAIL_PROVIDER_ALIASES.get(key)
        if not normalized:
            raise ValueError("Email provider must be gmail or microsoft.")
        return normalized

    def get_status(self) -> ServiceStatus:
        enabled = [provider for provider in self.providers.values() if provider.enabled]
        if not enabled:
            return ServiceStatus(ServiceState.UNAVAILABLE, "Email is not configured.")
        connected = [provider for provider in enabled if provider.adapter.connected]
        if not connected:
            return ServiceStatus(ServiceState.DEGRADED, "Email providers need authorization.")
        errors = [self._last_error.get(provider.key, "") for provider in connected]
        errors = [error for error in errors if error]
        if errors:
            return ServiceStatus(ServiceState.DEGRADED, errors[0])
        return ServiceStatus(
            ServiceState.AVAILABLE,
            f"Email ready: {', '.join(provider.display_name for provider in connected)}.",
        )

    def is_available(self) -> bool:
        return self.get_status().state is ServiceState.AVAILABLE

    def provider_statuses(self, *, refresh: bool = False) -> tuple[ProviderConnectionStatus, ...]:
        statuses = []
        for provider in self.providers.values():
            adapter = provider.adapter
            account = self._accounts.get(provider.key)
            error = self._last_error.get(provider.key, "")
            healthy = bool(provider.enabled and adapter.connected and not error)
            unread = self._cached_unread(provider.key)
            if refresh and provider.enabled and adapter.connected:
                try:
                    account = adapter.account()
                    self._accounts[provider.key] = account
                    unread = adapter.unread_count()
                    self._cache_unread(provider.key, unread)
                    self._record_success(provider.key)
                    error = ""
                    healthy = True
                except (EmailError, OSError, ValueError):
                    error = "Provider check failed. Reconnect the account or try again later."
                    self._last_error[provider.key] = error
                    healthy = False
            statuses.append(ProviderConnectionStatus(
                provider.key,
                provider.display_name,
                provider.enabled,
                adapter.configured,
                adapter.connected,
                healthy,
                account.email_address if account else "",
                ("mail_read",) if adapter.connected else (),
                unread,
                self._last_checked.get(provider.key, ""),
                error,
            ))
        return tuple(statuses)

    def provider_summary(self, *, refresh: bool = False) -> str:
        lines = ["Email Providers:"]
        for item in self.provider_statuses(refresh=refresh):
            state = "connected" if item.healthy else (
                "authorization problem" if item.connected else (
                    "configured" if item.configured else "not configured"
                )
            )
            if not item.enabled:
                state = "disabled"
            account = f" — {item.account}" if item.account else ""
            capability = "read-only" if "mail_read" in item.capabilities else "no mail access"
            unread = f", {item.unread_count} unread" if item.unread_count is not None else ""
            lines.append(f"  {item.provider}: {item.display_name} [{state}; {capability}{unread}]{account}")
            if item.error:
                lines.append(f"    Problem: {item.error}")
                lines.append(f"    Try: email connect {item.provider}")
        return "\n".join(lines)

    def connect(self, provider_key: str) -> EmailAccount:
        key = self.normalize_provider(provider_key)
        provider = self._provider(key)
        try:
            account = provider.adapter.connect()
        except (EmailError, OAuthCancelledError, OAuthScopeError, OAuthError):
            raise
        except Exception as exc:
            raise EmailError(f"{provider.display_name} connection failed.") from exc
        provider.enabled = True
        self._accounts[key] = account
        self._last_error.pop(key, None)
        self._record_success(key)
        if self._provider_state_writer:
            self._provider_state_writer(key, True)
        if not self.default_provider:
            self.set_default(key)
        return account

    def configure_provider(self, provider_key: str, **settings: object) -> None:
        key = self.normalize_provider(provider_key)
        provider = self._provider(key)
        allowed = {"credentials_path"} if key == "gmail" else {"client_id", "tenant"}
        unknown = set(settings) - allowed
        if unknown:
            raise ValueError(f"Unsupported {key} email setting: {sorted(unknown)[0]}")
        for field, raw in settings.items():
            value = str(raw).strip()
            if not value:
                raise ValueError(f"Email {field.replace('_', ' ')} cannot be empty.")
            if key == "gmail" and field == "credentials_path":
                provider.adapter.credentials_path = Path(value)
            elif key == "microsoft" and field == "client_id":
                provider.adapter.client_id = value
            elif key == "microsoft" and field == "tenant":
                provider.adapter.oauth.tenant = value
            if self._provider_config_writer:
                self._provider_config_writer(key, field, value)

    def disconnect(self, provider_key: str) -> None:
        key = self.normalize_provider(provider_key)
        provider = self._provider(key)
        provider.adapter.disconnect()
        provider.enabled = False
        self._accounts.pop(key, None)
        self._unread.pop(key, None)
        self._last_checked.pop(key, None)
        self._last_error.pop(key, None)
        if self._provider_state_writer:
            self._provider_state_writer(key, False)
        if self.default_provider == key:
            remaining = next((item.key for item in self.providers.values() if item.enabled), "")
            self.set_default(remaining)

    def set_default(self, provider_key: str) -> None:
        key = self.normalize_provider(provider_key, allow_empty=True)
        if key and key not in self.providers:
            raise EmailProviderUnavailable(f"Email provider is not registered: {key}")
        self.default_provider = key
        if self._default_provider_writer:
            self._default_provider_writer(key)

    def accounts(self, *, refresh: bool = True) -> tuple[EmailAccount, ...]:
        accounts = []
        for provider in self._selected(""):
            try:
                account = provider.adapter.account() if refresh else self._accounts.get(provider.key)
                if account:
                    self._accounts[provider.key] = account
                    accounts.append(account)
                    self._record_success(provider.key)
            except EmailError as exc:
                self._record_error(provider.key, exc)
        return tuple(accounts)

    def inbox(self, provider_key: str = "", *, limit: int | None = None,
              page_token: str = "") -> MessagePage:
        return self._list(provider_key, limit=limit, page_token=page_token)

    def unread(self, provider_key: str = "", *, limit: int | None = None,
               page_token: str = "") -> MessagePage:
        return self._list(provider_key, unread=True, limit=limit, page_token=page_token)

    def search(self, query: str, provider_key: str = "", *, limit: int | None = None,
               page_token: str = "") -> MessagePage:
        normalized = str(query).strip()
        if not normalized:
            raise ValueError("Email search query cannot be empty.")
        return self._list(
            provider_key,
            query=normalized[:500],
            limit=limit,
            page_token=page_token,
        )

    def _list(self, provider_key: str, *, query: str = "", unread: bool = False,
              limit: int | None = None, page_token: str = "") -> MessagePage:
        selected = self._selected(provider_key, one=bool(page_token))
        bounded = _limit(limit if limit is not None else self.result_limit)
        messages: list[MessageSummary] = []
        next_token = ""
        errors = []
        for provider in selected:
            try:
                page = provider.adapter.list_messages(
                    query=query,
                    unread=unread,
                    limit=bounded,
                    page_token=page_token,
                )
                messages.extend(page.messages)
                if len(selected) == 1:
                    next_token = page.next_page_token
                self._record_success(provider.key)
            except EmailError as exc:
                self._record_error(provider.key, exc)
                errors.append(f"{provider.display_name} is unavailable.")
        if not messages and errors:
            raise EmailError(" ".join(errors))
        messages.sort(key=lambda item: item.received_at, reverse=True)
        return MessagePage(tuple(messages[:bounded]), next_token)

    def read(self, reference: str, provider_key: str = "") -> FullMessage:
        provider, message_id = self._reference(reference, provider_key)
        try:
            result = provider.adapter.read_message(message_id)
            self._record_success(provider.key)
            return result
        except EmailError as exc:
            self._record_error(provider.key, exc)
            raise

    def thread(self, reference: str, provider_key: str = "", *, limit: int | None = None) -> EmailThread:
        provider, message_id = self._reference(reference, provider_key)
        try:
            result = provider.adapter.read_thread(
                message_id,
                limit=_limit(limit if limit is not None else self.result_limit),
            )
            self._record_success(provider.key)
            return result
        except EmailError as exc:
            self._record_error(provider.key, exc)
            raise

    def unread_count(self, provider_key: str = "", *, refresh: bool = False) -> int:
        total = 0
        errors = []
        for provider in self._selected(provider_key):
            cached = None if refresh else self._cached_unread(provider.key)
            if cached is not None:
                total += cached
                continue
            try:
                value = provider.adapter.unread_count()
                self._cache_unread(provider.key, value)
                self._record_success(provider.key)
                total += value
            except EmailError as exc:
                self._record_error(provider.key, exc)
                errors.append(provider.display_name)
        if errors and not total:
            raise EmailError(f"Unread mail could not be checked for: {', '.join(errors)}.")
        return total

    def summarize(self, provider_key: str = "", *, unread_only: bool = True,
                  important_only: bool = False) -> str:
        page = self.unread(provider_key, limit=self.summary_limit) if unread_only else self.inbox(
            provider_key, limit=self.summary_limit
        )
        messages = page.messages
        if important_only:
            messages = tuple(item for item in messages if item.importance == "high")
        if not messages:
            qualifier = "explicitly high-importance unread" if important_only else (
                "unread" if unread_only else "recent"
            )
            return f"No {qualifier} email was found in the bounded check."
        qualifier = "explicitly high-importance unread" if important_only else (
            "unread" if unread_only else "recent"
        )
        lines = [f"{len(messages)} {qualifier} message{'s' if len(messages) != 1 else ''}:"]
        for item in messages:
            signal = " [provider marked high importance]" if item.importance == "high" else ""
            lines.append(
                f"- [{item.provider}] {redact_email_text(item.subject, limit=1000)} — "
                f"{item.sender.address or item.sender.name}{signal}"
            )
            if item.preview:
                lines.append(f"  {redact_email_text(item.preview, limit=240)}")
        lines.append(
            f"Checked at most {self.summary_limit} relevant messages; attachments were not downloaded."
        )
        return "\n".join(lines)

    def handle_request(self, request: str) -> ServiceResult:
        value = request.strip()
        lowered = value.lower()
        try:
            if "latest message" in lowered and "thread" in lowered:
                match = re.search(r"\b(gmail|google|microsoft|outlook|m365):([^\s]+)", value, re.I)
                if not match:
                    return ServiceResult(
                        False,
                        error="Include a provider-qualified message ID, such as gmail:<message-id>.",
                    )
                reference = f"{match.group(1)}:{match.group(2).rstrip('.,?!;')}"
                thread = self.thread(reference, limit=self.summary_limit)
                if not thread.messages:
                    return ServiceResult(True, "No messages were found in that bounded thread.")
                latest = thread.messages[-1]
                sender = latest.summary.sender.address or latest.summary.sender.name
                excerpt = redact_email_text(
                    latest.body_text or latest.summary.preview,
                    limit=500,
                )
                return ServiceResult(
                    True,
                    f"Latest message from {sender}: "
                    f"{redact_email_text(latest.summary.subject, limit=1000)}\n{excerpt}",
                )
            if "important" in lowered and "unread" in lowered:
                return ServiceResult(True, self.summarize(important_only=True))
            if "summar" in lowered and ("email" in lowered or "mail" in lowered):
                return ServiceResult(True, self.summarize(unread_only="unread" in lowered))
            if lowered.startswith("find ") or "find the email" in lowered or "email from" in lowered:
                query = re.sub(r"^(ask\s+)?(orion\s+)?(find\s+)?(the\s+)?(email|mail)\s*", "", value, flags=re.I)
                page = self.search(query or value, limit=self.summary_limit)
                if not page.messages:
                    return ServiceResult(True, "No matching email was found in the bounded search.")
                lines = ["Matching email:"]
                lines.extend(
                    f"- {redact_email_text(item.subject, limit=1000)} — "
                    f"{item.sender.address or item.sender.name} [{item.reference}]"
                    for item in page.messages
                )
                return ServiceResult(True, "\n".join(lines))
            return ServiceResult(True, self.summarize(unread_only=True))
        except (EmailError, ValueError) as exc:
            return ServiceResult(False, error=str(exc))

    def _provider(self, key: str) -> EmailProvider:
        try:
            return self.providers[key]
        except KeyError as exc:
            raise EmailProviderUnavailable(f"Email provider is not registered: {key}") from exc

    def _selected(self, provider_key: str, *, one: bool = False) -> tuple[EmailProvider, ...]:
        if provider_key:
            provider = self._provider(self.normalize_provider(provider_key))
            if not provider.enabled or not provider.adapter.connected:
                raise EmailProviderUnavailable(
                    f"{provider.display_name} is not connected. Run 'email connect {provider.key}'."
                )
            return (provider,)
        available = tuple(
            provider for provider in self.providers.values()
            if provider.enabled and provider.adapter.connected
        )
        if not available:
            raise EmailProviderUnavailable(
                "No email provider is connected. Run 'email connect gmail' or "
                "'email connect microsoft'."
            )
        if one:
            if self.default_provider:
                default = next((item for item in available if item.key == self.default_provider), None)
                if default:
                    return (default,)
            if len(available) != 1:
                raise EmailProviderUnavailable("Choose an email provider for this operation.")
        return available

    def _reference(self, reference: str, provider_key: str) -> tuple[EmailProvider, str]:
        raw = str(reference).strip()
        if ":" in raw:
            prefix, raw_id = raw.split(":", 1)
            normalized = EMAIL_PROVIDER_ALIASES.get(prefix.lower())
            if normalized:
                return self._selected(normalized, one=True)[0], _message_id(raw_id)
        selected = self._selected(provider_key, one=True)
        return selected[0], _message_id(raw)

    def _record_success(self, provider_key: str) -> None:
        self._last_checked[provider_key] = self._now().astimezone(timezone.utc).isoformat(timespec="seconds")
        self._last_error.pop(provider_key, None)

    def _record_error(self, provider_key: str, _error: Exception) -> None:
        self._last_error[provider_key] = "Provider request failed. Reconnect the account or try again later."

    def _cache_unread(self, provider_key: str, value: int) -> None:
        self._unread[provider_key] = (monotonic(), max(0, int(value)))

    def _cached_unread(self, provider_key: str) -> int | None:
        item = self._unread.get(provider_key)
        if item is None or monotonic() - item[0] > self.cache_seconds:
            return None
        return item[1]


class EmailBriefingProvider:
    name = "Email"

    def __init__(self, service: EmailService) -> None:
        self.service = service

    def get_briefing(self) -> tuple[BriefingItem, ...]:
        if self.service.get_status().state is ServiceState.UNAVAILABLE:
            return ()
        statuses = tuple(
            item for item in self.service.provider_statuses(refresh=False)
            if item.enabled and item.connected
        )
        if not statuses:
            return ()
        known = [item.unread_count for item in statuses if item.unread_count is not None]
        if known:
            unread = sum(known)
            message = f"{unread} unread message{'s' if unread != 1 else ''}"
            if len(known) != len(statuses):
                message += " across recently checked accounts"
            priority = BriefingPriority.IMPORTANT if unread else BriefingPriority.INFORMATIONAL
        elif any(item.error for item in statuses):
            message = "Connection problem — run 'email status'"
            priority = BriefingPriority.IMPORTANT
        else:
            message = (
                f"{len(statuses)} account{'s' if len(statuses) != 1 else ''} connected; "
                "run 'email status' to refresh unread mail"
            )
            priority = BriefingPriority.INFORMATIONAL
        return (BriefingItem("Email", message, priority=priority, source=self.name, icon="[MAIL]"),)


def build_email_service(config, paths) -> EmailService:
    """Build the canonical EmailService for Orion runtime and First Contact."""
    legacy_enabled = bool(config.get("email.enabled", False))
    legacy_provider = str(config.get("email.provider", "")).lower()

    configured_google = str(config.get("email.gmail.credentials_path", "")).strip()
    legacy_google = str(config.get("connect.gmail.credentials_path", "")).strip()
    conventional_google = Path("config/google-gmail-credentials.json")
    calendar_google = str(config.get(
        "calendar.google.credentials_path", "config/google-oauth-credentials.json"
    )).strip()
    google_credentials = (
        configured_google
        or legacy_google
        or (str(conventional_google) if conventional_google.is_file() else calendar_google)
    )
    gmail_token = paths.user_file(
        config.get(
            "email.gmail.token_path",
            config.get("connect.gmail.token_path", "google-gmail-token.json"),
        ),
        category="tokens",
    )

    microsoft_client_id = config.get(
        "email.microsoft.client_id",
        config.get("calendar.microsoft.client_id", ""),
    )
    microsoft_tenant = config.get(
        "email.microsoft.tenant",
        config.get("calendar.microsoft.tenant", "common"),
    )
    microsoft_token = paths.user_file(
        config.get("email.microsoft.token_path", "microsoft-mail-token.json"),
        category="tokens",
    )

    providers = (
        EmailProvider(
            "gmail",
            config.get("email.gmail.name", "Gmail"),
            GmailAdapter(google_credentials, gmail_token),
            bool(config.get("email.gmail.enabled", legacy_enabled and "gmail" in legacy_provider)),
        ),
        EmailProvider(
            "microsoft",
            config.get("email.microsoft.name", "Outlook / Microsoft 365"),
            MicrosoftGraphEmailAdapter(
                microsoft_client_id,
                microsoft_token,
                tenant=microsoft_tenant,
                timeout=float(config.get("email.microsoft.timeout_seconds", 10.0)),
            ),
            bool(config.get(
                "email.microsoft.enabled",
                legacy_enabled and ("outlook" in legacy_provider or "microsoft" in legacy_provider),
            )),
        ),
    )

    def save_provider(key: str, enabled: bool) -> None:
        config.set(f"email.{key}.enabled", enabled)
        config.set("email.enabled", any(
            enabled if provider.key == key else provider.enabled
            for provider in providers
        ))
        config.save()

    def save_default(key: str) -> None:
        config.set("email.default_provider", key)
        config.save()

    def save_config(key: str, field: str, value: object) -> None:
        config.set(f"email.{key}.{field}", value)
        config.save()

    return EmailService(
        providers,
        default_provider=config.get("email.default_provider", ""),
        result_limit=int(config.get("email.result_limit", DEFAULT_RESULT_LIMIT)),
        summary_limit=int(config.get("email.summary_limit", DEFAULT_RESULT_LIMIT)),
        cache_seconds=float(config.get("email.cache_seconds", 300.0)),
        provider_state_writer=save_provider,
        default_provider_writer=save_default,
        provider_config_writer=save_config,
    )
