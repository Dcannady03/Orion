import unittest

from orion.services.request_router import RequestRouterService


class Result:
    def __init__(self, success=True, output="", error=""):
        self.success = success
        self.output = output
        self.error = error


class Service:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def handle_request(self, request):
        self.requests.append(request)
        return self.result


class Brain:
    def __init__(self):
        self.prompts = []

    def ask(self, prompt):
        self.prompts.append(prompt)
        return "AI answer"


class RequestRouterTests(unittest.TestCase):
    def test_weather_uses_weather_service_before_ai(self):
        brain = Brain()
        weather = Service(Result(output="81°F and clear"))
        router = RequestRouterService(brain, weather_service=weather)
        result = router.route("Good morning, what's the weather today?")
        self.assertEqual(result.source, "weather")
        self.assertEqual(result.output, "81°F and clear")
        self.assertEqual(brain.prompts, [])

    def test_calendar_uses_calendar_service_before_ai(self):
        brain = Brain()
        calendar = Service(Result(output="No events today"))
        router = RequestRouterService(brain, calendar_service=calendar)
        result = router.route("What's on my calendar?")
        self.assertEqual(result.source, "calendar")
        self.assertEqual(brain.prompts, [])

    def test_unknown_request_falls_back_to_ai(self):
        brain = Brain()
        router = RequestRouterService(brain)
        result = router.route("Explain recursion")
        self.assertEqual(result.source, "ai")
        self.assertEqual(brain.prompts, ["Explain recursion"])
