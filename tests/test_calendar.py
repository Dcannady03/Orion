import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from orion.services.base import ServiceState
from orion.services.calendar import CalendarBriefingProvider, CalendarEvent, CalendarService


TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 7, 14, 8, 0, tzinfo=TZ)


class FakeCalendarClient:
    def __init__(self, events=(), error=None):
        self.events = tuple(events)
        self.error = error
        self.calls = []

    def list_events(self, start, end, *, calendar_id="primary"):
        self.calls.append((start, end, calendar_id))
        if self.error:
            raise self.error
        return tuple(event for event in self.events if event.start < end and event.end > start)


def event(title, hour, *, day=14, minutes=60, all_day=False):
    start = datetime(2026, 7, day, hour, 0, tzinfo=TZ)
    return CalendarEvent(title.lower(), title, start, start + timedelta(minutes=minutes), all_day=all_day)


class CalendarTests(unittest.TestCase):
    def make_service(self, events=(), enabled=True):
        return CalendarService(
            enabled=enabled,
            timezone="America/Los_Angeles",
            client=FakeCalendarClient(events),
            user_name="Daniel",
            now_provider=lambda: NOW,
        )

    def test_disabled_calendar_reports_unavailable_without_network(self):
        service = self.make_service(enabled=False)
        self.assertFalse(service.is_available())
        self.assertEqual(service.get_status().state, ServiceState.UNAVAILABLE)
        result = service.handle_request("calendar")
        self.assertFalse(result.success)
        self.assertIn("not configured", result.error)

    def test_today_agenda_formats_events_in_time_order(self):
        service = self.make_service((event("Dentist", 14), event("Team Meeting", 10)))
        result = service.handle_request("calendar today")
        self.assertTrue(result.success)
        self.assertIn("Calendar for Today", result.output)
        self.assertLess(result.output.index("Team Meeting"), result.output.index("Dentist"))

    def test_conversational_calendar_question_uses_natural_response(self):
        service = self.make_service((event("Team Meeting", 10),))
        result = service.handle_request("what's on my calendar today?")
        self.assertTrue(result.success)
        self.assertTrue(result.output.startswith("You have 1 event today."))
        self.assertIn("10:00 AM — Team Meeting", result.output)

    def test_tomorrow_uses_next_day_bounds(self):
        service = self.make_service((event("Project Review", 9, day=15),))
        result = service.handle_request("calendar tomorrow")
        self.assertTrue(result.success)
        self.assertIn("Project Review", result.output)
        self.assertIn("Tomorrow", result.output)

    def test_next_event_searches_future_window(self):
        service = self.make_service((event("Later Meeting", 16), event("First Meeting", 9)))
        result = service.handle_request("when is my next meeting?")
        self.assertTrue(result.success)
        self.assertIn("First Meeting", result.output)
        self.assertIn("Tuesday at 9:00 AM", result.output)

    def test_free_afternoon_question_is_focused(self):
        service = self.make_service((event("Dentist", 14),))
        result = service.handle_request("am i free this afternoon?")
        self.assertTrue(result.success)
        self.assertEqual(result.output, "No, you have Dentist today afternoon.")

    def test_briefing_provider_returns_next_event(self):
        provider = CalendarBriefingProvider(self.make_service((event("Team Meeting", 10), event("Dentist", 14))))
        item = provider.get_briefing()[0]
        self.assertEqual(item.title, "Calendar")
        self.assertIn("2 events today", item.message)
        self.assertIn("Team Meeting at 10:00 AM", item.message)
        self.assertEqual(item.source, "Calendar")

    def test_disabled_briefing_provider_is_silent(self):
        provider = CalendarBriefingProvider(self.make_service(enabled=False))
        self.assertEqual(provider.get_briefing(), ())


if __name__ == "__main__":
    unittest.main()

class FakeConnectableCalendarClient(FakeCalendarClient):
    def __init__(self, events=(), error=None):
        super().__init__(events, error)
        self.connected = False

    def connect(self):
        if self.error:
            raise self.error
        self.connected = True


