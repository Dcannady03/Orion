"""Two-way Discord interface for Orion."""
from __future__ import annotations

import asyncio
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable


SENSITIVE_TERMS = (
    "run command", "shell", "powershell", "cmd.exe", "terminal",
    "delete ", "remove file", "modify file", "write file", "edit file",
    "git push", "git commit", "git reset", "git checkout", "git merge",
    "docker start", "docker stop", "docker restart", "docker remove",
    "send email", "email ", "change model", "switch model", "change provider",
    "vault ", "install ", "uninstall ", "shutdown", "restart computer",
)


@dataclass(frozen=True)
class DiscordAccessPolicy:
    """Authorization policy for Discord as an Orion interface.

    ``owner_user_ids`` identify people who may request protected actions.
    When ``allow_channel_members`` is true, any human member in an allowed
    server channel may have a normal conversation with Orion. DMs always
    remain owner-only.
    """

    owner_user_ids: frozenset[int]
    allowed_channel_ids: frozenset[int] = frozenset()
    allowed_role_ids: frozenset[int] = frozenset()
    allow_channel_members: bool = False

    @staticmethod
    def _parse_ids(values: Iterable[object]) -> frozenset[int]:
        ids: set[int] = set()
        for value in values or ():
            try:
                ids.add(int(str(value).strip()))
            except (TypeError, ValueError):
                continue
        return frozenset(ids)

    @classmethod
    def from_values(
        cls,
        values: Iterable[object],
        channel_values: Iterable[object] = (),
        role_values: Iterable[object] = (),
        allow_channel_members: bool = False,
    ) -> "DiscordAccessPolicy":
        return cls(
            cls._parse_ids(values),
            cls._parse_ids(channel_values),
            cls._parse_ids(role_values),
            bool(allow_channel_members),
        )

    # Backward-compatible alias used by older tests/callers.
    @property
    def allowed_user_ids(self) -> frozenset[int]:
        return self.owner_user_ids

    def is_owner(self, user_id: int) -> bool:
        return int(user_id) in self.owner_user_ids

    def permits(self, user_id: int) -> bool:
        """Legacy owner-only permission check."""
        return self.is_owner(user_id)

    def permits_server_context(
        self,
        channel_id: int,
        role_ids: Iterable[int] = (),
    ) -> bool:
        if self.allowed_channel_ids and int(channel_id) not in self.allowed_channel_ids:
            return False
        if self.allowed_role_ids:
            member_roles = {int(role_id) for role_id in role_ids}
            if not member_roles.intersection(self.allowed_role_ids):
                return False
        return True

    def permits_conversation(
        self,
        user_id: int,
        *,
        is_dm: bool,
        channel_id: int | None = None,
        role_ids: Iterable[int] = (),
    ) -> bool:
        if is_dm:
            return self.is_owner(user_id)
        if channel_id is None or not self.permits_server_context(channel_id, role_ids):
            return False
        return self.allow_channel_members or self.is_owner(user_id)


@dataclass
class DiscordRuntimeDiagnostics:
    state: str = "Offline"
    bot_name: str = ""
    guilds: list[str] = field(default_factory=list)
    watching: list[str] = field(default_factory=list)
    messages_received: int = 0
    replies_sent: int = 0
    ignored: int = 0
    last_user_id: int | None = None
    last_channel_id: int | None = None
    last_request: str | None = None
    last_route: str | None = None
    last_error: str | None = None
    last_ignore_reason: str | None = None


@dataclass(frozen=True)
class DiscordImageIntent:
    prompt: str
    dedicated: bool = False


class DiscordImageIntentDetector:
    """Recognize only explicit generation language without an AI classification call."""

    GENERATE = re.compile(
        r"^(?:please\s+)?(?:create|generate|make|render)\s+(?:me\s+)?"
        r"(?:an?\s+)?(?:image|picture|artwork|illustration|drawing)\s*"
        r"(?:of\s+)?(?P<prompt>.*)$",
        re.IGNORECASE,
    )
    DRAW = re.compile(r"^(?:please\s+)?draw\s+(?:me\s+)?(?P<prompt>.*)$", re.IGNORECASE)

    @classmethod
    def detect(cls, text: str) -> DiscordImageIntent | None:
        value = str(text).strip()
        if value.casefold().startswith("!image"):
            boundary = value[6:7]
            if boundary and not boundary.isspace():
                return None
            return DiscordImageIntent(value[6:].strip(), True)
        for pattern in (cls.GENERATE, cls.DRAW):
            matched = pattern.fullmatch(value)
            if matched:
                return DiscordImageIntent(matched.group("prompt").strip(), False)
        return None


