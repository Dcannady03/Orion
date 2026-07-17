"""Transparent multi-provider routing for Orion intelligence requests."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Iterable

from orion.intelligence.factory import AIProviderFactory
from orion.services.ai_performance import AIPerformanceStore


@dataclass(frozen=True)
class RouteDecision:
    profile: str
    task_type: str
    provider: str
    model: str
    reason: str
    fallbacks: tuple[str, ...]
    duration_seconds: float
    success: bool
    error: str = ""
    timestamp: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class AIRoutingService:
    """Choose the quickest suitable configured provider for each request.

    The first implementation intentionally uses deterministic rules. It is
    explainable, easy to test, and always lets the user disable routing or
    select a profile explicitly.
    """

    PROFILES = {
        "fast": "Prefer the fastest local model for everyday requests.",
        "balanced": "Use local AI for simple work and cloud AI for harder tasks.",
        "coding": "Prefer OpenAI for coding and architecture, with local fallback.",
        "research": "Prefer Gemini for long-context research, then OpenAI.",
    }

    COMPLEX_TERMS = {
        "analyze", "architecture", "compare", "debug", "diagnose", "design",
        "evaluate", "investigate", "reason", "refactor", "review", "strategy",
    }
    CODING_TERMS = {
        "bug", "class", "code", "commit", "function", "git", "python",
        "refactor", "repository", "script", "stack trace", "test", "traceback",
    }
    RESEARCH_TERMS = {
        "research", "sources", "citations", "paper", "report", "evidence",
        "literature", "long document", "summarize document",
    }

    def __init__(self, config_manager, provider_manager, performance_store: AIPerformanceStore | None = None):
        self.config = config_manager
        self.provider_manager = provider_manager
        self.performance = performance_store or AIPerformanceStore()
        self.last_decision: RouteDecision | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("ai.routing.enabled", True))

    @property
    def profile(self) -> str:
        value = str(self.config.get("ai.routing.profile", self.config.get("ai.active_profile", "balanced"))).lower()
        return value if value in self.PROFILES else "balanced"

    def set_enabled(self, enabled: bool) -> None:
        self.config.set("ai.routing.enabled", bool(enabled))
        self.config.save()

    def set_profile(self, profile: str) -> str:
        key = profile.lower().strip()
        if key not in self.PROFILES:
            raise ValueError(f"Unknown routing profile: {profile}")
        self.config.set("ai.routing.profile", key)
        self.config.set("ai.active_profile", key)
        self.config.save()
        return key

    def classify(self, prompt: str) -> tuple[str, str]:
        text = prompt.casefold()
        words = text.split()
        if any(term in text for term in self.RESEARCH_TERMS):
            return "research", "Research or long-context request"
        if any(term in text for term in self.CODING_TERMS):
            return "coding", "Coding or software-engineering request"
        if len(words) >= 80 or len(prompt) >= 500 or any(term in text for term in self.COMPLEX_TERMS):
            return "complex", "Complex reasoning request"
        return "conversation", "Short conversational request"

    def provider_order(self, prompt: str, profile: str | None = None) -> tuple[str, ...]:
        selected = profile or self.profile
        task_type, _ = self.classify(prompt)
        if selected == "fast":
            order = ("ollama", "openai", "gemini")
        elif selected == "coding":
            order = ("openai", "ollama", "gemini")
        elif selected == "research":
            order = ("gemini", "openai", "ollama")
        elif task_type in {"coding", "complex", "research"}:
            order = ("openai", "gemini", "ollama")
        else:
            order = ("ollama", "openai", "gemini")
        ready = tuple(key for key in order if self._ready(key))
        if not bool(self.config.get("ai.routing.adaptive", True)):
            return ready
        minimum = int(self.config.get("ai.routing.minimum_health_samples", 3))
        position = {key: index for index, key in enumerate(ready)}
        health_rank = {"healthy": 0, "learning": 0, "degraded": 1, "unhealthy": 2}
        return tuple(sorted(ready, key=lambda key: (
            health_rank[self.performance.provider_health(key, minimum_samples=minimum)["state"]],
            position[key],
        )))

    def route_chat(self, prompt: str, *, system_prompt: str | None = None) -> str:
        profile = self.profile
        task_type, reason = self.classify(prompt)
        order = self.provider_order(prompt, profile)
        if not order:
            raise ConnectionError("No configured AI provider is available for routing.")

        errors: list[str] = []
        started = perf_counter()
        for index, provider_key in enumerate(order):
            provider_started = perf_counter()
            try:
                provider = AIProviderFactory(self.config, self.provider_manager.secrets).create(provider_key)
                response = provider.chat(prompt, system_prompt=system_prompt)
                duration = perf_counter() - provider_started
                self.performance.record(provider_key, str(getattr(provider, "model", "unknown")), duration, True)
                self.last_decision = RouteDecision(
                    profile=profile,
                    task_type=task_type,
                    provider=provider_key,
                    model=str(getattr(provider, "model", "unknown")),
                    reason=reason if index == 0 else f"Fallback after: {'; '.join(errors)}",
                    fallbacks=tuple(order[index + 1:]),
                    duration_seconds=round(duration, 3),
                    success=True,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                return response
            except (ConnectionError, TimeoutError, OSError, ValueError) as exc:
                duration = perf_counter() - provider_started
                model = str(self.config.get(f"providers.{provider_key}.model", "unknown"))
                self.performance.record(provider_key, model, duration, False, str(exc))
                errors.append(f"{provider_key}: {exc}")

        duration = perf_counter() - started
        self.last_decision = RouteDecision(
            profile=profile,
            task_type=task_type,
            provider=order[-1],
            model=str(self.config.get(f"providers.{order[-1]}.model", "unknown")),
            reason=reason,
            fallbacks=(),
            duration_seconds=round(duration, 3),
            success=False,
            error="; ".join(errors),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        raise ConnectionError(f"All routed AI providers failed: {'; '.join(errors)}")

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "profile": self.profile,
            "available_profiles": dict(self.PROFILES),
            "ready_providers": [item.key for item in self.provider_manager.statuses() if item.enabled and item.configured],
            "adaptive": bool(self.config.get("ai.routing.adaptive", True)),
            "provider_health": [self.performance.provider_health(item.key, minimum_samples=int(self.config.get("ai.routing.minimum_health_samples", 3))) for item in self.provider_manager.statuses() if item.enabled and item.configured],
            "last_decision": self.last_decision.as_dict() if self.last_decision else None,
        }

    def _ready(self, provider: str) -> bool:
        return any(
            item.key == provider and item.enabled and item.configured
            for item in self.provider_manager.statuses()
        )
