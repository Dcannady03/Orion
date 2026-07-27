"""Agent management, scope resolution, and provider/model resolution."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from orion.agents.models import (
    AgentPermissionPolicy,
    AgentResolution,
    ManagedAgentDefinition,
    normalize_agent_id,
    utc_now,
)
from orion.agents.repository import AgentRepository
from orion.agents.templates import AgentTemplateRegistry
from orion.agents.registry import AgentResponse, AgentTestResult
from orion.agents.prompt import ORION_AGENT_OUTPUT_CONTRACT


class AgentConflictError(ValueError):
    """Raised when combined agent scopes would produce an ambiguous identity."""


class AgentManager:
    """Dedicated service for reusable permanent and workspace agent profiles."""

    is_production_agent_manager = True
    MAX_TEST_RESPONSE_CHARS = 50_000

    def __init__(
        self,
        permanent_root: str | Path,
        workspace_manager,
        config_manager,
        provider_factory=None,
        *,
        provider_manager=None,
        routing_service=None,
        template_registry: AgentTemplateRegistry | None = None,
    ) -> None:
        self.root = Path(permanent_root)
        self.workspace_manager = workspace_manager
        self.config = config_manager
        self.provider_factory = provider_factory
        self.provider_manager = provider_manager
        self.routing = routing_service
        self.template_registry = template_registry or AgentTemplateRegistry()
        self.permanent_repository = AgentRepository(self.root, "permanent")

    @property
    def workspace_root(self) -> Path:
        return Path(self.workspace_manager.root).resolve()

    @property
    def workspace_repository(self) -> AgentRepository:
        root = self.workspace_root / ".orion" / "agents"
        return AgentRepository(
            root,
            "workspace",
            workspace_boundary=self.workspace_root,
        )

    def repository(self, scope: str) -> AgentRepository:
        normalized = str(scope).strip().lower()
        if normalized == "permanent":
            return self.permanent_repository
        if normalized == "workspace":
            return self.workspace_repository
        raise ValueError("Agent scope must be permanent or workspace.")

    def create_profile(
        self,
        *,
        name: str,
        scope: str = "permanent",
        agent_id: str | None = None,
        description: str = "",
        job: str | None = None,
        specialty: str = "",
        personality: str = "Practical, direct, and collaborative.",
        instructions: str = "",
        provider: str = "auto",
        model: str = "auto",
        routing_profile: str = "balanced",
        temperature: float | None = None,
        generation: dict[str, Any] | None = None,
        capabilities: Iterable[str] = (),
        permissions: AgentPermissionPolicy | None = None,
        workspace_access: str = "read_only",
        enabled: bool = True,
    ) -> ManagedAgentDefinition:
        identity = normalize_agent_id(agent_id or name)
        agent = ManagedAgentDefinition.create(
            agent_id=identity,
            name=name,
            description=description,
            scope=scope,
            job=job,
            specialty=specialty,
            personality=personality,
            instructions=instructions,
            provider=provider,
            model=model,
            routing_profile=routing_profile,
            temperature=temperature,
            generation=generation,
            capabilities=tuple(capabilities),
            permissions=permissions,
            workspace_access=workspace_access,
            enabled=enabled,
        )
        self._ensure_identity_available(agent.agent_id)
        self.repository(scope).save(agent)
        return agent

    def create_from_template(
        self,
        template_id: str,
        *,
        scope: str = "permanent",
        name: str | None = None,
        agent_id: str | None = None,
    ) -> ManagedAgentDefinition:
        agent = self.template_registry.instantiate(
            template_id,
            scope=scope,
            name=name,
            agent_id=agent_id,
        )
        self._ensure_identity_available(agent.agent_id)
        self.repository(scope).save(agent)
        return agent

    def save(
        self,
        agent,
        *,
        overwrite: bool = False,
    ) -> Path:
        managed = self._coerce(agent)
        if not overwrite:
            self._ensure_identity_available(managed.agent_id)
        return self.repository(managed.scope).save(managed, overwrite=overwrite)

    def update(self, agent: ManagedAgentDefinition) -> ManagedAgentDefinition:
        current = self.load(agent.agent_id, scope=agent.scope)
        updated = replace(
            agent,
            metadata=replace(
                agent.metadata,
                created_at=current.metadata.created_at,
                updated_at=utc_now(),
            ),
        )
        self.repository(agent.scope).save(updated, overwrite=True)
        return updated

    def load(
        self,
        identifier: str,
        *,
        scope: str | None = None,
    ) -> ManagedAgentDefinition:
        if scope is not None:
            return self._load_from_repository(self.repository(scope), identifier)
        matches: list[ManagedAgentDefinition] = []
        for repository in (self.permanent_repository, self.workspace_repository):
            try:
                matches.append(self._load_from_repository(repository, identifier))
            except FileNotFoundError:
                continue
        if not matches:
            raise FileNotFoundError(f"Agent not found: {identifier}")
        identities = {(item.scope, item.agent_id) for item in matches}
        if len(identities) > 1:
            scopes = ", ".join(sorted(item.scope for item in matches))
            raise AgentConflictError(
                f"Agent reference is ambiguous across scopes ({scopes}): {identifier}"
            )
        return matches[0]

    def all(self, scope: str | None = None) -> tuple[ManagedAgentDefinition, ...]:
        if scope is not None:
            return self.repository(scope).all()
        permanent = self.permanent_repository.all()
        workspace = self.workspace_repository.all()
        permanent_ids = {item.agent_id for item in permanent}
        duplicates = sorted(
            item.agent_id for item in workspace if item.agent_id in permanent_ids
        )
        if duplicates:
            raise AgentConflictError(
                "Workspace agents cannot override permanent agents; conflicting IDs: "
                + ", ".join(duplicates)
            )
        return tuple(sorted((*permanent, *workspace), key=lambda item: (item.name.casefold(), item.scope)))

    def validate(self, identifier: str, *, scope: str | None = None) -> ManagedAgentDefinition:
        return self.load(identifier, scope=scope)

    def delete(self, identifier: str, *, scope: str | None = None) -> Path:
        agent = self.load(identifier, scope=scope)
        return self.repository(agent.scope).delete(agent.agent_id)

    def set_enabled(
        self,
        identifier: str,
        enabled: bool,
        *,
        scope: str | None = None,
    ) -> ManagedAgentDefinition:
        if not isinstance(enabled, bool):
            raise ValueError("Agent enabled state must be true or false.")
        agent = self.load(identifier, scope=scope)
        return self.update(replace(agent, enabled=enabled))

    def promote(self, identifier: str) -> ManagedAgentDefinition:
        workspace_agent = self.load(identifier, scope="workspace")
        self._ensure_identity_available(workspace_agent.agent_id, ignore_scope="workspace")
        promoted = workspace_agent.with_scope("permanent")
        self.permanent_repository.save(promoted)
        try:
            self.workspace_repository.delete(workspace_agent.agent_id)
        except Exception:
            try:
                self.permanent_repository.delete(promoted.agent_id)
            except Exception:
                pass
            raise
        return promoted

    def copy(
        self,
        identifier: str,
        new_name: str,
        *,
        scope: str | None = None,
        new_id: str | None = None,
    ) -> ManagedAgentDefinition:
        source = self.load(identifier)
        target_scope = scope or source.scope
        identity = normalize_agent_id(new_id or new_name)
        self._ensure_identity_available(identity)
        timestamp = utc_now()
        copied = replace(
            source,
            agent_id=identity,
            name=str(new_name).strip(),
            scope=target_scope,
            metadata=replace(
                source.metadata,
                created_at=timestamp,
                updated_at=timestamp,
            ),
            extensions={},
        )
        self.repository(target_scope).save(copied)
        return copied

    def templates(self) -> tuple[ManagedAgentDefinition, ...]:
        return self.template_registry.all()

    def ensure_defaults(self, agents: Iterable[Any]) -> None:
        """Migrate the existing role defaults without overwriting user-owned profiles."""
        for agent in agents:
            managed = self._coerce(agent)
            if not self.permanent_repository.exists(managed.agent_id):
                self.permanent_repository.save(managed)

    def resolve(
        self,
        agent: ManagedAgentDefinition,
        *,
        goal: str = "Agent request",
        provider: str = "auto",
        model: str = "auto",
    ) -> tuple[str, str]:
        resolved = self.resolution_candidates(
            agent,
            goal=goal,
            provider=provider,
            model=model,
        )[0]
        return resolved.provider, resolved.model

    def resolution_candidates(
        self,
        agent: ManagedAgentDefinition,
        *,
        goal: str,
        provider: str = "auto",
        model: str = "auto",
    ) -> tuple[AgentResolution, ...]:
        job_provider = str(provider or "auto").strip().lower()
        job_model = str(model or "auto").strip()
        requested_provider = (
            job_provider
            if job_provider != "auto"
            else agent.execution.provider
        )
        requested_model = (
            job_model if job_model != "auto" else agent.execution.model
        )
        source = (
            "job-override"
            if job_provider != "auto" or job_model != "auto"
            else "agent-preference"
            if requested_provider != "auto" or requested_model != "auto"
            else "routing"
        )
        routing_profile = agent.execution.routing_profile
        routing_order = self._routing_order(goal, routing_profile)
        if requested_provider != "auto":
            order = [requested_provider]
            if self._fallback_allowed():
                order.extend(item for item in routing_order if item != requested_provider)
        else:
            order = list(routing_order)
        if not order:
            default = str(self.config.get("providers.default", "")).strip().lower()
            if default:
                order = [default]

        results: list[AgentResolution] = []
        failures: list[str] = []
        for index, provider_key in enumerate(dict.fromkeys(order)):
            selected_model = (
                requested_model
                if requested_model != "auto"
                and (requested_provider == "auto" and index == 0 or provider_key == requested_provider)
                else str(self.config.get(f"providers.{provider_key}.model", "")).strip()
            )
            reason = self._unavailable_reason(provider_key, selected_model)
            if reason:
                failures.append(reason)
                continue
            fallback_reason = ""
            if index > 0 or failures:
                fallback_reason = (
                    f"Requested {requested_provider}:{requested_model} was unavailable; "
                    f"selected {provider_key}:{selected_model} through "
                    f"{routing_profile} routing."
                )
            results.append(AgentResolution(
                requested_provider=requested_provider,
                requested_model=requested_model,
                provider=provider_key,
                model=selected_model,
                routing_profile=routing_profile,
                source=source if not fallback_reason else "routing-fallback",
                fallback_reason=fallback_reason,
            ))
        if not results:
            detail = "; ".join(failures) or "no configured providers"
            raise ValueError(
                f"No provider/model is available for agent {agent.agent_id}: {detail}"
            )
        return tuple(results)

    def test(self, identifier: str) -> AgentTestResult:
        agent = self.load(identifier)
        if not agent.enabled:
            raise ValueError(f"Agent is disabled: {agent.agent_id}")
        if self.provider_factory is None:
            raise RuntimeError("Agent provider factory is unavailable.")
        candidates = self.resolution_candidates(
            agent,
            goal="Bounded agent configuration test",
        )
        failures: list[str] = []
        for candidate in candidates:
            try:
                provider = self.provider_factory.create(candidate.provider)
                if str(getattr(provider, "model", "")) != candidate.model:
                    if not hasattr(provider, "select_model"):
                        raise ValueError(
                            f"{candidate.provider} does not support agent-specific models."
                        )
                    provider.select_model(candidate.model)
                raw = provider.chat(
                    "Confirm that you understand the configured assignment and describe "
                    "how you would approach it safely.",
                    system_prompt=(
                        f"You are the configured Orion agent {agent.name}.\n"
                        f"Job: {agent.role.job}\n"
                        f"Personality: {agent.role.personality}\n"
                        f"Agent instructions:\n{agent.role.instructions}\n\n"
                        "This is one bounded configuration test. No tools are available. "
                        "Do not modify files, run commands, use the network, or perform Git "
                        "actions. Orion safety and approval rules override agent text.\n"
                        f"{ORION_AGENT_OUTPUT_CONTRACT}"
                    ),
                )
                if len(str(raw)) > self.MAX_TEST_RESPONSE_CHARS:
                    raise ValueError(
                        "Agent test response exceeded the 50,000-character limit."
                    )
                response = self._parse_response(raw)
                return AgentTestResult(
                    agent=agent,
                    provider=candidate.provider,
                    model=str(getattr(provider, "model", candidate.model)),
                    response=response,
                )
            except Exception as exc:
                failures.append(
                    f"{candidate.actual_assignment} ({type(exc).__name__})"
                )
        raise RuntimeError(
            "Agent test provider routing failed: " + ", ".join(failures)
        )

    @staticmethod
    def _parse_response(raw: str) -> AgentResponse:
        text = str(raw).strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return AgentResponse.from_value(json.loads(text))
        except json.JSONDecodeError as exc:
            raise ValueError("Agent test returned invalid JSON.") from exc

    def _load_from_repository(
        self,
        repository: AgentRepository,
        identifier: str,
    ) -> ManagedAgentDefinition:
        try:
            normalized = normalize_agent_id(identifier)
            if repository.exists(normalized):
                return repository.load(normalized)
        except ValueError:
            pass
        matches = [
            item for item in repository.all()
            if item.name.casefold() == str(identifier).strip().casefold()
        ]
        if not matches:
            raise FileNotFoundError(f"Agent not found: {identifier}")
        if len(matches) > 1:
            raise AgentConflictError(f"Agent name is ambiguous: {identifier}")
        return matches[0]

    def _ensure_identity_available(
        self,
        agent_id: str,
        *,
        ignore_scope: str | None = None,
    ) -> None:
        normalized = normalize_agent_id(agent_id)
        for repository in (self.permanent_repository, self.workspace_repository):
            if repository.scope == ignore_scope:
                continue
            if repository.exists(normalized):
                raise FileExistsError(
                    f"Agent ID already exists in {repository.scope} scope: {normalized}"
                )

    def _routing_order(self, goal: str, profile: str) -> tuple[str, ...]:
        if self.routing is not None:
            return tuple(self.routing.provider_order(goal, profile))
        default = str(self.config.get("providers.default", "ollama")).strip().lower()
        return tuple(dict.fromkeys((default, "ollama", "openai", "gemini")))

    def _fallback_allowed(self) -> bool:
        if self.routing is not None:
            return bool(getattr(self.routing, "enabled", True))
        return bool(self.config.get("ai.routing.enabled", True))

    def _unavailable_reason(self, provider: str, model: str) -> str:
        if not provider:
            return "provider is empty"
        if not model:
            return f"no model is configured for {provider}"
        if self.provider_manager is None:
            if not bool(self.config.get(f"providers.{provider}.enabled", True)):
                return f"provider is disabled: {provider}"
            return ""
        statuses = {item.key: item for item in self.provider_manager.statuses()}
        status = statuses.get(provider)
        if status is None:
            return f"provider is not registered: {provider}"
        if not status.enabled:
            return f"provider is disabled: {provider}"
        if not status.configured:
            return f"provider is not configured: {provider}"
        try:
            models = tuple(str(item).casefold() for item in self.provider_manager.models(provider))
        except Exception as exc:
            return f"provider models are unavailable: {provider} ({type(exc).__name__})"
        if models and model.casefold() not in models:
            return f"model is not available for {provider}: {model}"
        return ""

    @staticmethod
    def _coerce(agent: Any) -> ManagedAgentDefinition:
        if isinstance(agent, ManagedAgentDefinition):
            return agent
        if not hasattr(agent, "to_dict") or not hasattr(agent, "agent_id"):
            raise TypeError("An Orion agent definition is required.")
        return ManagedAgentDefinition.from_value(
            agent.to_dict(),
            expected_id=agent.agent_id,
            expected_scope="permanent",
            legacy_timestamp=utc_now(),
        )