@dataclass(frozen=True)
class DiscordImagePolicy:
    enabled: bool = False
    allowed_user_ids: frozenset[int] = frozenset()
    cooldown_seconds: float = 30.0
    max_prompt_chars: int = 1_000
    max_upload_bytes: int = 8_388_608
    max_concurrent_channels: int = 2

    @classmethod
    def from_values(
        cls,
        enabled: bool = False,
        allowed_user_ids: Iterable[object] = (),
        cooldown_seconds: object = 30,
        max_prompt_chars: object = 1_000,
        max_upload_bytes: object = 8_388_608,
        max_concurrent_channels: object = 2,
    ) -> "DiscordImagePolicy":
        try:
            cooldown = max(0.0, min(float(cooldown_seconds), 86_400.0))
            prompt_limit = max(1, min(int(max_prompt_chars), 4_000))
            upload_limit = max(1, min(int(max_upload_bytes), 100_000_000))
            concurrent = max(1, min(int(max_concurrent_channels), 16))
        except (TypeError, ValueError) as exc:
            raise ValueError("Discord image-generation limits are invalid.") from exc
        return cls(
            bool(enabled), DiscordAccessPolicy._parse_ids(allowed_user_ids), cooldown,
            prompt_limit, upload_limit, concurrent,
        )

    def permits(self, user_id: int) -> bool:
        return int(user_id) in self.allowed_user_ids


class DiscordMessageHandler:
    """Provider-independent message handling, kept testable without discord.py."""

    def __init__(
        self,
        ask: Callable[[str], str],
        policy: DiscordAccessPolicy,
        route_source: Callable[[], str | None] | None = None,
    ):
        self.ask = ask
        self.policy = policy
        self.route_source = route_source
        self._lock = threading.Lock()

    @staticmethod
    def is_sensitive(text: str) -> bool:
        value = text.strip().lower()
        return any(term in value for term in SENSITIVE_TERMS)

    @staticmethod
    def normalize(text: str) -> str:
        prompt = text.strip()
        if prompt.lower().startswith("ask "):
            prompt = prompt[4:].strip()
        return prompt

    def handle(self, user_id: int, text: str, *, enforce_owner: bool = True) -> str:
        prompt = self.normalize(text)
        if not prompt:
            return "I'm online. What can I help you with?"
        # Preserve the legacy owner-only handler contract. Channel-wide access is
        # decided by DiscordBotInterface before invoking this method.
        if enforce_owner and not self.policy.allow_channel_members and not self.policy.is_owner(user_id):
            return "This Discord account is not authorized to use Orion."
        if enforce_owner and self.is_sensitive(prompt) and not self.policy.is_owner(user_id):
            return (
                "That request could affect Daniel's computer or connected accounts. "
                "Only an Orion owner can authorize sensitive actions."
            )
        with self._lock:
            response = self.ask(prompt)
        return response or "I didn't receive a response from the active AI provider."


