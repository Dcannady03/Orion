"""Google Gemini API provider."""

from __future__ import annotations

import requests

from orion.intelligence.provider import AIProvider


class GeminiProvider(AIProvider):
    def __init__(self, model: str, api_key: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta", timeout: float = 60.0):
        self.model = model
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise ValueError("Gemini API key is not configured.")

    def name(self) -> str:
        return f"gemini:{self.model}"

    def chat(self, prompt: str, system_prompt: str | None = None) -> str:
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        response = requests.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        self._raise(response)
        data = response.json()
        parts = []
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if part.get("text"):
                    parts.append(str(part["text"]))
        return "\n".join(parts).strip()

    def list_models(self) -> list[str]:
        response = requests.get(
            f"{self.base_url}/models",
            headers={"x-goog-api-key": self.api_key},
            timeout=self.timeout,
        )
        self._raise(response)
        names = []
        for item in response.json().get("models", []):
            methods = item.get("supportedGenerationMethods", [])
            if "generateContent" not in methods:
                continue
            name = str(item.get("name", ""))
            if name.startswith("models/"):
                name = name[len("models/"):]
            if name:
                names.append(name)
        return sorted(names)

    def select_model(self, model: str) -> None:
        self.model = model

    @staticmethod
    def _raise(response) -> None:
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = ""
            try:
                detail = response.json().get("error", {}).get("message", "")
            except Exception:
                detail = response.text[:300]
            raise ConnectionError(f"Gemini request failed: {detail or exc}") from exc
