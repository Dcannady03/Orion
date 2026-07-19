"""Persistent, provider-neutral AI Team role assignment and validation."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Iterable


ACTIVE_PLANNING_MODEL = "active-planning-model"
SUPPORTED_PROVIDERS = frozenset({"ollama", "openai", "gemini"})
ROLE_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,63}")
SECRET_LIKE_PATTERNS = (
    re.compile(r"^sk-[A-Za-z0-9_-]{8,}$"),
    re.compile(r"^AIza[A-Za-z0-9_-]{8,}$"),
    re.compile(r"^(?:ghp|github_pat|xox[abprs])_[A-Za-z0-9_-]{8,}$", re.IGNORECASE),
)


@dataclass(frozen=True)
class TeamRoleSpec:
    role: str
    display_name: str
    category: str
    capability: str
    default_assignment: str
    uses_model: bool
    fallback_enabled: bool


ROLE_SPECS = (
    TeamRoleSpec(
        "architect",
        "Architect",
        "Planning model",
        "Structured architecture planning",
        ACTIVE_PLANNING_MODEL,
        True,
        True,
    ),
    TeamRoleSpec(
        "engineer_reviewer",
        "Engineering Reviewer",
        "Validation role (planning model)",
        "Structured implementation-plan review",
        ACTIVE_PLANNING_MODEL,
        True,
        True,
    ),
    TeamRoleSpec(
        "implementation",
        "Implementation Engine",
        "Execution engine",
        "Workspace-confined implementation",
        "codex",
        False,
        False,
    ),
    TeamRoleSpec(
        "tester",
        "Tester",
        "Validation role (execution engine)",
        "Bounded local test execution",
        "codex",
        False,
        False,
    ),
    TeamRoleSpec(
        "documentation",
        "Documentation Reviewer",
        "Validation role (planning model)",
        "Structured documentation review",
        ACTIVE_PLANNING_MODEL,
        True,
        True,
    ),
)
ROLE_SPEC_BY_NAME = {item.role: item for item in ROLE_SPECS}
ROLE_ALIASES = {
    "engineer": "engineer_reviewer",
    "engineering_reviewer": "engineer_reviewer",
    "reviewer": "engineer_reviewer",
    "docs": "documentation",
    "documentation_reviewer": "documentation",
}
ROLE_AGENT_KEYS = {
    "architect": ("team.roles.architect.agent", "architect"),
    "engineer_reviewer": ("team.roles.engineer_reviewer.agent", "engineer"),
    "documentation": ("team.roles.documentation.agent", "reviewer"),
}
LEGACY_ROLE_KEYS = {
    "architect": "architect",
    "engineer_reviewer": "engineer",
    "documentation": "reviewer",
}


def normalize_team_role(value: str) -> str:
    normalized = re.sub(r"[-\s]+", "_", str(value).strip().lower())
    normalized = ROLE_ALIASES.get(normalized, normalized)
    if not ROLE_NAME_PATTERN.fullmatch(normalized) or normalized not in ROLE_SPEC_BY_NAME:
        choices = ", ".join(item.role for item in ROLE_SPECS)
        raise ValueError(f"Unknown AI Team role: {value}. Choose one of: {choices}")
    return normalized


def _exact_mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    missing = sorted(fields - set(value))
    unknown = sorted(set(value) - fields)
    if missing:
        raise ValueError(f"{label} is missing required fields: {missing}")
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {unknown}")
    return value


def _required_string(value: Any, label: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be {maximum} characters or fewer.")
    return normalized


@dataclass(frozen=True)
class TeamRoleSnapshot:
    role: str
    display_name: str
    category: str
    requested_assignment: str
    actual_assignment: str
    available: bool
    capability: str
    fallback: str
    fallback_reason: str
    source: str

    @classmethod
    def from_value(cls, value: Any) -> "TeamRoleSnapshot":
        fields = {
            "role", "display_name", "category", "requested_assignment",
            "actual_assignment", "available", "capability", "fallback",
            "fallback_reason", "source",
        }
        value = _exact_mapping(value, fields, "AI Team role snapshot")
        role = normalize_team_role(value["role"])
        if not isinstance(value["available"], bool):
            raise ValueError("AI Team role availability must be true or false.")
        source = _required_string(value["source"], "AI Team role assignment source", 40)
        if source not in {"default", "user-configured"}:
            raise ValueError("AI Team role assignment source is invalid.")
        fallback_reason = value["fallback_reason"]
        if not isinstance(fallback_reason, str) or len(fallback_reason) > 500:
            raise ValueError("AI Team role fallback reason must be a bounded string.")
        return cls(
            role=role,
            display_name=_required_string(value["display_name"], "AI Team role display name", 100),
            category=_required_string(value["category"], "AI Team role category", 100),
            requested_assignment=_required_string(
                value["requested_assignment"], "AI Team requested assignment", 300
            ),
            actual_assignment=_required_string(
                value["actual_assignment"], "AI Team actual assignment", 300
            ),
            available=value["available"],
            capability=_required_string(value["capability"], "AI Team role capability", 200),
            fallback=_required_string(value["fallback"], "AI Team role fallback", 200),
            fallback_reason=fallback_reason,
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "display_name": self.display_name,
            "category": self.category,
            "requested_assignment": self.requested_assignment,
            "actual_assignment": self.actual_assignment,
            "available": self.available,
            "capability": self.capability,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "source": self.source,
        }


@dataclass(frozen=True)
class ResolvedTeamRole:
    spec: TeamRoleSpec
    requested_assignment: str
    actual_assignment: str
    source: str
    available: bool
    fallback: str
    fallback_reason: str = ""
    provider: str = ""
    model: str = ""
    engine_id: str = ""
    availability_reason: str = ""
    agent_id: str = ""
    agent_name: str = ""

    @property
    def role(self) -> str:
        return self.spec.role

    @property
    def name(self) -> str:
        return self.spec.role

    @property
    def display_name(self) -> str:
        return self.spec.display_name

    @property
    def category(self) -> str:
        return self.spec.category

    @property
    def capability(self) -> str:
        return self.spec.capability

    @property
    def active(self) -> bool:
        return self.role in {"architect", "engineer_reviewer"}

    def snapshot(self) -> TeamRoleSnapshot:
        return TeamRoleSnapshot(
            role=self.role,
            display_name=self.display_name,
            category=self.category,
            requested_assignment=self.requested_assignment,
            actual_assignment=self.actual_assignment,
            available=self.available,
            capability=self.capability,
            fallback=self.fallback,
            fallback_reason=self.fallback_reason or self.availability_reason,
            source=self.source,
        )


class TeamRoleRegistry:
    """Resolve and persist the five Orion-controlled AI Team workflow roles."""

    CONFIG_ROOT = "team.assignments"

    def __init__(
        self,
        config_manager,
        provider_manager=None,
        routing_service=None,
        execution_engines=None,
        agent_registry=None,
    ) -> None:
        self.config = config_manager
        self.provider_manager = provider_manager
        self.routing = routing_service
        self.execution_engines = execution_engines
        self.agents = agent_registry

    def roles(self, prompt: str = "AI Team planning") -> tuple[ResolvedTeamRole, ...]:
        return tuple(self.status(item.role, prompt=prompt) for item in ROLE_SPECS)

    def status(self, role: str, *, prompt: str = "AI Team planning") -> ResolvedTeamRole:
        normalized = normalize_team_role(role)
        spec = ROLE_SPEC_BY_NAME[normalized]
        requested, source = self._requested_assignment(normalized)
        fallback = self._fallback_label(spec)
        try:
            agent = self.agent(normalized)
            if spec.uses_model:
                candidates = self.planning_candidates(normalized, prompt, validate_agent=False)
                resolved = candidates[0]
            else:
                resolved = self._resolve_engine(spec, requested, source)
            if agent is not None:
                resolved = replace(
                    resolved,
                    agent_id=agent.agent_id,
                    agent_name=agent.name,
                )
            return resolved
        except (ConnectionError, FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            return ResolvedTeamRole(
                spec=spec,
                requested_assignment=requested,
                actual_assignment=requested,
                source=source,
                available=False,
                fallback=fallback,
                availability_reason=self._safe_reason(exc),
            )

    def show(self, role: str, *, prompt: str = "AI Team planning") -> ResolvedTeamRole:
        return self.status(role, prompt=prompt)

    def set(self, role: str, assignment: str) -> ResolvedTeamRole:
        normalized = normalize_team_role(role)
        spec = ROLE_SPEC_BY_NAME[normalized]
        value = self._validate_assignment_text(spec, assignment)
        if spec.uses_model:
            provider, model = self._parse_provider_model(value)
            self._provider_resolution(spec, value, "user-configured", provider, model)
            self.agent(normalized)
        else:
            self._resolve_engine(spec, value, "user-configured")
        self._persist(normalized, value)
        return self.status(normalized)

    def reset(self, role: str) -> ResolvedTeamRole:
        normalized = normalize_team_role(role)
        default = ROLE_SPEC_BY_NAME[normalized].default_assignment
        self._persist(normalized, default)
        return self.status(normalized)

    def planning_candidates(
        self,
        role: str,
        prompt: str,
        *,
        validate_agent: bool = True,
    ) -> tuple[ResolvedTeamRole, ...]:
        normalized = normalize_team_role(role)
        spec = ROLE_SPEC_BY_NAME[normalized]
        if not spec.uses_model:
            raise ValueError(f"{spec.display_name} is not assigned to a planning model.")
        if validate_agent:
            self.agent(normalized)
        requested, source = self._requested_assignment(normalized)
        fallback = self._fallback_label(spec)
        configured_default = requested == ACTIVE_PLANNING_MODEL
        if configured_default:
            provider = str(self.config.get("providers.default", "ollama")).strip().lower()
            model = str(self.config.get(f"providers.{provider}.model", "")).strip()
        else:
            provider, model = self._parse_provider_model(requested)

        first_error = ""
        candidates: list[ResolvedTeamRole] = []
        try:
            candidates.append(
                self._provider_resolution(spec, requested, source, provider, model)
            )
        except (ConnectionError, OSError, RuntimeError, ValueError) as exc:
            first_error = self._safe_reason(exc)
            if not configured_default:
                raise ValueError(
                    f"{spec.display_name} assignment is unavailable: {first_error}"
                ) from exc

        for fallback_provider in self._routing_order(prompt):
            fallback_model = str(
                self.config.get(f"providers.{fallback_provider}.model", "")
            ).strip()
            actual = f"{fallback_provider}:{fallback_model}"
            if any(item.actual_assignment.casefold() == actual.casefold() for item in candidates):
                continue
            try:
                resolved = self._provider_resolution(
                    spec,
                    requested,
                    source,
                    fallback_provider,
                    fallback_model,
                )
            except (ConnectionError, OSError, RuntimeError, ValueError):
                continue
            reason = (
                f"Active planning assignment unavailable ({first_error}); "
                f"selected {actual} through {self.routing_profile()} routing."
                if first_error
                else f"Runtime fallback through {self.routing_profile()} routing."
            )
            candidates.append(replace(resolved, fallback_reason=reason))

        if not candidates:
            raise ValueError(f"No available planning model can perform {spec.display_name}.")
        return tuple(candidates)

    def engine(self, role: str):
        normalized = normalize_team_role(role)
        spec = ROLE_SPEC_BY_NAME[normalized]
        if spec.uses_model:
            raise ValueError(f"{spec.display_name} is not assigned to an execution engine.")
        requested, source = self._requested_assignment(normalized)
        resolved = self._resolve_engine(spec, requested, source)
        if self.execution_engines is None:
            return None
        return self.execution_engines.engine(resolved.engine_id)

    def agent(self, role: str):
        normalized = normalize_team_role(role)
        if self.agents is None or normalized not in ROLE_AGENT_KEYS:
            return None
        key, default_agent = ROLE_AGENT_KEYS[normalized]
        configured = self.config.get(key, None)
        if configured is None and normalized == "engineer_reviewer":
            configured = self.config.get("team.roles.engineer.agent", default_agent)
        elif configured is None and normalized == "documentation":
            configured = self.config.get("team.roles.reviewer.agent", default_agent)
        agent_id = str(configured or default_agent).strip()
        agent = self.agents.load(agent_id)
        if not agent.enabled:
            raise ValueError(
                f"{ROLE_SPEC_BY_NAME[normalized].display_name} is assigned to disabled agent: "
                f"{agent.agent_id}"
            )
        return agent

    def _requested_assignment(self, role: str) -> tuple[str, str]:
        spec = ROLE_SPEC_BY_NAME[role]
        key = f"{self.CONFIG_ROOT}.{role}"
        configured = str(self.config.get(key, spec.default_assignment)).strip()
        source = "user-configured" if self._is_user_configured(key) else "default"

        # Existing pre-registry provider/model settings remain readable when a
        # new assignment has not been explicitly persisted.
        if source == "default" and role in LEGACY_ROLE_KEYS:
            legacy = LEGACY_ROLE_KEYS[role]
            provider = str(
                self.config.get(f"team.roles.{legacy}.provider", "configured-default")
            ).strip().lower()
            model = str(
                self.config.get(f"team.roles.{legacy}.model", "configured-default")
            ).strip()
            if provider != "configured-default":
                if model == "configured-default":
                    model = str(self.config.get(f"providers.{provider}.model", "")).strip()
                configured = f"{provider}:{model}"
                source = "user-configured"
            elif self.agents is not None and role in ROLE_AGENT_KEYS:
                agent = self.agent(role)
                if agent is not None and agent.provider != "configured-default":
                    agent_provider = agent.provider
                    agent_model = (
                        str(self.config.get(f"providers.{agent_provider}.model", "")).strip()
                        if agent.model == "configured-default"
                        else agent.model
                    )
                    configured = f"{agent_provider}:{agent_model}"
                    source = "user-configured"
        return self._validate_assignment_text(spec, configured), source

    def _provider_resolution(
        self,
        spec: TeamRoleSpec,
        requested: str,
        source: str,
        provider: str,
        model: str,
    ) -> ResolvedTeamRole:
        provider = provider.strip().lower()
        model = model.strip()
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Provider is not recognized: {provider}")
        if not model:
            raise ValueError(f"No model is configured for {provider}.")
        if self._looks_like_secret(model):
            raise ValueError("Role assignments cannot contain credentials or API keys.")

        if self.provider_manager is not None:
            statuses = {item.key: item for item in self.provider_manager.statuses()}
            status = statuses.get(provider)
            if status is None:
                raise ValueError(f"Provider is not registered: {provider}")
            if not status.enabled:
                raise ValueError(f"Provider is disabled: {provider}")
            if not status.configured:
                raise ValueError(f"Provider is not configured: {provider}")
            models = tuple(str(item).strip() for item in self.provider_manager.models(provider))
            if not any(item.casefold() == model.casefold() for item in models):
                raise ValueError(f"Model is not available for {provider}: {model}")
        elif not bool(self.config.get(f"providers.{provider}.enabled", True)):
            raise ValueError(f"Provider is disabled: {provider}")

        actual = f"{provider}:{model}"
        return ResolvedTeamRole(
            spec=spec,
            requested_assignment=requested,
            actual_assignment=actual,
            source=source,
            available=True,
            fallback=self._fallback_label(spec),
            provider=provider,
            model=model,
        )

    def _resolve_engine(
        self,
        spec: TeamRoleSpec,
        requested: str,
        source: str,
    ) -> ResolvedTeamRole:
        engine_id = requested.strip().lower()
        if not engine_id or ":" in engine_id:
            raise ValueError(f"{spec.display_name} requires an execution-engine ID.")
        if self.execution_engines is not None:
            try:
                engine = self.execution_engines.engine(engine_id)
            except ValueError as exc:
                raise ValueError(f"Execution engine is not recognized: {engine_id}") from exc
            if not engine.installed:
                raise ValueError(f"Execution engine is not installed: {engine_id}")
            if not engine.cli_support:
                raise ValueError(f"Execution engine has no CLI support: {engine_id}")
            if not engine.implementation_supported:
                raise ValueError(
                    f"Execution engine lacks Orion's required adapter capability: {engine_id}"
                )
            if not engine.ready_for_implementation:
                raise ValueError(f"Execution engine is not ready: {engine_id}")
        elif engine_id != "codex":
            raise ValueError(f"Execution engine is not recognized: {engine_id}")
        return ResolvedTeamRole(
            spec=spec,
            requested_assignment=requested,
            actual_assignment=engine_id,
            source=source,
            available=True,
            fallback=self._fallback_label(spec),
            engine_id=engine_id,
        )

    def _routing_order(self, prompt: str) -> tuple[str, ...]:
        if self.routing is not None:
            return tuple(self.routing.provider_order(prompt))
        default = str(self.config.get("providers.default", "ollama")).strip().lower()
        return tuple(dict.fromkeys((default, "ollama", "openai", "gemini")))

    def routing_profile(self) -> str:
        return str(getattr(self.routing, "profile", self.config.get("ai.routing.profile", "balanced")))

    def _fallback_label(self, spec: TeamRoleSpec) -> str:
        if not spec.fallback_enabled:
            return "None; unavailable assignments fail closed"
        return f"{self.routing_profile().title()} provider routing"

    def _persist(self, role: str, assignment: str) -> None:
        if not hasattr(self.config, "set") or not hasattr(self.config, "save"):
            raise RuntimeError("AI Team role configuration is read-only.")
        self.config.set(f"{self.CONFIG_ROOT}.{role}", assignment)
        self.config.save()

    def _is_user_configured(self, key: str) -> bool:
        local = getattr(self.config, "local_config", None)
        if isinstance(local, dict) and self._mapping_has_path(local, key):
            return True
        values = getattr(self.config, "values", None)
        return isinstance(values, dict) and key in values

    @staticmethod
    def _mapping_has_path(value: dict[str, Any], key: str) -> bool:
        current: Any = value
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        return True

    @staticmethod
    def _validate_assignment_text(spec: TeamRoleSpec, assignment: str) -> str:
        value = _required_string(assignment, f"{spec.display_name} assignment", 300)
        if any(character in value for character in "\r\n\t"):
            raise ValueError("AI Team role assignments cannot contain control characters.")
        if spec.uses_model:
            if value != ACTIVE_PLANNING_MODEL:
                TeamRoleRegistry._parse_provider_model(value)
        elif ":" in value:
            raise ValueError(f"{spec.display_name} requires an execution-engine ID.")
        return value

    @staticmethod
    def _parse_provider_model(value: str) -> tuple[str, str]:
        if ":" not in value:
            raise ValueError("Planning-model assignments must use provider:model.")
        provider, model = value.split(":", 1)
        provider = provider.strip().lower()
        model = model.strip()
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Provider is not recognized: {provider}")
        if not model:
            raise ValueError("Planning-model assignments require a model name.")
        if TeamRoleRegistry._looks_like_secret(model):
            raise ValueError("Role assignments cannot contain credentials or API keys.")
        return provider, model

    @staticmethod
    def _looks_like_secret(value: str) -> bool:
        return any(pattern.fullmatch(str(value).strip()) for pattern in SECRET_LIKE_PATTERNS)

    @staticmethod
    def _safe_reason(exc: BaseException) -> str:
        text = str(exc)
        safe_prefixes = (
            "Provider is not recognized", "Provider is not registered", "Provider is disabled",
            "Provider is not configured", "Model is not available", "No model is configured",
            "Execution engine is not recognized", "Execution engine is not installed",
            "Execution engine has no CLI support", "Execution engine lacks Orion's required adapter",
            "Execution engine is not ready", "AI Team role", "Architect", "Engineering Reviewer",
            "Documentation Reviewer", "No available planning model",
        )
        if text.startswith(safe_prefixes):
            return text[:500]
        return f"Unavailable ({type(exc).__name__})."


def role_snapshots(values: Iterable[ResolvedTeamRole]) -> list[TeamRoleSnapshot]:
    snapshots = [value.snapshot() for value in values]
    if len({item.role for item in snapshots}) != len(snapshots):
        raise ValueError("AI Team role snapshots cannot contain duplicate roles.")
    return snapshots
