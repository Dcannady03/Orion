"""Shared natural-language request routing for every Orion interface."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoutedResponse:
    output: str
    source: str
    success: bool = True


class RequestRouterService:
    """Route natural-language requests to Orion services before AI fallback."""

    WEATHER_PHRASES = (
        "weather", "forecast", "temperature", "how hot", "how cold",
        "do i need an umbrella", "will it rain", "is it raining",
    )
    CALENDAR_PHRASES = (
        "calendar", "schedule", "agenda", "next meeting", "next event",
        "appointments", "am i free", "do i have anything", "what do i have today",
        "what's on my calendar", "what is on my calendar",
    )
    EMAIL_PHRASES = (
        "email", "emails", "mail", "inbox", "unread message",
        "message from", "latest message in this thread",
    )

    def __init__(self, brain, weather_service=None, calendar_service=None, email_service=None):
        self.brain = brain
        self.weather_service = weather_service
        self.calendar_service = calendar_service
        self.email_service = email_service

    @staticmethod
    def _contains(text: str, phrases: tuple[str, ...]) -> bool:
        value = text.strip().lower()
        return any(phrase in value for phrase in phrases)

    def route(self, request: str) -> RoutedResponse:
        prompt = request.strip()
        if not prompt:
            return RoutedResponse("I'm online. What can I help you with?", "system")

        if self.weather_service and self._contains(prompt, self.WEATHER_PHRASES):
            result = self.weather_service.handle_request(prompt)
            if result.success:
                return RoutedResponse(result.output, "weather")
            return RoutedResponse(f"Weather unavailable: {result.error}", "weather", False)

        if self.calendar_service and self._contains(prompt, self.CALENDAR_PHRASES):
            result = self.calendar_service.handle_request(prompt)
            if result.success:
                return RoutedResponse(result.output, "calendar")
            return RoutedResponse(f"Calendar unavailable: {result.error}", "calendar", False)

        if self.email_service and self._contains(prompt, self.EMAIL_PHRASES):
            result = self.email_service.handle_request(prompt)
            if result.success:
                return RoutedResponse(result.output, "email")
            return RoutedResponse(f"Email unavailable: {result.error}", "email", False)

        response = self.brain.ask(prompt)
        return RoutedResponse(
            response or "I didn't receive a response from the active AI provider.",
            "ai",
            bool(response),
        )
