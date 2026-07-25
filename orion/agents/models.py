"""Versioned, provider-neutral models for Orion's reusable Agent System."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


AGENT_SCHEMA_VERSION = 1
AGENT_SCOPES = frozenset({"permanent", "workspace"})
AGENT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
PROVIDER_PATTERN = re.compile(r"(?:auto|[a-z0-9][a-z0-9_-]{0,63})")
PROFILE_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
CAPABILITIES = frozenset({
    "read_workspace",
    "write_workspace",
    "run_tests",
    "run_commands",
    "use_network",
    "web_research",
    "inspect_git",
    "write_git",
    "access_calendar",
    "access_email",
    "generate_images",
})
SECRET_KEY_PARTS = frozenset({
    "api_key", "apikey", "authorization", "credential", "oauth", "password",
    "private_key", "secret", "token",
})
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:ghp|github_pat|xox[abprs])_[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_agent_id(value: str) -> str:
    normalized = re.sub(r"[-_\s]+", "-", str(value).strip().lower())
    if not AGENT_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Agent ID must be 2-64 lowercase letters, numbers, or hyphens."
        )
    return normalized


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a YAML mapping.")
    return dict(value)


def _text(
    value: Any,
    label: str,
    maximum: int,
    *,
    required: bool = True,
    multiline: bool = True,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{label} must be a non-empty string.")
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be {maximum:,} characters or fewer.")
    if not multiline and any(character in normalized for character in "\r\n\t"):
        raise ValueError(f"{label} cannot contain control characters.")
    return normalized


def _timestamp(value: Any, label: str) -> str:
    text = _text(value, label, 80, multiline=False)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset.")
    return text


def _looks_like_secret(value: Any) -> bool:
    return isinstance(value, str) and any(
        pattern.search(value.strip()) for pattern in SECRET_VALUE_PATTERNS
    )


def _validate_secret_free(value: Any, path: str = "agent") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(part in normalized for part in SECRET_KEY_PARTS):
                raise ValueError(f"{path} cannot store secret-bearing field: {key}")
            _validate_secret_free(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_secret_free(item, f"{path}[{index}]")
    elif _looks_like_secret(value):
        raise ValueError(f"{path} cannot contain credential-shaped values.")


class PermissionPolicy(str, Enum):
    DENIED = "denied"
    APPROVAL = "approval"
    ALLOWED = "allowed"

    @classmethod
    def parse(cls, value: Any, label: str) -> "PermissionPolicy":
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            return cls.APPROVAL if value else cls.DENIED
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            choices = ", ".join(item.value for item in cls)
            raise ValueError(f"{label} must be one of: {choices}") from exc


@dataclass(frozen=True)
class AgentRole:
    job: str
    specialty: str = ""
    personality: str = "Practical, direct, and collaborative."
    instructions: str = ""

    @classmethod
    def from_value(cls, value: Any) -> "AgentRole":
        value = _mapping(value, "Agent role")
        return cls(
            job=_text(value.get("job", ""), "Agent job", 200),
            specialty=_text(
                value.get("specialty", ""), "Agent specialty", 500, required=False
            ),
            personality=_text(
                value.get("personality", ""),
                "Agent personality",
                1_000,
            ),
            instructions=_text(
                value.get("instructions", ""),
                "Agent instructions",
                20_000,
                required=False,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.job,
            "specialty": self.specialty,
            "personality": self.personality,
            "instructions": self.instructions,
        }


@dataclass(frozen=True)
class AgentExecutionPreferences:
    provider: str = "auto"
    model: str = "auto"
    routing_profile: str = "balanced"
    temperature: float | None = None
    generation: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "AgentExecutionPreferences":
        value = _mapping(value, "Agent execution preferences")
        provider = _text(
            value.get("provider", "auto"), "Agent provider", 64, multiline=False
        ).lower()
        if provider == "configured-default":
            provider = "auto"
        if not PROVIDER_PATTERN.fullmatch(provider):
            raise ValueError("Agent provider must be 'auto' or a provider identifier.")
        model = _text(
            value.get("model", "auto"), "Agent model", 200, multiline=False
        )
        if model == "configured-default":
            model = "auto"
        if _looks_like_secret(model):
            raise ValueError("Agent model cannot contain credential-shaped values.")
        routing_profile = _text(
            value.get("routing_profile", "balanced"),
            "Agent routing profile",
            64,
            multiline=False,
        ).lower()
        if not PROFILE_PATTERN.fullmatch(routing_profile):
            raise ValueError("Agent routing profile has an invalid format.")
        temperature = value.get("temperature", None)
        if temperature is not None:
            if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
                raise ValueError("Agent temperature must be a number or null.")
            temperature = float(temperature)
            if not 0.0 <= temperature <= 2.0:
                raise ValueError("Agent temperature must be between 0 and 2.")
        generation = value.get("generation", {})
        if not isinstance(generation, dict):
            raise ValueError("Agent generation settings must be a YAML mapping.")
        for key, item in generation.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Agent generation setting names must be non-empty strings.")
            if not isinstance(item, (str, int, float, bool, type(None))):
                raise ValueError("Agent generation setting values must be scalar.")
        _validate_secret_free(generation, "execution.generation")
        return cls(provider, model, routing_profile, temperature, dict(generation))

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "routing_profile": self.routing_profile,
            "temperature": self.temperature,
        }
        if self.generation:
            value["generation"] = dict(self.generation)
        return value


@dataclass(frozen=True)
class AgentPermissionPolicy:
    network: PermissionPolicy = PermissionPolicy.DENIED
    shell: PermissionPolicy = PermissionPolicy.APPROVAL
    write_files: PermissionPolicy = PermissionPolicy.APPROVAL
    git_write: PermissionPolicy = PermissionPolicy.DENIED
    calendar: PermissionPolicy = PermissionPolicy.DENIED
    email: PermissionPolicy = PermissionPolicy.DENIED
    image_generation: PermissionPolicy = PermissionPolicy.DENIED
    extensions: dict[str, Any] = field(default_factory=dict)

    FIELDS = (
        "network", "shell", "write_files", "git_write", "calendar", "email",
        "image_generation",
    )

    @classmethod
    def from_value(cls, value: Any) -> "AgentPermissionPolicy":
        value = _mapping(value, "Agent permissions")
        known = {
            key: PermissionPolicy.parse(
                value.get(key, cls.__dataclass_fields__[key].default),
                f"Agent {key} permission",
            )
            for key in cls.FIELDS
        }
        extensions = {key: item for key, item in value.items() if key not in cls.FIELDS}
        _validate_secret_free(extensions, "permissions")
        return cls(**known, extensions=extensions)

    def to_dict(self) -> dict[str, Any]:
        value = {key: getattr(self, key).value for key in self.FIELDS}
        value.update(self.extensions)
        return value


@dataclass(frozen=True)
class AgentMetadata:
    created_at: str
    updated_at: str

    @classmethod
    def create(cls, now: str | None = None) -> "AgentMetadata":
        timestamp = _timestamp(now or utc_now(), "Agent timestamp")
        return cls(timestamp, timestamp)

    @classmethod
    def from_value(cls, value: Any) -> "AgentMetadata":
        value = _mapping(value, "Agent metadata")
        created = _timestamp(value.get("created_at", ""), "Agent created_at")
        updated = _timestamp(value.get("updated_at", ""), "Agent updated_at")
        if datetime.fromisoformat(updated.replace("Z", "+00:00")) < datetime.fromisoformat(
            created.replace("Z", "+00:00")
        ):
            raise ValueError("Agent updated_at cannot precede created_at.")
        return cls(created, updated)

    def to_dict(self) -> dict[str, str]:
        return {"created_at": self.created_at, "updated_at": self.updated_at}


@dataclass(frozen=True)
class ManagedAgentDefinition:
    schema_version: int
    agent_id: str
    name: str
    description: str
    scope: str
    enabled: bool
    role: AgentRole
    execution: AgentExecutionPreferences
    capabilities: tuple[str, ...]
    permissions: AgentPermissionPolicy
    workspace_access: str
    metadata: AgentMetadata
    extensions: dict[str, Any] = field(default_factory=dict)

    TOP_LEVEL_FIELDS = frozenset({
        "schema_version", "id", "name", "description", "scope", "enabled",
        "role", "execution", "capabilities", "permissions", "workspace_access",
        "metadata",
    })

    @property
    def provider(self) -> str:
        return "configured-default" if self.execution.provider == "auto" else self.execution.provider

    @property
    def model(self) -> str:
        return "configured-default" if self.execution.model == "auto" else self.execution.model

    @property
    def instructions(self) -> str:
        return self.role.instructions

    @property
    def tools(self) -> tuple[str, ...]:
        """Compatibility view used by Orion's existing planning workflow."""
        aliases = {
            "read_workspace": "read_files",
            "inspect_git": "inspect_diff",
            "run_tests": "run_tests",
        }
        return tuple(aliases[item] for item in self.capabilities if item in aliases)

    @classmethod
    def create(
        cls,
        *,
        agent_id: str,
        name: str,
        description: str = "",
        scope: str = "permanent",
        job: str | None = None,
        specialty: str = "",
        personality: str = "Practical, direct, and collaborative.",
        instructions: str = "",
        provider: str = "auto",
        model: str = "auto",
        routing_profile: str = "balanced",
        temperature: float | None = None,
        generation: dict[str, Any] | None = None,
        capabilities: tuple[str, ...] | list[str] = (),
        permissions: AgentPermissionPolicy | None = None,
        workspace_access: str = "read_only",
        enabled: bool = True,
        now: str | None = None,
    ) -> "ManagedAgentDefinition":
        timestamp = now or utc_now()
        return cls.from_value({
            "schema_version": AGENT_SCHEMA_VERSION,
            "id": agent_id,
            "name": name,
            "description": description,
            "scope": scope,
            "enabled": enabled,
            "role": {
                "job": job or name,
                "specialty": specialty,
                "personality": personality,
                "instructions": instructions,
            },
            "execution": {
                "provider": provider,
                "model": model,
                "routing_profile": routing_profile,
                "temperature": temperature,
                "generation": generation or {},
            },
            "capabilities": list(capabilities),
            "permissions": (permissions or AgentPermissionPolicy()).to_dict(),
            "workspace_access": workspace_access,
            "metadata": {"created_at": timestamp, "updated_at": timestamp},
        })

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        expected_id: str | None = None,
        expected_scope: str | None = None,
        legacy_timestamp: str | None = None,
    ) -> "ManagedAgentDefinition":
        value = _mapping(value, "Agent definition")
        if "schema_version" not in value:
            value = cls._migrate_legacy(
                value,
                expected_id=expected_id,
                expected_scope=expected_scope,
                timestamp=legacy_timestamp,
            )
        schema_version = value.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise ValueError("Agent schema_version must be an integer.")
        if schema_version != AGENT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported agent schema version {schema_version}; "
                f"this Orion version supports {AGENT_SCHEMA_VERSION}."
            )
        agent_id = normalize_agent_id(value.get("id", expected_id or ""))
        if expected_id is not None and agent_id != normalize_agent_id(expected_id):
            raise ValueError("Agent ID does not match its filename.")
        scope = _text(value.get("scope", ""), "Agent scope", 20, multiline=False).lower()
        if scope not in AGENT_SCOPES:
            raise ValueError("Agent scope must be 'permanent' or 'workspace'.")
        if expected_scope is not None and scope != expected_scope:
            raise ValueError(
                f"Agent scope is {scope!r}, but it is stored in the {expected_scope} repository."
            )
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("Agent enabled state must be true or false.")
        raw_capabilities = value.get("capabilities", [])
        if not isinstance(raw_capabilities, list) or any(
            not isinstance(item, str) for item in raw_capabilities
        ):
            raise ValueError("Agent capabilities must be a YAML list of strings.")
        capabilities = tuple(item.strip().lower() for item in raw_capabilities)
        if len(capabilities) > 32:
            raise ValueError("Agent capabilities cannot contain more than 32 entries.")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("Agent capabilities cannot contain duplicates.")
        unknown_capabilities = sorted(set(capabilities) - CAPABILITIES)
        if unknown_capabilities:
            raise ValueError(f"Agent capabilities are not supported: {unknown_capabilities}")
        workspace_access = _text(
            value.get("workspace_access", "read_only"),
            "Agent workspace access",
            20,
            multiline=False,
        ).lower()
        if workspace_access not in {"none", "read_only", "read_write"}:
            raise ValueError(
                "Agent workspace_access must be 'none', 'read_only', or 'read_write'."
            )
        if "write_workspace" in capabilities and workspace_access != "read_write":
            raise ValueError("write_workspace capability requires read_write workspace access.")
        if "read_workspace" in capabilities and workspace_access == "none":
            raise ValueError("read_workspace capability requires workspace access.")
        permissions = AgentPermissionPolicy.from_value(value.get("permissions", {}))
        if "write_workspace" not in capabilities and permissions.write_files != PermissionPolicy.DENIED:
            permissions = replace(permissions, write_files=PermissionPolicy.DENIED)
        if "write_git" not in capabilities and permissions.git_write != PermissionPolicy.DENIED:
            permissions = replace(permissions, git_write=PermissionPolicy.DENIED)
        if not {"run_tests", "run_commands"} & set(capabilities) and permissions.shell != PermissionPolicy.DENIED:
            permissions = replace(permissions, shell=PermissionPolicy.DENIED)
        if not {"use_network", "web_research"} & set(capabilities) and permissions.network != PermissionPolicy.DENIED:
            permissions = replace(permissions, network=PermissionPolicy.DENIED)
        extensions = {
            key: item for key, item in value.items() if key not in cls.TOP_LEVEL_FIELDS
        }
        _validate_secret_free(extensions, "agent extensions")
        result = cls(
            schema_version=schema_version,
            agent_id=agent_id,
            name=_text(value.get("name", ""), "Agent name", 100),
            description=_text(
                value.get("description", ""),
                "Agent description",
                1_000,
                required=False,
            ),
            scope=scope,
            enabled=enabled,
            role=AgentRole.from_value(value.get("role", {})),
            execution=AgentExecutionPreferences.from_value(value.get("execution", {})),
            capabilities=capabilities,
            permissions=permissions,
            workspace_access=workspace_access,
            metadata=AgentMetadata.from_value(value.get("metadata", {})),
            extensions=extensions,
        )
        _validate_secret_free(result.to_dict(), "agent definition")
        return result

    @classmethod
    def _migrate_legacy(
        cls,
        value: dict[str, Any],
        *,
        expected_id: str | None,
        expected_scope: str | None,
        timestamp: str | None,
    ) -> dict[str, Any]:
        if not expected_id:
            raise ValueError("Legacy agent definitions require an ID from the filename.")
        tools = value.get("tools", [])
        if not isinstance(tools, list):
            tools = []
        tool_aliases = {
            "read_files": "read_workspace",
            "inspect_diff": "inspect_git",
            "run_tests": "run_tests",
        }
        capabilities = list(dict.fromkeys(
            tool_aliases[item] for item in tools if item in tool_aliases
        ))
        legacy_permissions = value.get("permissions", {})
        filesystem = (
            legacy_permissions.get("filesystem", {})
            if isinstance(legacy_permissions, dict)
            else {}
        )
        shell = (
            legacy_permissions.get("shell", {})
            if isinstance(legacy_permissions, dict)
            else {}
        )
        git = (
            legacy_permissions.get("git", {})
            if isinstance(legacy_permissions, dict)
            else {}
        )
        if bool(filesystem.get("write")) and "write_workspace" not in capabilities:
            capabilities.append("write_workspace")
        if bool(shell.get("arbitrary_commands")) and "run_commands" not in capabilities:
            capabilities.append("run_commands")
        if any(bool(git.get(key)) for key in ("create_branch", "commit", "push")):
            capabilities.append("write_git")
        created = timestamp or utc_now()
        known = {
            "name", "enabled", "provider", "model", "instructions", "tools",
            "limits", "permissions",
        }
        extensions = {
            f"legacy_{key}": item for key, item in value.items() if key not in known
        }
        migrated = {
            "schema_version": AGENT_SCHEMA_VERSION,
            "id": expected_id,
            "name": value.get("name", expected_id),
            "description": "",
            "scope": expected_scope or "permanent",
            "enabled": value.get("enabled", True),
            "role": {
                "job": value.get("name", expected_id),
                "specialty": "",
                "personality": "Practical, direct, and collaborative.",
                "instructions": value.get("instructions", ""),
            },
            "execution": {
                "provider": value.get("provider", "auto"),
                "model": value.get("model", "auto"),
                "routing_profile": "balanced",
                "temperature": None,
            },
            "capabilities": capabilities,
            "permissions": {
                "network": "denied",
                "shell": "approval" if any(bool(item) for item in shell.values()) else "denied",
                "write_files": "approval" if bool(filesystem.get("write")) else "denied",
                "git_write": "approval" if any(bool(item) for item in git.values()) else "denied",
                "calendar": "denied",
                "email": "denied",
                "image_generation": "denied",
            },
            "workspace_access": (
                "read_write" if bool(filesystem.get("write"))
                else "read_only" if bool(filesystem.get("read"))
                else "none"
            ),
            "metadata": {"created_at": created, "updated_at": created},
        }
        migrated.update(extensions)
        return migrated

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "scope": self.scope,
            "enabled": self.enabled,
            "role": self.role.to_dict(),
            "execution": self.execution.to_dict(),
            "capabilities": list(self.capabilities),
            "permissions": self.permissions.to_dict(),
            "workspace_access": self.workspace_access,
            "metadata": self.metadata.to_dict(),
        }
        value.update(self.extensions)
        return value

    def with_updated_timestamp(self, timestamp: str | None = None) -> "ManagedAgentDefinition":
        return replace(
            self,
            metadata=replace(self.metadata, updated_at=_timestamp(
                timestamp or utc_now(), "Agent updated_at"
            )),
        )

    def with_scope(self, scope: str, timestamp: str | None = None) -> "ManagedAgentDefinition":
        value = self.to_dict()
        value["scope"] = scope
        value["metadata"]["updated_at"] = timestamp or utc_now()
        return ManagedAgentDefinition.from_value(value)