class DiscordBotInterface:
    """Runs a discord.py client in a background thread beside Orion's CLI."""

    def __init__(
        self,
        orion,
        token: str,
        allowed_user_ids: Iterable[object],
        allowed_channel_ids: Iterable[object] = (),
        allowed_role_ids: Iterable[object] = (),
        allow_channel_members: bool = False,
        image_service=None,
        image_policy: DiscordImagePolicy | None = None,
        monotonic=None,
    ):
        self.orion = orion
        self.token = token.strip()
        self.policy = DiscordAccessPolicy.from_values(
            allowed_user_ids,
            allowed_channel_ids,
            allowed_role_ids,
            allow_channel_members,
        )
        self._last_route: str | None = None
        self.image_service = image_service
        self.image_policy = image_policy or DiscordImagePolicy()
        self._monotonic = monotonic or time.monotonic
        self._image_lock = threading.Lock()
        self._image_channels: set[int] = set()
        self._image_cooldowns: dict[tuple[int, int], float] = {}

        def route(prompt: str) -> str:
            result = orion.request_router.route(prompt)
            self._last_route = result.source
            return result.output

        self.handler = DiscordMessageHandler(route, self.policy, lambda: self._last_route)
        self.thread: threading.Thread | None = None
        self.error: str = ""
        self.diagnostics = DiscordRuntimeDiagnostics()

    @staticmethod
    def _image_error_label(category: str) -> str:
        return {
            "credential_missing": "Provider Unavailable",
            "client_missing": "Provider Unavailable",
            "provider_unavailable": "Provider Unavailable",
            "rate_limited": "Rate Limited",
            "timed_out": "Timed Out",
            "invalid_response": "Invalid Response",
            "response_too_large": "Invalid Response",
            "content_rejected": "Request Rejected",
            "disabled": "Disabled",
        }.get(category, "Generation Failed")

    async def handle_image_request(self, message, text: str, discord_module) -> bool:
        """Generate and attach one image after normal Discord inbound checks pass."""
        intent = DiscordImageIntentDetector.detect(text)
        if intent is None:
            return False
        async def reply(content: str, **kwargs) -> None:
            await message.reply(content, mention_author=False, **kwargs)
            self.diagnostics.replies_sent += 1
        policy = self.image_policy
        user_id = int(message.author.id)
        channel_id = int(getattr(message.channel, "id", 0) or 0)
        guild_id = int(getattr(getattr(message, "guild", None), "id", 0) or 0)
        if message.guild is None:
            await reply("Image generation is not enabled in direct messages.")
            return True
        if not policy.enabled or self.image_service is None:
            await reply("Discord image generation is disabled.")
            return True
        if not policy.permits(user_id):
            await reply("This Discord account is not approved for image generation.")
            return True
        if not intent.prompt:
            await reply("Usage: !image <prompt>")
            return True
        if len(intent.prompt) > policy.max_prompt_chars:
            await reply(f"Image prompt exceeds the {policy.max_prompt_chars}-character Discord limit.")
            return True
        key = (user_id, channel_id)
        now = self._monotonic()
        with self._image_lock:
            previous = self._image_cooldowns.get(key, -1e30)
            if now - previous < policy.cooldown_seconds:
                remaining = max(1, int(policy.cooldown_seconds - (now - previous)))
                cooldown_message = f"Image generation cooldown is active. Try again in {remaining} seconds."
            elif channel_id in self._image_channels or len(self._image_channels) >= policy.max_concurrent_channels:
                cooldown_message = "An image generation is already active for this channel or the global Discord limit is busy."
            else:
                cooldown_message = ""
                self._image_cooldowns[key] = now
                self._image_channels.add(channel_id)
        if cooldown_message:
            await reply(cooldown_message)
            return True
        try:
            await reply("Generating an image...")
            result = await asyncio.to_thread(
                self.image_service.generate,
                intent.prompt,
                source_interface="discord",
                discord_user_id=str(user_id),
                discord_guild_id=str(guild_id),
                discord_channel_id=str(channel_id),
            )
            if result.status != "succeeded" or not result.artifacts:
                label = self._image_error_label(result.safe_error_category)
                await reply(f"Image generation could not be completed: {label}.")
                return True
            artifact = result.artifacts[0]
            if artifact.byte_size > policy.max_upload_bytes:
                self.image_service.record_delivery(
                    result.image_id, succeeded=False, byte_size=artifact.byte_size,
                    category="upload_too_large",
                )
                await reply(f"Image {result.image_id} was generated, but it exceeds the Discord upload limit.")
                return True
            path = self.image_service.store.artifact_path(artifact)
            attachment = discord_module.File(str(path), filename=artifact.filename)
            caption = (
                "Generated by Orion\n"
                f"Provider: {result.resolved_provider.title()}\n"
                f"Image ID: {result.image_id}"
            )
            await reply(caption, file=attachment)
            self.image_service.record_delivery(
                result.image_id, succeeded=True, byte_size=artifact.byte_size,
            )
            return True
        except Exception:
            await reply("Image generation could not be completed: Generation Failed.")
            return True
        finally:
            with self._image_lock:
                self._image_channels.discard(channel_id)

    def start(self) -> None:
        if not self.token:
            raise ValueError("Discord bot token is not configured")
        if not self.policy.owner_user_ids and not self.policy.allow_channel_members:
            raise ValueError("No Discord owners are configured")
        if self.policy.allow_channel_members and not self.policy.allowed_channel_ids:
            raise ValueError("Channel-wide access requires at least one allowed channel ID")
        try:
            import discord  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Discord bot support requires: pip install discord.py") from exc

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        interface = self
        self.diagnostics.state = "Connecting"
        print("[INFO] Connecting Orion to Discord...")

        @client.event
        async def on_ready():
            interface.diagnostics.state = "Online"
            interface.diagnostics.bot_name = str(client.user or "Orion")
            interface.diagnostics.guilds = [guild.name for guild in client.guilds]
            watching: list[str] = []
            for guild in client.guilds:
                for channel in getattr(guild, "text_channels", ()):
                    if not interface.policy.allowed_channel_ids or channel.id in interface.policy.allowed_channel_ids:
                        watching.append(f"{guild.name} / #{channel.name}")
            interface.diagnostics.watching = watching
            print(f"[OK] Discord interface online as {client.user}")
            access = "Anyone in allowed channels" if interface.policy.allow_channel_members else "Owners only"
            print(f"     Access: {access}")
            if watching:
                print(f"     Watching: {', '.join(watching)}")
            print(f"     Owners: {len(interface.policy.owner_user_ids)} approved owner(s)")

        @client.event
        async def on_message(message):
            d = interface.diagnostics
            d.messages_received += 1
            d.last_user_id = getattr(message.author, "id", None)
            d.last_channel_id = getattr(message.channel, "id", None)
            d.last_ignore_reason = None

            if message.author.bot:
                d.ignored += 1
                d.last_ignore_reason = "Message authored by a bot"
                return

            is_dm = message.guild is None
            channel_id = getattr(message.channel, "id", None)
            role_ids = [role.id for role in getattr(message.author, "roles", ())]

            # An explicitly approved Orion channel acts as a dedicated chat room:
            # every human message in that channel may be routed without requiring
            # an @Orion mention. In other server channels, a direct bot mention is
            # still required.
            dedicated_channel = bool(
                not is_dm
                and interface.policy.allow_channel_members
                and channel_id is not None
                and interface.policy.permits_server_context(channel_id, role_ids)
            )
            mentioned = bool(client.user and client.user.mentioned_in(message))

            if not is_dm and not dedicated_channel and not mentioned:
                d.ignored += 1
                d.last_ignore_reason = "Server message did not mention Orion"
                print(f"[Discord] Ignored: {d.last_ignore_reason}")
                return

            if dedicated_channel:
                print(
                    f"[Discord] Dedicated Orion channel accepted message from "
                    f"{message.author.id} without requiring a mention."
                )

            if not interface.policy.permits_conversation(
                message.author.id,
                is_dm=is_dm,
                channel_id=channel_id,
                role_ids=role_ids,
            ):
                d.ignored += 1
                if is_dm:
                    d.last_ignore_reason = "DM sender is not an owner"
                elif interface.policy.allowed_channel_ids and message.channel.id not in interface.policy.allowed_channel_ids:
                    d.last_ignore_reason = "Channel is not allowed"
                elif interface.policy.allowed_role_ids:
                    d.last_ignore_reason = "Member lacks a required role"
                else:
                    d.last_ignore_reason = "Member is not authorized"
                print(
                    f"[Discord] Ignored user {message.author.id} in channel "
                    f"{getattr(message.channel, 'id', 'DM')}: {d.last_ignore_reason}"
                )
                return

            text = message.content
            if client.user:
                text = text.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "")
            text = interface.handler.normalize(text)
            image_intent = DiscordImageIntentDetector.detect(text)
            d.last_request = "[image generation request]" if image_intent else text
            print(
                f"[Discord] Request from {message.author.id} in channel "
                f"{getattr(message.channel, 'id', 'DM')}: "
                f"{'[image generation request]' if image_intent else text}"
            )

            async with message.channel.typing():
                if image_intent is not None:
                    handled = await interface.handle_image_request(message, text, discord)
                    if handled:
                        d.last_route = "image"
                        return
                try:
                    answer = await asyncio.to_thread(
                        interface.handler.handle,
                        message.author.id,
                        text,
                    )
                    d.last_route = interface._last_route or (
                        "security" if DiscordMessageHandler.is_sensitive(text) and not interface.policy.is_owner(message.author.id)
                        else None
                    )
                    if d.last_route:
                        print(f"[Discord] Routed via: {d.last_route}")
                except Exception as exc:  # Keep gateway alive on provider failures.
                    d.last_error = str(exc)
                    answer = f"Orion could not complete that request: {exc}"
                    print(f"[Discord] Request failed: {exc}")

            chunks = [answer[i:i + 1900] for i in range(0, len(answer), 1900)] or [answer]
            for chunk in chunks:
                await message.reply(chunk, mention_author=False)
                d.replies_sent += 1
            print("[Discord] Reply sent.")

        def runner():
            try:
                client.run(self.token, log_handler=None)
            except Exception as exc:
                self.error = str(exc)
                self.diagnostics.state = "Offline"
                self.diagnostics.last_error = str(exc)
                print(f"Discord interface stopped: {exc}")

        self.thread = threading.Thread(target=runner, name="orion-discord", daemon=True)
        self.thread.start()
