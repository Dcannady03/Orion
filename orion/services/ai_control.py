"""Provider-neutral AI model management for Orion."""

from __future__ import annotations

from dataclasses import dataclass, field
import time


@dataclass(frozen=True)
class AIModelInfo:
    name: str
    size_bytes: int = 0
    family: str = "Unknown"
    parameter_size: str = "Unknown"
    quantization: str = "Unknown"
    context_length: int = 0
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    @property
    def size_label(self) -> str:
        if not self.size_bytes:
            return "Unknown"
        return f"{self.size_bytes / (1024 ** 3):.1f} GB"

    @property
    def context_label(self) -> str:
        if not self.context_length:
            return "Unknown"
        if self.context_length >= 1000:
            return f"{round(self.context_length / 1000)}K"
        return str(self.context_length)

    @property
    def tags(self) -> tuple[str, ...]:
        text = f"{self.name} {self.family}".lower()
        tags = []
        caps = {item.lower() for item in self.capabilities}
        if "vision" in caps or any(word in text for word in ("vision", "vl", "llava")):
            tags.append("Vision")
        if "embedding" in caps or "embed" in text:
            tags.append("Embeddings")
        if any(word in text for word in ("whisper", "speech")):
            tags.append("Speech")
        if any(word in text for word in ("coder", "code", "qwen")) and "Speech" not in tags:
            tags.append("Coding")
        if any(word in text for word in ("reason", "qwq", "deepseek-r1")):
            tags.append("Reasoning")
        if self._parameter_billions() and self._parameter_billions() <= 10:
            tags.append("Fast")
        elif self.size_bytes and self.size_bytes <= 8 * 1024 ** 3:
            tags.append("Fast")
        if not tags:
            tags.append("Chat")
        return tuple(tags)

    def _parameter_billions(self) -> float | None:
        value = self.parameter_size.lower().replace("parameters", "").strip()
        candidates = [value]
        tail = self.name.lower().rsplit(":", 1)[-1]
        if tail != self.name.lower():
            candidates.append(tail)
        for candidate in candidates:
            try:
                if candidate.endswith("b"):
                    return float(candidate[:-1])
                if candidate.endswith("m"):
                    return float(candidate[:-1]) / 1000
            except ValueError:
                continue
        return None


class AIControlService:
    """Coordinates model discovery, selection, profiles, and quick benchmarks."""

    PROFILES = {
        "coding": {"description": "Low-temperature software development", "temperature": 0.2, "preference": "coding"},
        "creative": {"description": "More varied writing and ideation", "temperature": 0.9, "preference": "general"},
        "lightweight": {"description": "Prioritize low resource usage and speed", "temperature": 0.4, "preference": "fast"},
        "vision": {"description": "Prioritize image-capable models", "temperature": 0.3, "preference": "vision"},
        "balanced": {"description": "General-purpose default behavior", "temperature": 0.5, "preference": "general"},
    }

    def __init__(self, provider, config_manager):
        self.provider = provider
        self.config_manager = config_manager

    def models(self) -> list[AIModelInfo]:
        # Respect instance-level list_models overrides used by plugins/tests.
        if "list_models" in getattr(self.provider, "__dict__", {}):
            return [AIModelInfo(name=name) for name in self.provider.list_models()]
        if hasattr(self.provider, "list_model_details"):
            return self.provider.list_model_details()
        return [AIModelInfo(name=name) for name in self.provider.list_models()]

    def select(self, model: str, persist: bool = False) -> tuple[str, str]:
        names = [item.name for item in self.models()]
        match = self._match_model(model, names)
        if not match:
            raise ValueError(f"Installed model not found: {model}")
        previous = self.provider.model
        if match == previous:
            return previous, match
        self.provider.select_model(match)
        # Real Ollama providers can preload the selected model. Instance-level
        # list_models overrides are test/plugin doubles and intentionally skip I/O.
        if hasattr(self.provider, "warm_model") and "list_models" not in getattr(self.provider, "__dict__", {}):
            try:
                self.provider.warm_model(match)
            except Exception:
                self.provider.select_model(previous)
                raise
        if persist:
            try:
                self.set_default(match)
            except OSError:
                self.provider.select_model(previous)
                raise
        return previous, match

    def default_model(self) -> str:
        """Return the model Orion will load on its next startup."""
        return self.config_manager.get("providers.ollama.model", self.provider.model)

    def set_default(self, model: str) -> str:
        """Persist an installed model as Orion's startup default."""
        names = [item.name for item in self.models()]
        match = self._match_model(model, names)
        if not match:
            raise ValueError(f"Installed model not found: {model}")
        self.config_manager.set("providers.ollama.model", match)
        self.config_manager.save()
        return match

    def recommend(self, purpose: str, models: list[AIModelInfo] | None = None) -> AIModelInfo | None:
        items = models if models is not None else self.models()
        if not items:
            return None
        purpose = purpose.lower().strip()
        if purpose in {"fast", "fastest", "light", "lightweight"}:
            return min(
                items,
                key=lambda item: (
                    item.size_bytes if item.size_bytes else (item._parameter_billions() or 10**9) * 1024**3,
                    item.name.casefold(),
                ),
            )
        if purpose in {"vision", "image", "images"}:
            candidates = [item for item in items if "Vision" in item.tags]
            return max(candidates, key=self._quality_key) if candidates else None
        if purpose in {"coding", "code", "developer"}:
            candidates = [item for item in items if "Coding" in item.tags and "Speech" not in item.tags]
            return max(candidates, key=self._quality_key) if candidates else None
        if purpose in {"reasoning", "best", "overall", "general"}:
            chat = [item for item in items if "Speech" not in item.tags and "Embeddings" not in item.tags]
            return max(chat or items, key=self._quality_key)
        return None

    def activate_profile(self, name: str) -> dict:
        key = name.lower().strip()
        if key not in self.PROFILES:
            raise ValueError(f"Unknown AI profile: {name}")
        profile = dict(self.PROFILES[key])
        model = self.recommend(profile["preference"])
        if model:
            self.select(model.name, persist=True)
        self.config_manager.set("ai.active_profile", key)
        self.config_manager.set("ai.temperature", profile["temperature"])
        self.config_manager.save()
        profile["model"] = self.provider.model
        profile["name"] = key
        return profile

    def quick_benchmark(self, model: str, prompt: str = "Reply with exactly: Orion benchmark ready.") -> dict:
        previous = self.provider.model
        self.provider.select_model(model)
        started = time.perf_counter()
        try:
            response = self.provider.chat(prompt)
            elapsed = time.perf_counter() - started
        finally:
            self.provider.select_model(previous)
        return {"model": model, "seconds": elapsed, "response": response}

    @staticmethod
    def _quality_key(item: AIModelInfo):
        params = item._parameter_billions() or 0
        return (params, item.context_length, item.size_bytes, item.name.casefold())

    @staticmethod
    def _match_model(query: str, names: list[str]) -> str | None:
        wanted = query.strip().casefold()
        exact = next((name for name in names if name.casefold() == wanted), None)
        if exact:
            return exact
        matches = [name for name in names if wanted in name.casefold()]
        return matches[0] if len(matches) == 1 else None
