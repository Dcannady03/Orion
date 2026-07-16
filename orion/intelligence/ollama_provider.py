"""
Ollama AI Provider

Connects Orion to a local Ollama server.
"""

import json
from urllib import request, error

from orion.intelligence.provider import AIProvider
from orion.services.ai_control import AIModelInfo


class OllamaProvider(AIProvider):
    """AI provider for local Ollama models."""

    def __init__(self, base_url: str, model: str, timeout: float = 45.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def name(self) -> str:
        return f"ollama:{self.model}"

    def _fetch_tags(self) -> dict:
        url = f"{self.base_url}/api/tags"
        req = request.Request(url, method="GET")
        try:
            with request.urlopen(req, timeout=10) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except (error.URLError, TimeoutError) as exc:
            raise ConnectionError(
                f"Could not connect to Ollama at {self.base_url}. "
                "Make sure Ollama is running."
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectionError("Ollama returned an invalid model list.") from exc

    def list_models(self) -> list[str]:
        """Return model names currently installed in Ollama."""
        return [item.name for item in self.list_model_details()]

    def list_model_details(self) -> list[AIModelInfo]:
        """Return installed models with metadata supplied by Ollama."""
        models = []
        seen = set()
        for item in self._fetch_tags().get("models", []):
            name = str(item.get("name") or item.get("model") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            details = item.get("details") or {}
            models.append(AIModelInfo(
                name=name,
                size_bytes=int(item.get("size") or 0),
                family=str(details.get("family") or "Unknown"),
                parameter_size=str(details.get("parameter_size") or "Unknown"),
                quantization=str(details.get("quantization_level") or "Unknown"),
                context_length=int(details.get("context_length") or item.get("context_length") or 0),
                capabilities=tuple(item.get("capabilities") or ()),
            ))
        return sorted(models, key=lambda item: item.name.casefold())

    def select_model(self, model: str) -> None:
        """Switch the active model for future requests."""
        value = model.strip()
        if not value:
            raise ValueError("Model name cannot be empty.")
        self.model = value


    def warm_model(self, model: str | None = None, keep_alive: str = "5m") -> None:
        """Load a model into Ollama memory before Orion accepts the next prompt."""
        target = (model or self.model).strip()
        if not target:
            raise ValueError("Model name cannot be empty.")
        url = f"{self.base_url}/api/generate"
        payload = {"model": target, "prompt": "", "stream": False, "keep_alive": keep_alive}
        req = request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with request.urlopen(req, timeout=300) as response:
                json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError) as exc:
            raise ConnectionError(
                f"Could not load {target} in Ollama at {self.base_url}."
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectionError("Ollama returned an invalid model-load response.") from exc

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
            with request.urlopen(req, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                result = json.loads(body)
                return result.get("response", "").strip()

        except error.URLError as exc:
            raise ConnectionError(
                f"Could not connect to Ollama at {self.base_url}. "
                "Make sure Ollama is running."
            ) from exc
