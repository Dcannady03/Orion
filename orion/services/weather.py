"""Open-Meteo weather integration for Orion v0.3.5."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from time import monotonic
import json
import socket
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from orion.services.base import ServiceResult, ServiceState, ServiceStatus
from orion.services.briefing import BriefingItem, BriefingPriority


class WeatherError(RuntimeError):
    """Raised when live weather data cannot be resolved safely."""


@dataclass(frozen=True, slots=True)
class WeatherLocation:
    name: str
    latitude: float
    longitude: float
    admin1: str = ""
    country: str = ""
    timezone: str = "auto"

    @property
    def display_name(self) -> str:
        parts = [self.name]
        if self.admin1 and self.admin1.lower() != self.name.lower():
            parts.append(self.admin1)
        return ", ".join(parts)


@dataclass(frozen=True, slots=True)
class DailyWeather:
    day: str
    weather_code: int
    high: float
    low: float
    precipitation_probability: int


@dataclass(frozen=True, slots=True)
class WeatherReport:
    location: WeatherLocation
    temperature: float
    apparent_temperature: float
    relative_humidity: int
    weather_code: int
    wind_speed: float
    is_day: bool
    daily: tuple[DailyWeather, ...]
    temperature_unit: str = "°F"
    wind_unit: str = "mph"

    @property
    def condition(self) -> str:
        return weather_code_description(self.weather_code)

    @property
    def today(self) -> DailyWeather:
        if not self.daily:
            raise WeatherError("The forecast did not contain daily weather data.")
        return self.daily[0]


WMO_DESCRIPTIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Heavy drizzle", 56: "Light freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Light freezing rain",
    67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Light rain showers", 81: "Rain showers",
    82: "Heavy rain showers", 85: "Light snow showers", 86: "Snow showers",
    95: "Thunderstorms", 96: "Thunderstorms with hail", 99: "Severe thunderstorms with hail",
}


def weather_code_description(code: int) -> str:
    return WMO_DESCRIPTIONS.get(int(code), f"Weather code {code}")


class OpenMeteoClient:
    """Minimal standard-library HTTP client for Open-Meteo."""

    GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
    FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, *, timeout: float = 5.0, opener: Callable[..., Any] = urlopen) -> None:
        self.timeout = timeout
        self._opener = opener

    def _get_json(self, url: str, parameters: dict[str, Any]) -> dict[str, Any]:
        request = Request(f"{url}?{urlencode(parameters)}", headers={"User-Agent": "Orion/0.3.5"})
        try:
            with self._opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, socket.timeout, ValueError, json.JSONDecodeError) as exc:
            raise WeatherError(f"Weather service request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise WeatherError("Weather service returned an invalid response.")
        if payload.get("error"):
            raise WeatherError(str(payload.get("reason") or "Weather service rejected the request."))
        return payload

    @staticmethod
    def _location_parts(query: str) -> tuple[str, tuple[str, ...]]:
        parts = tuple(part.strip() for part in query.split(",") if part.strip())
        if not parts:
            return query.strip(), ()
        return parts[0], tuple(part.casefold() for part in parts[1:])

    @staticmethod
    def _result_score(result: dict[str, Any], qualifiers: tuple[str, ...]) -> int:
        if not qualifiers:
            return 0
        fields = {
            str(result.get("admin1") or "").casefold(),
            str(result.get("admin2") or "").casefold(),
            str(result.get("country") or "").casefold(),
            str(result.get("country_code") or "").casefold(),
        }
        aliases = {"california": "ca", "ca": "california"}
        score = 0
        for qualifier in qualifiers:
            if qualifier in fields:
                score += 10
            elif aliases.get(qualifier) in fields:
                score += 10
            elif any(qualifier in field or field in qualifier for field in fields if field):
                score += 3
        return score

    def geocode(self, query: str) -> WeatherLocation:
        # Open-Meteo's `name` field expects the settlement name. A profile value
        # such as "Yuba City, California" must therefore be split and the
        # remaining components used to rank the returned candidates.
        city, qualifiers = self._location_parts(query)
        payload = self._get_json(
            self.GEOCODING_URL,
            {"name": city, "count": 10, "language": "en", "format": "json"},
        )
        results = payload.get("results") or []
        if not results:
            raise WeatherError(f"I couldn't find a weather location named {query}.")
        result = max(results, key=lambda item: self._result_score(item, qualifiers))
        return WeatherLocation(
            name=str(result.get("name") or city),
            latitude=float(result["latitude"]),
            longitude=float(result["longitude"]),
            admin1=str(result.get("admin1") or ""),
            country=str(result.get("country") or ""),
            timezone=str(result.get("timezone") or "auto"),
        )

    def forecast(self, location: WeatherLocation, *, units: str = "imperial") -> WeatherReport:
        imperial = units.lower() != "metric"
        parameters = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "temperature_unit": "fahrenheit" if imperial else "celsius",
            "wind_speed_unit": "mph" if imperial else "kmh",
            "timezone": location.timezone or "auto",
            "forecast_days": 7,
        }
        payload = self._get_json(self.FORECAST_URL, parameters)
        current = payload.get("current") or {}
        daily_payload = payload.get("daily") or {}
        times = daily_payload.get("time") or []
        daily = tuple(
            DailyWeather(
                day=str(day),
                weather_code=int((daily_payload.get("weather_code") or [0])[index]),
                high=float((daily_payload.get("temperature_2m_max") or [0])[index]),
                low=float((daily_payload.get("temperature_2m_min") or [0])[index]),
                precipitation_probability=int((daily_payload.get("precipitation_probability_max") or [0])[index] or 0),
            )
            for index, day in enumerate(times)
        )
        try:
            return WeatherReport(
                location=location,
                temperature=float(current["temperature_2m"]),
                apparent_temperature=float(current["apparent_temperature"]),
                relative_humidity=int(current["relative_humidity_2m"]),
                weather_code=int(current["weather_code"]),
                wind_speed=float(current["wind_speed_10m"]),
                is_day=bool(current["is_day"]),
                daily=daily,
                temperature_unit="°F" if imperial else "°C",
                wind_unit="mph" if imperial else "km/h",
            )
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise WeatherError("Weather service response was missing required forecast fields.") from exc


class WeatherService:
    """Resolve locations, fetch reports, and format truthful weather answers."""

    name = "Weather"

    def __init__(self, default_location: str, *, units: str = "imperial", client: OpenMeteoClient | None = None, user_name: str = "") -> None:
        self.default_location = default_location.strip()
        self.units = units
        self.client = client or OpenMeteoClient()
        self.user_name = user_name.strip()
        self._location_cache: dict[str, WeatherLocation] = {}
        self._report_cache: dict[str, tuple[float, WeatherReport]] = {}
        self._cache_ttl_seconds = 300.0
        self._last_error = ""

    def is_available(self) -> bool:
        return bool(self.default_location)

    def get_status(self) -> ServiceStatus:
        if not self.default_location:
            return ServiceStatus(ServiceState.UNAVAILABLE, "No default weather location is configured.")
        if self._last_error:
            return ServiceStatus(ServiceState.DEGRADED, self._last_error)
        return ServiceStatus(ServiceState.AVAILABLE, f"Ready for {self.default_location}")

    def resolve_location(self, query: str | None = None) -> WeatherLocation:
        value = (query or self.default_location).strip()
        if not value:
            raise WeatherError("No weather location is configured.")
        key = value.casefold()
        if key not in self._location_cache:
            self._location_cache[key] = self.client.geocode(value)
        return self._location_cache[key]

    def get_weather(self, location_query: str | None = None) -> WeatherReport:
        cache_key = (location_query or self.default_location).strip().casefold()
        cached = self._report_cache.get(cache_key)
        now = monotonic()
        if cached and now - cached[0] <= self._cache_ttl_seconds:
            return cached[1]

        try:
            report = self.client.forecast(self.resolve_location(location_query), units=self.units)
            self._report_cache[cache_key] = (now, report)
            self._last_error = ""
            return report
        except WeatherError as exc:
            self._last_error = str(exc)
            if cached:
                return cached[1]
            raise

    @staticmethod
    def _requested_location(request: str) -> str | None:
        text = request.strip()
        lowered = text.lower()
        if " in " in lowered:
            return text[lowered.rfind(" in ") + 4:].strip(" ?.!")
        if lowered.startswith("weather "):
            suffix = text[8:].strip()
            if suffix.lower() not in {"today", "tomorrow", "weekend", "here"}:
                return suffix
        return None

    @staticmethod
    def _day_index(request: str) -> int:
        return 1 if "tomorrow" in request.lower() else 0

    @staticmethod
    def _is_conversational_request(request: str) -> bool:
        value = request.strip().lower()
        return not (value == "weather" or value.startswith("weather "))

    @staticmethod
    def _has_greeting(request: str) -> bool:
        value = request.strip().lower()
        greetings = ("good morning", "good afternoon", "good evening", "hello", "hi ", "hey ")
        return value in {"hi", "hello", "hey"} or any(greeting in value for greeting in greetings)

    def _format_conversational(self, report: WeatherReport, request: str, day: WeatherDay, index: int) -> str:
        value = request.lower()
        unit = report.temperature_unit
        name = f", {self.user_name}" if self.user_name else ""
        greeting = f"Good morning{name}.\n\n" if "good morning" in value else ""
        if "good afternoon" in value:
            greeting = f"Good afternoon{name}.\n\n"
        elif "good evening" in value:
            greeting = f"Good evening{name}.\n\n"

        if any(phrase in value for phrase in ("will it rain", "is it raining", "umbrella", "rain today", "chance of rain")):
            if day.precipitation_probability < 20:
                answer = f"No rain is expected in {report.location.display_name} today."
            else:
                answer = f"There is a {day.precipitation_probability}% chance of rain in {report.location.display_name} today."
            return greeting + answer

        if any(phrase in value for phrase in ("how hot", "temperature", "how warm")):
            when = "Tomorrow" if index else "Today"
            return (
                greeting
                + f"{when} in {report.location.display_name}, the high will be about {round(day.high)}{unit} "
                + f"with a low near {round(day.low)}{unit}. "
                + (f"It is currently {round(report.temperature)}{unit}." if index == 0 else "")
            ).rstrip()

        if index == 1:
            return (
                greeting
                + f"Tomorrow in {report.location.display_name}, expect {weather_code_description(day.weather_code).lower()} "
                + f"with a high near {round(day.high)}{unit} and a low near {round(day.low)}{unit}. "
                + ("No rain is expected." if day.precipitation_probability < 20 else f"The chance of rain is {day.precipitation_probability}%.")
            )

        rain_sentence = (
            "No rain is expected."
            if day.precipitation_probability < 20
            else f"The chance of rain is {day.precipitation_probability}%."
        )
        return (
            greeting
            + f"It is currently {round(report.temperature)}{unit} and {report.condition.lower()} in {report.location.display_name}. "
            + f"Today's high will be about {round(day.high)}{unit}, with a low near {round(day.low)}{unit}. "
            + f"Humidity is {report.relative_humidity}%, winds are around {round(report.wind_speed)} {report.wind_unit}, and {rain_sentence.lower()}"
        )

    def format_report(self, report: WeatherReport, request: str = "weather") -> str:
        index = self._day_index(request)
        if index >= len(report.daily):
            raise WeatherError("That forecast day is not available.")
        day = report.daily[index]

        if self._is_conversational_request(request):
            return self._format_conversational(report, request, day, index)

        if index == 0:
            rain = "No rain is expected." if day.precipitation_probability < 20 else f"Rain chance: {day.precipitation_probability}%."
            return (
                f"Weather for {report.location.display_name}\n"
                f"  Now: {round(report.temperature)}{report.temperature_unit}, {report.condition}\n"
                f"  Feels like: {round(report.apparent_temperature)}{report.temperature_unit}\n"
                f"  High / Low: {round(day.high)}{report.temperature_unit} / {round(day.low)}{report.temperature_unit}\n"
                f"  Humidity: {report.relative_humidity}%\n"
                f"  Wind: {round(report.wind_speed)} {report.wind_unit}\n"
                f"  {rain}"
            )
        return (
            f"Tomorrow in {report.location.display_name}: "
            f"{weather_code_description(day.weather_code)}, "
            f"high {round(day.high)}{report.temperature_unit}, "
            f"low {round(day.low)}{report.temperature_unit}, "
            f"rain chance {day.precipitation_probability}%."
        )

    def handle_request(self, request: str) -> ServiceResult:
        try:
            report = self.get_weather(self._requested_location(request))
            return ServiceResult(True, self.format_report(report, request), asdict(report))
        except WeatherError as exc:
            return ServiceResult(False, error=str(exc))


class WeatherBriefingProvider:
    """Add current weather to Morning Star without coupling startup to HTTP."""

    name = "Weather"

    def __init__(self, service: WeatherService) -> None:
        self.service = service

    def get_briefing(self) -> tuple[BriefingItem, ...]:
        report = self.service.get_weather()
        today = report.today
        rain = f", {today.precipitation_probability}% rain" if today.precipitation_probability >= 20 else ""
        return (
            BriefingItem(
                "Weather",
                f"{round(report.temperature)}{report.temperature_unit} and {report.condition.lower()}; "
                f"high {round(today.high)}{report.temperature_unit}, low {round(today.low)}{report.temperature_unit}{rain}",
                priority=BriefingPriority.IMPORTANT,
                source=self.name,
                icon="[WX]",
            ),
        )
