"""
Orion Intent Detection

A small placeholder intent detector.
This will grow into the system that helps Orion decide whether a request is:
- general chat
- coding help
- memory
- a skill/tool request
- an automation request
"""


class IntentDetector:
    """Detects the rough intent of a user request."""

    def detect(self, text: str) -> str:
        """Return a simple intent label for the provided text."""
        text_lower = text.strip().lower()

        if not text_lower:
            return "empty"

        coding_keywords = [
            "code",
            "python",
            "function",
            "class",
            "bug",
            "error",
            "traceback",
            "refactor",
            "script",
        ]

        if any(keyword in text_lower for keyword in coding_keywords):
            return "coding"

        return "chat"
