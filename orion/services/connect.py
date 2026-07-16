"""Orion Connect Center: unified communication services."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from orion.services.briefing import BriefingItem, BriefingPriority


@dataclass(frozen=True)
class ConnectStatus:
    key: str
    name: str
    configured: bool
    healthy: bool
    detail: str


@dataclass(frozen=True)
class MailSummary:
    id: str
    subject: str
    sender: str
    snippet: str
    unread: bool = False


class GmailClient:
    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]

    def __init__(self, credentials_path: str, token_path: str):
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)

    def _service(self, *, interactive: bool = False):
        try:
            from google.auth.transport.requests import Request as GoogleRequest
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError("Google Gmail libraries are not installed. Run: pip install -r requirements.txt") from exc

        creds = None
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), self.SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
        if not creds or not creds.valid:
            if not interactive:
                raise ConnectionError("Gmail is not connected")
            if not self.credentials_path.exists():
                raise FileNotFoundError(
                    f"Google OAuth credentials not found: {self.credentials_path}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), self.SCOPES)
            creds = flow.run_local_server(port=0)
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def connect(self) -> None:
        self._service(interactive=True).users().getProfile(userId="me").execute()

    def profile(self) -> dict:
        return self._service().users().getProfile(userId="me").execute()

    def unread_count(self) -> int:
        response = self._service().users().labels().get(userId="me", id="INBOX").execute()
        return int(response.get("messagesUnread", 0))

    def list_messages(self, query: str = "in:inbox", limit: int = 10) -> list[MailSummary]:
        service = self._service()
        response = service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        results: list[MailSummary] = []
        for item in response.get("messages", []):
            message = service.users().messages().get(
                userId="me", id=item["id"], format="metadata", metadataHeaders=["Subject", "From"]
            ).execute()
            headers = {h.get("name", "").lower(): h.get("value", "") for h in message.get("payload", {}).get("headers", [])}
            labels = set(message.get("labelIds", []))
            results.append(MailSummary(
                id=message["id"], subject=headers.get("subject", "(No subject)"),
                sender=headers.get("from", "Unknown sender"), snippet=message.get("snippet", ""),
                unread="UNREAD" in labels,
            ))
        return results

    def read_message(self, message_id: str) -> MailSummary:
        message = self._service().users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = {h.get("name", "").lower(): h.get("value", "") for h in message.get("payload", {}).get("headers", [])}
        return MailSummary(message["id"], headers.get("subject", "(No subject)"), headers.get("from", "Unknown sender"), message.get("snippet", ""), "UNREAD" in set(message.get("labelIds", [])))

    def send(self, to: str, subject: str, body: str) -> str:
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        result = self._service().users().messages().send(userId="me", body={"raw": encoded}).execute()
        return str(result.get("id", "sent"))


class DiscordWebhookClient:
    USER_AGENT = "Orion-Connect/0.4.2.1 (+https://github.com/)"

    def __init__(self, webhook_url: str = "", timeout: float = 10.0):
        self.webhook_url = webhook_url
        self.timeout = timeout

    def health(self) -> dict:
        if not self.webhook_url:
            raise ConnectionError("Discord webhook is not configured")
        request = Request(
            self.webhook_url,
            headers={
                "Accept": "application/json",
                "User-Agent": self.USER_AGENT,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {401, 403, 404}:
                raise ConnectionError(
                    "Discord rejected the webhook. Regenerate the webhook URL and try again."
                ) from exc
            raise

    def send(self, content: str) -> None:
        if not self.webhook_url:
            raise ConnectionError("Discord webhook is not configured")
        payload = json.dumps({"content": content}).encode("utf-8")
        request = Request(
            self.webhook_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": self.USER_AGENT,
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout):
            return None


class ConnectService:
    def __init__(
        self,
        gmail: GmailClient,
        discord: DiscordWebhookClient,
        vault=None,
        config_manager=None,
        discord_bot_runtime=None,
    ):
        self.gmail = gmail
        self.discord = discord
        self.vault = vault
        self.config_manager = config_manager
        self.discord_bot_runtime = discord_bot_runtime

    def _discord_bot_status(self) -> ConnectStatus | None:
        """Return Discord bot status when the gateway integration is configured.

        The older Connect Center only inspected the webhook client, while Orion's
        two-way Discord interface stores its token as ``discord_bot`` and its
        settings under ``connect.discord_bot``. Prefer the bot integration when
        present, but retain webhook compatibility for existing installations.
        """
        token = ""
        if self.vault is not None:
            token = str(self.vault.store.get("discord_bot") or "")

        enabled = False
        owners = []
        channels = []
        if self.config_manager is not None:
            enabled = bool(self.config_manager.get("connect.discord_bot.enabled", False))
            owners = self.config_manager.get(
                "connect.discord_bot.owner_user_ids",
                self.config_manager.get("connect.discord_bot.allowed_user_ids", []),
            ) or []
            channels = self.config_manager.get("connect.discord_bot.allowed_channel_ids", []) or []

        configured = bool(token) or bool(owners) or bool(channels)
        if not configured:
            return None

        runtime = self.discord_bot_runtime() if callable(self.discord_bot_runtime) else None
        if runtime is not None:
            diagnostics = getattr(runtime, "diagnostics", None)
            bot_name = getattr(diagnostics, "bot_name", "") if diagnostics else ""
            detail = f"Bot online as {bot_name}" if bot_name else "Bot interface online"
            return ConnectStatus("discord", "Discord", True, True, detail)

        if enabled:
            return ConnectStatus(
                "discord", "Discord", True, False,
                "Bot configured; restart Orion to start the interface",
            )
        return ConnectStatus("discord", "Discord", True, False, "Bot configured but disabled")

    def statuses(self) -> list[ConnectStatus]:
        statuses: list[ConnectStatus] = []
        try:
            profile = self.gmail.profile()
            statuses.append(ConnectStatus("gmail", "Gmail", True, True, profile.get("emailAddress", "Connected")))
        except Exception as exc:
            statuses.append(ConnectStatus("gmail", "Gmail", self.gmail.token_path.exists(), False, str(exc)))
        bot_status = self._discord_bot_status()
        if bot_status is not None:
            statuses.append(bot_status)
        else:
            configured = bool(self.discord.webhook_url)
            if configured:
                try:
                    info = self.discord.health()
                    statuses.append(ConnectStatus("discord", "Discord", True, True, info.get("name") or "Webhook connected"))
                except Exception as exc:
                    statuses.append(ConnectStatus("discord", "Discord", True, False, str(exc)))
            else:
                statuses.append(ConnectStatus("discord", "Discord", False, False, "Not configured"))
        return statuses

    def unread_count(self) -> int:
        return self.gmail.unread_count()


class ConnectBriefingProvider:
    name = "Connect"

    def __init__(self, service: ConnectService):
        self.service = service

    def get_briefing(self):
        try:
            unread = self.service.unread_count()
            message = f"{unread} unread email" + ("s" if unread != 1 else "")
            priority = BriefingPriority.IMPORTANT if unread else BriefingPriority.INFORMATIONAL
            return (BriefingItem("Connect", message, priority=priority, source=self.name, icon="[MSG]"),)
        except Exception:
            connected = sum(1 for item in self.service.statuses() if item.healthy)
            if connected:
                return (BriefingItem("Connect", f"{connected} service(s) online", source=self.name, icon="[MSG]"),)
            return ()