class CalendarProviderTests(unittest.TestCase):
    def test_multiple_providers_merge_and_label_events(self):
        from orion.services.calendar import CalendarProvider
        google = FakeConnectableCalendarClient((event("Dentist", 14),))
        outlook = FakeConnectableCalendarClient((event("Staff Meeting", 9),))
        service = CalendarService(
            enabled=True,
            timezone="America/Los_Angeles",
            providers=(
                CalendarProvider("google", "Personal Google", google),
                CalendarProvider("microsoft", "Work Outlook", outlook),
            ),
            now_provider=lambda: NOW,
        )
        result = service.handle_request("calendar today")
        self.assertTrue(result.success)
        self.assertLess(result.output.index("Staff Meeting"), result.output.index("Dentist"))
        self.assertIn("[Work Outlook]", result.output)
        self.assertIn("[Personal Google]", result.output)

    def test_connect_targets_requested_provider(self):
        from orion.services.calendar import CalendarProvider
        google = FakeConnectableCalendarClient()
        outlook = FakeConnectableCalendarClient()
        service = CalendarService(
            enabled=True,
            timezone="America/Los_Angeles",
            providers=(
                CalendarProvider("google", "Personal Google", google),
                CalendarProvider("microsoft", "Personal Outlook", outlook),
            ),
            now_provider=lambda: NOW,
        )
        result = service.handle_request("calendar connect microsoft")
        self.assertTrue(result.success)
        self.assertTrue(outlook.connected)
        self.assertFalse(google.connected)

    def test_provider_listing_is_provider_neutral(self):
        from orion.services.calendar import CalendarProvider
        service = CalendarService(
            enabled=True,
            timezone="America/Los_Angeles",
            providers=(CalendarProvider("google", "Personal Google", FakeConnectableCalendarClient()),),
            now_provider=lambda: NOW,
        )
        result = service.handle_request("calendar providers")
        self.assertTrue(result.success)
        self.assertIn("google: Personal Google", result.output)

class CalendarProviderManagementTests(unittest.TestCase):
    def test_enable_and_disable_provider_persist_state(self):
        from orion.services.calendar import CalendarProvider
        writes = []
        service = CalendarService(
            enabled=True,
            timezone="America/Los_Angeles",
            providers=(CalendarProvider("microsoft", "Personal Outlook", FakeConnectableCalendarClient(), enabled=False),),
            now_provider=lambda: NOW,
            provider_state_writer=lambda key, enabled: writes.append((key, enabled)),
        )
        enabled = service.handle_request("calendar enable microsoft")
        self.assertTrue(enabled.success)
        self.assertTrue(service.providers["microsoft"].enabled)
        self.assertEqual(writes[-1], ("microsoft", True))

        disabled = service.handle_request("calendar disable microsoft")
        self.assertTrue(disabled.success)
        self.assertFalse(service.providers["microsoft"].enabled)
        self.assertEqual(writes[-1], ("microsoft", False))

    def test_configure_microsoft_saves_client_id(self):
        from orion.services.calendar import CalendarProvider, MicrosoftCalendarClient
        writes = []
        client = MicrosoftCalendarClient("", ".orion/test-token.json")
        service = CalendarService(
            enabled=True,
            timezone="America/Los_Angeles",
            providers=(CalendarProvider("microsoft", "Personal Outlook", client, enabled=True),),
            now_provider=lambda: NOW,
            provider_config_writer=lambda provider, field, value: writes.append((provider, field, value)),
            input_provider=lambda prompt: "test-client-id",
        )
        result = service.handle_request("calendar configure microsoft")
        self.assertTrue(result.success)
        self.assertEqual(client.client_id, "test-client-id")
        self.assertEqual(writes, [("microsoft", "client_id", "test-client-id")])
        self.assertIn("calendar connect microsoft", result.output)

    def test_enable_unknown_provider_is_friendly_error(self):
        service = CalendarService(enabled=True, timezone="America/Los_Angeles", providers=(), now_provider=lambda: NOW)
        result = service.handle_request("calendar enable imaginary")
        self.assertFalse(result.success)
        self.assertIn("Unknown calendar provider", result.error)
