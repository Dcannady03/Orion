"""Provider-neutral calendar integration for Orion v0.3.6.1."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, time
import json
from pathlib import Path
from typing import Callable, Iterable, Protocol
from zoneinfo import ZoneInfo

from orion.services.base import ServiceResult, ServiceState, ServiceStatus
from orion.services.briefing import BriefingItem, BriefingPriority


class CalendarError(RuntimeError):
    """Raised when calendar data cannot be loaded safely."""


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    event_id: str
    title: str
    start: datetime
    end: datetime
    location: str = ""
    description: str = ""
    all_day: bool = False
    source: str = ""

    @property
    def duration_minutes(self) -> int:
        return max(0, int((self.end - self.start).total_seconds() // 60))


class CalendarClient(Protocol):
    def connect(self) -> None: ...
    def list_events(self, start: datetime, end: datetime, *, calendar_id: str = "primary") -> Iterable[CalendarEvent]: ...


@dataclass(slots=True)
class CalendarProvider:
    key: str
    display_name: str
    client: CalendarClient
    calendar_id: str = "primary"
    enabled: bool = True

    def connect(self) -> None:
        self.client.connect()

    def list_events(self, start: datetime, end: datetime) -> tuple[CalendarEvent, ...]:
        events = self.client.list_events(start, end, calendar_id=self.calendar_id)
        return tuple(replace(event, source=event.source or self.display_name) for event in events)


class GoogleCalendarClient:
    """Google Calendar adapter. Interactive OAuth occurs only in connect()."""

    SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",)

    def __init__(self, credentials_path: str, token_path: str) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self._service = None

    def _imports(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover
            raise CalendarError("Google Calendar dependencies are not installed. Run: python -m pip install -r requirements.txt") from exc
        return Request, Credentials, InstalledAppFlow, build

    def _load_credentials(self, *, interactive: bool):
        Request, Credentials, InstalledAppFlow, _ = self._imports()
        credentials = None
        if self.token_path.exists():
            try:
                credentials = Credentials.from_authorized_user_file(str(self.token_path), self.SCOPES)
            except Exception as exc:
                raise CalendarError(f"Google Calendar token could not be read: {exc}") from exc
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                self._save_token(credentials)
            except Exception as exc:
                raise CalendarError(f"Google Calendar authorization refresh failed: {exc}") from exc
        if credentials and credentials.valid:
            return credentials
        if not interactive:
            raise CalendarError("Google Calendar is not connected. Run 'calendar connect google'.")
        if not self.credentials_path.exists():
            raise CalendarError(f"Google Calendar credentials were not found at {self.credentials_path}.")
        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), self.SCOPES)
            credentials = flow.run_local_server(port=0)
            self._save_token(credentials)
            return credentials
        except Exception as exc:
            raise CalendarError(f"Google Calendar authorization failed: {exc}") from exc

    def _save_token(self, credentials) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(credentials.to_json(), encoding="utf-8")

    def _build_service(self, *, interactive: bool = False):
        if self._service is not None:
            return self._service
        _, _, _, build = self._imports()
        credentials = self._load_credentials(interactive=interactive)
        try:
            self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        except Exception as exc:
            raise CalendarError(f"Google Calendar client could not start: {exc}") from exc
        return self._service

    def connect(self) -> None:
        self._build_service(interactive=True)

    @staticmethod
    def _parse_event_time(value: dict[str, str], timezone: ZoneInfo) -> tuple[datetime, bool]:
        if "dateTime" in value:
            return datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00")).astimezone(timezone), False
        if "date" in value:
            return datetime.combine(datetime.fromisoformat(value["date"]).date(), time.min, timezone), True
        raise CalendarError("A calendar event was missing its start or end time.")

    def list_events(self, start: datetime, end: datetime, *, calendar_id: str = "primary") -> tuple[CalendarEvent, ...]:
        service = self._build_service(interactive=False)
        timezone = start.tzinfo if isinstance(start.tzinfo, ZoneInfo) else ZoneInfo("UTC")
        try:
            response = service.events().list(calendarId=calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(), singleEvents=True, orderBy="startTime").execute()
        except Exception as exc:
            raise CalendarError(f"Google Calendar request failed: {exc}") from exc
        events = []
        for item in response.get("items", []):
            if item.get("status") == "cancelled":
                continue
            event_start, all_day = self._parse_event_time(item.get("start") or {}, timezone)
            event_end, _ = self._parse_event_time(item.get("end") or {}, timezone)
            events.append(CalendarEvent(str(item.get("id") or ""), str(item.get("summary") or "Untitled event"), event_start, event_end, str(item.get("location") or ""), str(item.get("description") or ""), all_day))
        return tuple(events)


class MicrosoftCalendarClient:
    """Microsoft Graph calendar adapter using delegated desktop authentication."""

    SCOPES = ("Calendars.Read", "offline_access", "User.Read")
    GRAPH_URL = "https://graph.microsoft.com/v1.0/me/calendarView"

    def __init__(self, client_id: str, token_path: str, *, tenant: str = "common", timeout: float = 10.0) -> None:
        self.client_id = client_id.strip()
        self.token_path = Path(token_path)
        self.tenant = tenant or "common"
        self.timeout = timeout

    def _imports(self):
        try:
            import msal
            import requests
        except ImportError as exc:  # pragma: no cover
            raise CalendarError("Microsoft Calendar dependencies are not installed. Run: python -m pip install -r requirements.txt") from exc
        return msal, requests

    def _cache_and_app(self):
        if not self.client_id:
            raise CalendarError("Microsoft Calendar client_id is not configured.")
        msal, _ = self._imports()
        cache = msal.SerializableTokenCache()
        if self.token_path.exists():
            try:
                cache.deserialize(self.token_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise CalendarError(f"Microsoft Calendar token cache could not be read: {exc}") from exc
        app = msal.PublicClientApplication(self.client_id, authority=f"https://login.microsoftonline.com/{self.tenant}", token_cache=cache)
        return app, cache

    def _save_cache(self, cache) -> None:
        if cache.has_state_changed:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(cache.serialize(), encoding="utf-8")

    def _token(self, *, interactive: bool) -> str:
        app, cache = self._cache_and_app()
        accounts = app.get_accounts()
        result = app.acquire_token_silent(list(self.SCOPES), account=accounts[0]) if accounts else None
        if not result and interactive:
            result = app.acquire_token_interactive(scopes=list(self.SCOPES), prompt="select_account")
        self._save_cache(cache)
        if not result or "access_token" not in result:
            detail = (result or {}).get("error_description", "Microsoft Calendar is not connected.")
            if not interactive:
                detail = "Microsoft Calendar is not connected. Run 'calendar connect microsoft'."
            raise CalendarError(detail)
        return str(result["access_token"])

    def connect(self) -> None:
        self._token(interactive=True)

    @staticmethod
    def _parse_graph_time(value: dict, timezone: ZoneInfo) -> datetime:
        raw = str(value.get("dateTime") or "")
        if not raw:
            raise CalendarError("A Microsoft calendar event was missing its time.")
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone)
        return parsed.astimezone(timezone)

    def list_events(self, start: datetime, end: datetime, *, calendar_id: str = "primary") -> tuple[CalendarEvent, ...]:
        _, requests = self._imports()
        token = self._token(interactive=False)
        headers = {"Authorization": f"Bearer {token}", "Prefer": f'outlook.timezone="{getattr(start.tzinfo, "key", "UTC")}"'}
        params = {"startDateTime": start.isoformat(), "endDateTime": end.isoformat(), "$orderby": "start/dateTime", "$top": "250"}
        try:
            response = requests.get(self.GRAPH_URL, headers=headers, params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise CalendarError(f"Microsoft Calendar request failed: {exc}") from exc
        timezone = start.tzinfo if isinstance(start.tzinfo, ZoneInfo) else ZoneInfo("UTC")
        events = []
        for item in payload.get("value", []):
            if item.get("isCancelled"):
                continue
            event_start = self._parse_graph_time(item.get("start") or {}, timezone)
            event_end = self._parse_graph_time(item.get("end") or {}, timezone)
            events.append(CalendarEvent(str(item.get("id") or ""), str(item.get("subject") or "Untitled event"), event_start, event_end, str((item.get("location") or {}).get("displayName") or ""), str(item.get("bodyPreview") or ""), bool(item.get("isAllDay"))))
        return tuple(events)


class CalendarService:
    """Provider-neutral calendar coordinator that merges connected accounts."""

    name = "Calendar"

    def __init__(self, *, enabled: bool, timezone: str, providers: Iterable[CalendarProvider] | None = None,
                 calendar_id: str = "primary", client: CalendarClient | None = None,
                 credentials_path: str = "config/google-calendar-credentials.json",
                 token_path: str = ".orion/google-calendar-token.json", user_name: str = "", now_provider=None,
                 provider_state_writer: Callable[[str, bool], None] | None = None,
                 provider_config_writer: Callable[[str, str, str], None] | None = None,
                 input_provider: Callable[[str], str] | None = None) -> None:
        self.enabled = bool(enabled)
        self.timezone_name = timezone or "UTC"
        try:
            self.timezone = ZoneInfo(self.timezone_name)
        except Exception as exc:
            raise CalendarError(f"Invalid calendar timezone: {self.timezone_name}") from exc
        if providers is None:
            legacy_client = client or GoogleCalendarClient(credentials_path, token_path)
            providers = (CalendarProvider("google", "Google Calendar", legacy_client, calendar_id, self.enabled),)
        self.providers = {provider.key.lower(): provider for provider in providers}
        self.user_name = user_name.strip()
        self._now_provider = now_provider or (lambda: datetime.now(self.timezone))
        self._provider_state_writer = provider_state_writer
        self._provider_config_writer = provider_config_writer
        self._input_provider = input_provider or input
        self._last_error = ""

    def active_providers(self) -> tuple[CalendarProvider, ...]:
        return tuple(provider for provider in self.providers.values() if provider.enabled)

    def is_available(self) -> bool:
        return self.enabled and bool(self.active_providers())

    def get_status(self) -> ServiceStatus:
        if not self.enabled:
            return ServiceStatus(ServiceState.UNAVAILABLE, "Calendar is not configured.")
        if not self.active_providers():
            return ServiceStatus(ServiceState.UNAVAILABLE, "No calendar providers are enabled.")
        if self._last_error:
            return ServiceStatus(ServiceState.DEGRADED, self._last_error)
        names = ", ".join(provider.display_name for provider in self.active_providers())
        return ServiceStatus(ServiceState.AVAILABLE, f"Calendar providers ready: {names}.")

    def provider_summary(self) -> str:
        lines = ["Calendar Providers:"]
        for provider in self.providers.values():
            state = "enabled" if provider.enabled else "disabled"
            lines.append(f"  {provider.key}: {provider.display_name} [{state}]")
        return "\n".join(lines)


    def enable_provider(self, provider_key: str) -> ServiceResult:
        key = provider_key.strip().lower()
        provider = self.providers.get(key)
        if provider is None:
            return ServiceResult(False, error=f"Unknown calendar provider '{key}'. Run 'calendar providers'.")
        provider.enabled = True
        self.enabled = True
        if self._provider_state_writer:
            self._provider_state_writer(key, True)
        message = f"{provider.display_name} is now enabled."
        if key == "microsoft" and isinstance(provider.client, MicrosoftCalendarClient) and not provider.client.client_id:
            message += "\nClient ID is not configured yet. Run: calendar configure microsoft"
        if key == "google" and isinstance(provider.client, GoogleCalendarClient) and not provider.client.credentials_path.exists():
            message += "\nGoogle credentials are not configured yet. Run: calendar configure google"
        return ServiceResult(True, message)

    def disable_provider(self, provider_key: str) -> ServiceResult:
        key = provider_key.strip().lower()
        provider = self.providers.get(key)
        if provider is None:
            return ServiceResult(False, error=f"Unknown calendar provider '{key}'. Run 'calendar providers'.")
        provider.enabled = False
        if self._provider_state_writer:
            self._provider_state_writer(key, False)
        return ServiceResult(True, f"{provider.display_name} is now disabled.")

    def configure_provider(self, provider_key: str) -> ServiceResult:
        key = provider_key.strip().lower()
        provider = self.providers.get(key)
        if provider is None:
            return ServiceResult(False, error=f"Unknown calendar provider '{key}'. Run 'calendar providers'.")
        if key == "microsoft" and isinstance(provider.client, MicrosoftCalendarClient):
            client_id = self._input_provider("Enter your Microsoft Application (client) ID: ").strip()
            if not client_id:
                return ServiceResult(False, error="Microsoft client ID was not changed.")
            provider.client.client_id = client_id
            if self._provider_config_writer:
                self._provider_config_writer(key, "client_id", client_id)
            return ServiceResult(True, "Microsoft Calendar configuration saved.\nRun: calendar connect microsoft")
        if key == "google" and isinstance(provider.client, GoogleCalendarClient):
            default = str(provider.client.credentials_path)
            entered = self._input_provider(f"Google credentials path [{default}]: ").strip()
            credentials_path = entered or default
            provider.client.credentials_path = Path(credentials_path)
            if self._provider_config_writer:
                self._provider_config_writer(key, "credentials_path", credentials_path)
            return ServiceResult(True, "Google Calendar configuration saved.\nRun: calendar connect google")
        return ServiceResult(False, error=f"Calendar provider '{key}' does not support guided configuration.")

    def connect(self, provider_key: str = "") -> ServiceResult:
        if not self.enabled:
            return ServiceResult(False, error="Calendar is disabled in config/default.yaml.")
        key = provider_key.strip().lower()
        if not key:
            active = self.active_providers()
            if len(active) != 1:
                return ServiceResult(False, error="Choose a provider: calendar connect google | microsoft")
            provider = active[0]
        else:
            provider = self.providers.get(key)
            if provider is None or not provider.enabled:
                return ServiceResult(False, error=f"Calendar provider '{key}' is not enabled.")
        try:
            provider.connect()
            self._last_error = ""
            return ServiceResult(True, f"{provider.display_name} is connected.")
        except CalendarError as exc:
            self._last_error = str(exc)
            return ServiceResult(False, error=str(exc))

    def _day_bounds(self, offset: int = 0) -> tuple[datetime, datetime]:
        day = self._now_provider().date() + timedelta(days=offset)
        start = datetime.combine(day, time.min, self.timezone)
        return start, start + timedelta(days=1)

    def _list_merged(self, start: datetime, end: datetime) -> tuple[CalendarEvent, ...]:
        if not self.is_available():
            raise CalendarError("Calendar is not configured.")
        events, errors = [], []
        for provider in self.active_providers():
            try:
                events.extend(provider.list_events(start, end))
            except CalendarError as exc:
                errors.append(f"{provider.display_name}: {exc}")
        if errors and not events:
            self._last_error = "; ".join(errors)
            raise CalendarError(self._last_error)
        self._last_error = "; ".join(errors)
        return tuple(sorted(events, key=lambda event: (event.start, event.title, event.source)))

    def events_for_day(self, offset: int = 0) -> tuple[CalendarEvent, ...]:
        return self._list_merged(*self._day_bounds(offset))

    def next_event(self) -> CalendarEvent | None:
        now = self._now_provider()
        future = [event for event in self._list_merged(now, now + timedelta(days=30)) if event.end > now]
        return min(future, key=lambda event: event.start) if future else None

    def is_free(self, *, tomorrow=False, afternoon=False, morning=False):
        offset = 1 if tomorrow else 0
        events = self.events_for_day(offset)
        start, end = self._day_bounds(offset)
        label = "tomorrow" if tomorrow else "today"
        if morning:
            start, end, label = start.replace(hour=6), start.replace(hour=12), label + " morning"
        elif afternoon:
            start, end, label = start.replace(hour=12), start.replace(hour=18), label + " afternoon"
        matches = tuple(event for event in events if event.start < end and event.end > start)
        return not matches, matches, label

    def _format_event_portable(self, event: CalendarEvent) -> str:
        when = "All day" if event.all_day else event.start.strftime("%I:%M %p").lstrip("0")
        source = f" [{event.source}]" if event.source else ""
        return f"{when} — {event.title}{source}"

    def format_day(self, offset=0, *, conversational=False) -> str:
        events = self.events_for_day(offset)
        label = "tomorrow" if offset else "today"
        if not events:
            return f"You don't have anything scheduled {label}." if conversational else f"No calendar events {label}."
        lines = [f"You have {len(events)} event{'s' if len(events) != 1 else ''} {label}." if conversational else f"Calendar for {label.title()}:"]
        lines.extend(f"  • {self._format_event_portable(event)}" for event in events)
        return "\n".join(lines)

    def format_next(self) -> str:
        event = self.next_event()
        if event is None:
            return "You don't have any upcoming calendar events in the next 30 days."
        when = event.start.strftime("%A at %I:%M %p").replace(" 0", " ")
        source = f" on {event.source}" if event.source else ""
        return f"Your next event is {event.title} on {when}{source}."

    @staticmethod
    def _is_conversational(request: str) -> bool:
        value = request.strip().lower()
        return value.startswith("ask ") or any(token in value for token in ("what's", "what is", "do i", "am i", "when", "good morning", "good afternoon", "good evening"))

    def handle_request(self, request: str) -> ServiceResult:
        value = request.strip().lower()
        try:
            if value in {"calendar providers", "calendar accounts"}:
                return ServiceResult(True, self.provider_summary())
            if value.startswith("calendar enable "):
                return self.enable_provider(value.split("calendar enable ", 1)[1])
            if value.startswith("calendar disable "):
                return self.disable_provider(value.split("calendar disable ", 1)[1])
            if value.startswith("calendar configure "):
                return self.configure_provider(value.split("calendar configure ", 1)[1])
            if "connect" in value:
                provider = value.split("connect", 1)[1].strip()
                return self.connect(provider)
            if "next" in value or "when's my next" in value or "when is my next" in value:
                return ServiceResult(True, self.format_next())
            if "free" in value or "anything this" in value:
                free, events, label = self.is_free(tomorrow="tomorrow" in value, morning="morning" in value, afternoon="afternoon" in value)
                if free:
                    return ServiceResult(True, f"Yes, you're free {label}.")
                names = ", ".join(event.title for event in events)
                return ServiceResult(True, f"No, you have {names} {label}.")
            offset = 1 if "tomorrow" in value else 0
            events = self.events_for_day(offset)
            return ServiceResult(True, self.format_day(offset, conversational=self._is_conversational(request)), {"events": [asdict(event) for event in events]})
        except CalendarError as exc:
            return ServiceResult(False, error=str(exc))


class CalendarBriefingProvider:
    name = "Calendar"

    def __init__(self, service: CalendarService) -> None:
        self.service = service

    def get_briefing(self) -> tuple[BriefingItem, ...]:
        if not self.service.is_available():
            return ()
        events = self.service.events_for_day(0)
        if not events:
            message = "No events today"
        else:
            next_event = events[0]
            when = "all day" if next_event.all_day else next_event.start.strftime("%I:%M %p").lstrip("0")
            source = f" [{next_event.source}]" if next_event.source else ""
            message = f"{len(events)} event{'s' if len(events) != 1 else ''} today; next is {next_event.title} at {when}{source}"
        return (BriefingItem("Calendar", message, priority=BriefingPriority.IMPORTANT, source=self.name, icon="[CAL]"),)
