"""
Ollama AI Provider

Connects Orion to a local Ollama server.
"""

import json
from urllib import request, error

from orion.intelligence.provider import AIProvider


class OllamaProvider(AIProvider):
    """AI provider for local Ollama models."""

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def name(self) -> str:
        return f"ollama:{self.model}"

    def chat(self, prompt: str, system_prompt: str | None = None) -> str:
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }

        if system_prompt:
            payload["system"] = system_prompt

        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=120) as response:
                body = response.read().decode("utf-8")
                result = json.loads(body)
                return result.get("response", "").strip()

        except error.URLError as exc:
            raise ConnectionError(
                f"Could not connect to Ollama at {self.base_url}. "
                "Make sure Ollama is running."
            ) from exc
