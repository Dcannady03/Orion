"""Orion Connect Center: unified communication services."""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from orion.services.briefing import BriefingItem, BriefingPriority
from orion.services.email import EmailService, GmailAdapter


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


# Backward-compatible import name. The adapter is now read-only and all normal
# application access goes through EmailService.
GmailClient = GmailAdapter


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
        gmail: GmailClient | EmailService,
        discord: DiscordWebhookClient,
        vault=None,
        config_manager=None,
        discord_bot_runtime=None,
    ):
        self.email_service = gmail if isinstance(gmail, EmailService) else None
        self.gmail = None if self.email_service is not None else gmail
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

    def statuses(self, *, refresh_email: bool = False) -> list[ConnectStatus]:
        statuses: list[ConnectStatus] = []
        if self.email_service is not None:
            for item in self.email_service.provider_statuses(refresh=refresh_email):
                capability = "read-only" if "mail_read" in item.capabilities else "needs mail authorization"
                detail = item.account or item.error or capability
                if item.account:
                    detail = f"{item.account} ({capability})"
                if item.unread_count is not None:
                    detail += f"; {item.unread_count} unread"
                if item.last_checked:
                    detail += f"; checked {item.last_checked}"
                statuses.append(ConnectStatus(
                    item.provider,
                    item.display_name,
                    item.configured or item.connected,
                    item.healthy,
                    detail,
                ))
        else:
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
        if self.email_service is not None:
            return self.email_service.unread_count(refresh=False)
        return self.gmail.unread_count()


class ConnectBriefingProvider:
    name = "Connect"

    def __init__(self, service: ConnectService):
        self.service = service

    def get_briefing(self):
        if self.service.email_service is not None:
            connected = sum(
                1 for item in self.service.statuses()
                if item.key == "discord" and item.healthy
            )
            if connected:
                return (BriefingItem("Connect", "Discord online", source=self.name, icon="[MSG]"),)
            return ()
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
