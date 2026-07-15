"""OpenAI provider using the Responses API."""

from __future__ import annotations

import requests

from orion.intelligence.provider import AIProvider


class OpenAIProvider(AIProvider):
    def __init__(self, model: str, api_key: str, base_url: str = "https://api.openai.com/v1", timeout: float = 60.0):
        self.model = model
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise ValueError("OpenAI API key is not configured.")

    def name(self) -> str:
        return f"openai:{self.model}"

    def chat(self, prompt: str, system_prompt: str | None = None) -> str:
        payload = {"model": self.model, "input": prompt}
        if system_prompt:
            payload["instructions"] = system_prompt
        response = requests.post(
            f"{self.base_url}/responses",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        self._raise(response)
        data = response.json()
        text = data.get("output_text")
        if text:
            return str(text).strip()
        chunks = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    chunks.append(str(content["text"]))
        return "\n".join(chunks).strip()

    def list_models(self) -> list[str]:
        response = requests.get(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        self._raise(response)
        return sorted(str(item.get("id")) for item in response.json().get("data", []) if item.get("id"))

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
            raise ConnectionError(f"OpenAI request failed: {detail or exc}") from exc
