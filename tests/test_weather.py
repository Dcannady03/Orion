import json
import unittest
from unittest.mock import Mock

from orion.services.base import ServiceState
from orion.services.weather import (
    OpenMeteoClient, WeatherBriefingProvider, WeatherError, WeatherLocation,
    WeatherService, weather_code_description,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False
    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeClient:
    def __init__(self):
        self.geocoded = []
        self.forecasts = []
    def geocode(self, query):
        self.geocoded.append(query)
        return WeatherLocation("Yuba City", 39.14, -121.62, "California", "United States", "America/Los_Angeles")
    def forecast(self, location, *, units="imperial"):
        self.forecasts.append((location, units))
        payload = {
            "current": {
                "temperature_2m": 75.2, "relative_humidity_2m": 42,
                "apparent_temperature": 74.0, "is_day": 1,
                "weather_code": 0, "wind_speed_10m": 7.2,
            },
            "daily": {
                "time": ["2026-07-14", "2026-07-15"],
                "weather_code": [0, 2],
                "temperature_2m_max": [96.0, 93.0],
                "temperature_2m_min": [64.0, 63.0],
                "precipitation_probability_max": [5, 25],
            },
        }
        opener = lambda *_args, **_kwargs: FakeResponse(payload)
        return OpenMeteoClient(opener=opener).forecast(location, units=units)


class WeatherTests(unittest.TestCase):
    def test_current_weather_formats_live_report(self):
        service = WeatherService("Yuba City, California", client=FakeClient())
        result = service.handle_request("weather")
        self.assertTrue(result.success)
        self.assertIn("Weather for Yuba City, California", result.output)
        self.assertIn("Now: 75°F, Clear sky", result.output)
        self.assertIn("High / Low: 96°F / 64°F", result.output)

    def test_tomorrow_weather_uses_second_daily_forecast(self):
        service = WeatherService("Yuba City", client=FakeClient())
        result = service.handle_request("weather tomorrow")
        self.assertTrue(result.success)
        self.assertIn("Tomorrow", result.output)
        self.assertIn("Partly cloudy", result.output)
        self.assertIn("25%", result.output)

    def test_explicit_location_is_geocoded_and_cached(self):
        client = FakeClient()
        service = WeatherService("Yuba City", client=client)
        service.handle_request("weather in Sacramento")
        service.handle_request("weather in Sacramento")
        self.assertEqual(client.geocoded, ["Sacramento"])


    def test_recent_weather_report_is_reused_without_second_network_call(self):
        client = FakeClient()
        service = WeatherService("Yuba City", client=client)
        first = service.handle_request("weather")
        second = service.handle_request("good morning, how is the weather today?")
        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(len(client.forecasts), 1)

    def test_cached_weather_is_used_when_refresh_temporarily_fails(self):
        client = FakeClient()
        service = WeatherService("Yuba City", client=client)
        first = service.get_weather()
        service._report_cache["yuba city"] = (0.0, first)
        client.forecast = Mock(side_effect=WeatherError("HTTP Error 503: Service Unavailable"))
        cached = service.get_weather()
        self.assertEqual(cached.temperature, first.temperature)
        self.assertEqual(service.get_status().state, ServiceState.DEGRADED)

    def test_weather_error_returns_failed_service_result(self):
        client = Mock()
        client.geocode.side_effect = WeatherError("offline")
        service = WeatherService("Yuba City", client=client)
        result = service.handle_request("weather")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "offline")
        self.assertEqual(service.get_status().state, ServiceState.DEGRADED)

    def test_briefing_provider_returns_important_item(self):
        provider = WeatherBriefingProvider(WeatherService("Yuba City", client=FakeClient()))
        item = provider.get_briefing()[0]
        self.assertEqual(item.title, "Weather")
        self.assertIn("75°F", item.message)
        self.assertEqual(item.source, "Weather")


    def test_geocode_splits_city_and_state_and_selects_matching_region(self):
        captured = {}
        payload = {
            "results": [
                {
                    "name": "Yuba City", "latitude": 1.0, "longitude": 2.0,
                    "admin1": "Somewhere Else", "country": "United States",
                    "country_code": "US", "timezone": "America/Chicago",
                },
                {
                    "name": "Yuba City", "latitude": 39.14, "longitude": -121.62,
                    "admin1": "California", "country": "United States",
                    "country_code": "US", "timezone": "America/Los_Angeles",
                },
            ]
        }

        def opener(request, **_kwargs):
            captured["url"] = request.full_url
            return FakeResponse(payload)

        location = OpenMeteoClient(opener=opener).geocode("Yuba City, California")
        self.assertIn("name=Yuba+City", captured["url"])
        self.assertNotIn("name=Yuba+City%2C+California", captured["url"])
        self.assertEqual(location.admin1, "California")
        self.assertEqual(location.latitude, 39.14)

    def test_geocode_accepts_state_abbreviation(self):
        payload = {
            "results": [
                {
                    "name": "Yuba City", "latitude": 39.14, "longitude": -121.62,
                    "admin1": "California", "country": "United States",
                    "country_code": "US", "timezone": "America/Los_Angeles",
                }
            ]
        }
        location = OpenMeteoClient(opener=lambda *_args, **_kwargs: FakeResponse(payload)).geocode("Yuba City, CA")
        self.assertEqual(location.admin1, "California")


    def test_conversational_weather_preserves_greeting_and_uses_natural_summary(self):
        service = WeatherService("Yuba City, California", client=FakeClient(), user_name="Daniel")
        result = service.handle_request("good morning can you check the weather for me?")
        self.assertTrue(result.success)
        self.assertTrue(result.output.startswith("Good morning, Daniel."))
        self.assertIn("It is currently 75°F and clear sky in Yuba City, California.", result.output)
        self.assertIn("Today's high will be about 96°F", result.output)
        self.assertNotIn("Weather for", result.output)
        self.assertNotIn("  Now:", result.output)

    def test_rain_question_returns_focused_answer(self):
        service = WeatherService("Yuba City", client=FakeClient())
        result = service.handle_request("will it rain today?")
        self.assertTrue(result.success)
        self.assertEqual(result.output, "No rain is expected in Yuba City, California today.")

    def test_weather_command_keeps_detailed_report(self):
        service = WeatherService("Yuba City", client=FakeClient())
        result = service.handle_request("weather")
        self.assertIn("Weather for Yuba City, California", result.output)
        self.assertIn("  Humidity: 42%", result.output)

    def test_wmo_unknown_code_is_not_silently_mislabeled(self):
        self.assertEqual(weather_code_description(123), "Weather code 123")


if __name__ == "__main__":
    unittest.main()