@dataclass(frozen=True)
class AgentResolution:
    requested_provider: str
    requested_model: str
    provider: str
    model: str
    routing_profile: str
    source: str
    fallback_reason: str = ""

    @property
    def requested_assignment(self) -> str:
        return f"{self.requested_provider}:{self.requested_model}"

    @property
    def actual_assignment(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class SelectedAgent:
    agent_id: str
    responsibility: str

    @classmethod
    def from_value(cls, value: Any) -> "SelectedAgent":
        value = _mapping(value, "Selected agent")
        return cls(
            normalize_agent_id(value.get("id", "")),
            _text(value.get("responsibility", ""), "Agent responsibility", 1_000),
        )

    def to_dict(self) -> dict[str, str]:
        return {"id": self.agent_id, "responsibility": self.responsibility}


@dataclass(frozen=True)
class AgentRunSnapshot:
    schema_version: int
    agent_id: str
    name: str
    scope: str
    description: str
    job: str
    specialty: str
    personality: str
    instructions: str
    requested_provider: str
    requested_model: str
    actual_provider: str
    actual_model: str
    routing_profile: str
    capabilities: tuple[str, ...]
    permissions: dict[str, str]
    workspace_access: str
    responsibility: str
    definition_updated_at: str

    @classmethod
    def from_agent(
        cls,
        agent: ManagedAgentDefinition,
        resolution: AgentResolution,
        responsibility: str,
    ) -> "AgentRunSnapshot":
        snapshot = cls(
            schema_version=agent.schema_version,
            agent_id=agent.agent_id,
            name=agent.name,
            scope=agent.scope,
            description=agent.description,
            job=agent.role.job,
            specialty=agent.role.specialty,
            personality=agent.role.personality,
            instructions=agent.role.instructions,
            requested_provider=resolution.requested_provider,
            requested_model=resolution.requested_model,
            actual_provider=resolution.provider,
            actual_model=resolution.model,
            routing_profile=resolution.routing_profile,
            capabilities=agent.capabilities,
            permissions={
                key: value for key, value in agent.permissions.to_dict().items()
                if isinstance(value, str)
            },
            workspace_access=agent.workspace_access,
            responsibility=_text(
                responsibility, "Agent responsibility", 1_000
            ),
            definition_updated_at=agent.metadata.updated_at,
        )
        _validate_secret_free(snapshot.to_dict(), "agent run snapshot")
        return snapshot

    @classmethod
    def from_value(cls, value: Any) -> "AgentRunSnapshot":
        value = _mapping(value, "Agent run snapshot")
        required = {
            "schema_version", "id", "name", "scope", "description", "job",
            "specialty", "personality", "instructions", "requested_provider",
            "requested_model", "actual_provider", "actual_model",
            "routing_profile", "capabilities", "permissions", "workspace_access",
            "responsibility", "definition_updated_at",
        }
        missing = sorted(required - set(value))
        unknown = sorted(set(value) - required)
        if missing:
            raise ValueError(f"Agent run snapshot is missing fields: {missing}")
        if unknown:
            raise ValueError(f"Agent run snapshot contains unsupported fields: {unknown}")
        permissions = _mapping(value["permissions"], "Agent snapshot permissions")
        if any(not isinstance(key, str) or not isinstance(item, str) for key, item in permissions.items()):
            raise ValueError("Agent snapshot permissions must be string policies.")
        capabilities = value["capabilities"]
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            raise ValueError("Agent snapshot capabilities must be a list of strings.")
        snapshot = cls(
            schema_version=int(value["schema_version"]),
            agent_id=normalize_agent_id(value["id"]),
            name=_text(value["name"], "Agent snapshot name", 100),
            scope=_text(value["scope"], "Agent snapshot scope", 20, multiline=False),
            description=_text(value["description"], "Agent snapshot description", 1_000, required=False),
            job=_text(value["job"], "Agent snapshot job", 200),
            specialty=_text(value["specialty"], "Agent snapshot specialty", 500, required=False),
            personality=_text(value["personality"], "Agent snapshot personality", 1_000),
            instructions=_text(value["instructions"], "Agent snapshot instructions", 20_000, required=False),
            requested_provider=_text(value["requested_provider"], "Requested provider", 64, multiline=False),
            requested_model=_text(value["requested_model"], "Requested model", 200, multiline=False),
            actual_provider=_text(value["actual_provider"], "Actual provider", 64, multiline=False),
            actual_model=_text(value["actual_model"], "Actual model", 200, multiline=False),
            routing_profile=_text(value["routing_profile"], "Routing profile", 64, multiline=False),
            capabilities=tuple(item.strip() for item in capabilities),
            permissions=dict(permissions),
            workspace_access=_text(value["workspace_access"], "Workspace access", 20, multiline=False),
            responsibility=_text(value["responsibility"], "Agent responsibility", 1_000),
            definition_updated_at=_timestamp(value["definition_updated_at"], "Definition updated_at"),
        )
        _validate_secret_free(snapshot.to_dict(), "agent run snapshot")
        return snapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.agent_id,
            "name": self.name,
            "scope": self.scope,
            "description": self.description,
            "job": self.job,
            "specialty": self.specialty,
            "personality": self.personality,
            "instructions": self.instructions,
            "requested_provider": self.requested_provider,
            "requested_model": self.requested_model,
            "actual_provider": self.actual_provider,
            "actual_model": self.actual_model,
            "routing_profile": self.routing_profile,
            "capabilities": list(self.capabilities),
            "permissions": dict(self.permissions),
            "workspace_access": self.workspace_access,
            "responsibility": self.responsibility,
            "definition_updated_at": self.definition_updated_at,
        }
