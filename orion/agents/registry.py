"""External, least-privilege agent definitions for Orion."""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import yaml


AGENT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
CAPABILITY_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,63}")
SUPPORTED_PROVIDERS = frozenset({"configured-default", "ollama", "openai", "gemini"})
SUPPORTED_TOOLS = frozenset({"read_files", "inspect_diff", "run_tests"})
MAX_INSTRUCTIONS_CHARS = 20_000
MAX_TEST_RESPONSE_CHARS = 50_000


def normalize_agent_id(value: str) -> str:
    normalized = re.sub(r"[-_\s]+", "-", str(value).strip().lower())
    if not AGENT_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Agent ID must be 2-64 lowercase letters, numbers, or hyphens."
        )
    return normalized


def _exact_mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a YAML mapping.")
    keys = set(value)
    missing = sorted(fields - keys)
    unknown = sorted(keys - fields)
    if missing:
        raise ValueError(f"{label} is missing required fields: {missing}")
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {unknown}")
    return value


def _required_string(value: Any, label: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be {maximum:,} characters or fewer.")
    return normalized


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be true or false.")
    return value


@dataclass(frozen=True)
class FilesystemPermissions:
    read: bool = False
    write: bool = False

    @classmethod
    def from_value(cls, value: Any) -> "FilesystemPermissions":
        value = _exact_mapping(value, {"read", "write"}, "Agent filesystem permissions")
        return cls(
            read=_boolean(value["read"], "Agent filesystem read permission"),
            write=_boolean(value["write"], "Agent filesystem write permission"),
        )


@dataclass(frozen=True)
class ShellPermissions:
    run_tests: bool = False
    arbitrary_commands: bool = False

    @classmethod
    def from_value(cls, value: Any) -> "ShellPermissions":
        value = _exact_mapping(
            value,
            {"run_tests", "arbitrary_commands"},
            "Agent shell permissions",
        )
        return cls(
            run_tests=_boolean(value["run_tests"], "Agent run-tests permission"),
            arbitrary_commands=_boolean(
                value["arbitrary_commands"], "Agent arbitrary-command permission"
            ),
        )


@dataclass(frozen=True)
class GitPermissions:
    create_branch: bool = False
    commit: bool = False
    push: bool = False

    @classmethod
    def from_value(cls, value: Any) -> "GitPermissions":
        value = _exact_mapping(
            value, {"create_branch", "commit", "push"}, "Agent Git permissions"
        )
        return cls(
            create_branch=_boolean(value["create_branch"], "Agent create-branch permission"),
            commit=_boolean(value["commit"], "Agent commit permission"),
            push=_boolean(value["push"], "Agent push permission"),
        )


@dataclass(frozen=True)
class AgentPermissions:
    filesystem: FilesystemPermissions = FilesystemPermissions()
    shell: ShellPermissions = ShellPermissions()
    git: GitPermissions = GitPermissions()

    @classmethod
    def from_value(cls, value: Any) -> "AgentPermissions":
        value = _exact_mapping(value, {"filesystem", "shell", "git"}, "Agent permissions")
        return cls(
            filesystem=FilesystemPermissions.from_value(value["filesystem"]),
            shell=ShellPermissions.from_value(value["shell"]),
            git=GitPermissions.from_value(value["git"]),
        )

    @classmethod
    def for_tools(cls, tools: Iterable[str]) -> "AgentPermissions":
        capabilities = set(tools)
        return cls(
            filesystem=FilesystemPermissions(
                read=bool(capabilities & {"read_files", "inspect_diff"}),
                write=False,
            ),
            shell=ShellPermissions(run_tests="run_tests" in capabilities),
            git=GitPermissions(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "filesystem": {
                "read": self.filesystem.read,
                "write": self.filesystem.write,
            },
            "shell": {
                "run_tests": self.shell.run_tests,
                "arbitrary_commands": self.shell.arbitrary_commands,
            },
            "git": {
                "create_branch": self.git.create_branch,
                "commit": self.git.commit,
                "push": self.git.push,
            },
        }


@dataclass(frozen=True)
class AgentLimits:
    max_turns: int = 3
    can_modify_files: bool = False

    @classmethod
    def from_value(cls, value: Any) -> "AgentLimits":
        value = _exact_mapping(value, {"max_turns", "can_modify_files"}, "Agent limits")
        max_turns = value["max_turns"]
        if isinstance(max_turns, bool) or not isinstance(max_turns, int):
            raise ValueError("Agent max_turns must be an integer.")
        if not 1 <= max_turns <= 20:
            raise ValueError("Agent max_turns must be between 1 and 20.")
        return cls(
            max_turns=max_turns,
            can_modify_files=_boolean(
                value["can_modify_files"], "Agent can-modify-files limit"
            ),
        )


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    name: str
    enabled: bool
    provider: str
    model: str
    instructions: str
    tools: tuple[str, ...]
    limits: AgentLimits
    permissions: AgentPermissions

    @classmethod
    def from_value(cls, agent_id: str, value: Any) -> "AgentDefinition":
        normalized_id = normalize_agent_id(agent_id)
        value = _exact_mapping(
            value,
            {
                "name", "enabled", "provider", "model", "instructions",
                "tools", "limits", "permissions",
            },
            "Agent definition",
        )
        provider = _required_string(
            value["provider"], "Agent provider", maximum=64
        ).lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Agent provider is not supported: {provider}")
        raw_tools = value["tools"]
        if not isinstance(raw_tools, list) or any(not isinstance(item, str) for item in raw_tools):
            raise ValueError("Agent tools must be a YAML list of capability names.")
        if len(raw_tools) > 32:
            raise ValueError("Agent tools cannot contain more than 32 capabilities.")
        tools = tuple(item.strip().lower() for item in raw_tools)
        if any(not CAPABILITY_PATTERN.fullmatch(item) for item in tools):
            raise ValueError("Agent tool names must use lowercase letters, numbers, and underscores.")
        if len(set(tools)) != len(tools):
            raise ValueError("Agent tools cannot contain duplicates.")
        unknown_tools = sorted(set(tools) - SUPPORTED_TOOLS)
        if unknown_tools:
            raise ValueError(f"Agent tools are not supported: {unknown_tools}")
        limits = AgentLimits.from_value(value["limits"])
        permissions = AgentPermissions.from_value(value["permissions"])
        if limits.can_modify_files != permissions.filesystem.write:
            raise ValueError(
                "Agent can_modify_files must match its filesystem write permission."
            )
        if "read_files" in tools and not permissions.filesystem.read:
            raise ValueError("The read_files tool requires filesystem read permission.")
        if "inspect_diff" in tools and not permissions.filesystem.read:
            raise ValueError("The inspect_diff tool requires filesystem read permission.")
        if "run_tests" in tools and not permissions.shell.run_tests:
            raise ValueError("The run_tests tool requires shell run_tests permission.")
        if permissions.shell.arbitrary_commands and not permissions.filesystem.write:
            raise ValueError(
                "Arbitrary shell commands require filesystem write permission."
            )
        if (
            permissions.git.create_branch
            or permissions.git.commit
            or permissions.git.push
        ) and not permissions.filesystem.write:
            raise ValueError("Git mutation permissions require filesystem write permission.")
        return cls(
            agent_id=normalized_id,
            name=_required_string(value["name"], "Agent name", maximum=100),
            enabled=_boolean(value["enabled"], "Agent enabled state"),
            provider=provider,
            model=_required_string(value["model"], "Agent model", maximum=200),
            instructions=_required_string(
                value["instructions"],
                "Agent instructions",
                maximum=MAX_INSTRUCTIONS_CHARS,
            ),
            tools=tools,
            limits=limits,
            permissions=permissions,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "instructions": self.instructions,
            "tools": list(self.tools),
            "limits": {
                "max_turns": self.limits.max_turns,
                "can_modify_files": self.limits.can_modify_files,
            },
            "permissions": self.permissions.to_dict(),
        }


@dataclass(frozen=True)
class AgentResponse:
    summary: str
    recommendations: tuple[str, ...]
    risks: tuple[str, ...]
    next_action: str

    @classmethod
    def from_value(cls, value: Any) -> "AgentResponse":
        value = _exact_mapping(
            value,
            {"summary", "recommendations", "risks", "next_action"},
            "Agent response",
        )
        lists: dict[str, tuple[str, ...]] = {}
        for field_name in ("recommendations", "risks"):
            items = value[field_name]
            if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
                raise ValueError(f"Agent response {field_name} must be a list of strings.")
            lists[field_name] = tuple(item.strip() for item in items if item.strip())
        if not lists["recommendations"]:
            raise ValueError("Agent response recommendations cannot be empty.")
        return cls(
            summary=_required_string(value["summary"], "Agent response summary", maximum=4_000),
            recommendations=lists["recommendations"],
            risks=lists["risks"],
            next_action=_required_string(
                value["next_action"], "Agent response next_action", maximum=4_000
            ),
        )


@dataclass(frozen=True)
class AgentTestResult:
    agent: AgentDefinition
    provider: str
    model: str
    response: AgentResponse


class AgentRegistry:
    """Load strict agent definitions from Orion's external user-data directory."""

    def __init__(self, root: str | Path, config_manager, provider_factory=None) -> None:
        self.root = Path(root)
        self.config = config_manager
        self.provider_factory = provider_factory

    def save(self, agent: AgentDefinition, *, overwrite: bool = False) -> Path:
        path = self._path(agent.agent_id)
        if path.exists() and not overwrite:
            raise FileExistsError(f"Agent already exists: {agent.agent_id}")
        payload = agent.to_dict()
        AgentDefinition.from_value(agent.agent_id, payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".yaml.tmp")
        temporary.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        temporary.replace(path)
        return path

    def load(self, agent_id: str) -> AgentDefinition:
        normalized_id = normalize_agent_id(agent_id)
        path = self._path(normalized_id)
        if not path.is_file():
            raise FileNotFoundError(f"Agent not found: {normalized_id}")
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
            return AgentDefinition.from_value(normalized_id, value)
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            raise ValueError(f"Agent definition is invalid: {normalized_id}") from exc

    def all(self) -> tuple[AgentDefinition, ...]:
        agents = [self.load(path.stem) for path in sorted(self.root.glob("*.yaml"))]
        return tuple(sorted(agents, key=lambda item: item.agent_id))

    def set_enabled(self, agent_id: str, enabled: bool) -> AgentDefinition:
        if not isinstance(enabled, bool):
            raise ValueError("Agent enabled state must be true or false.")
        agent = self.load(agent_id)
        updated = replace(agent, enabled=enabled)
        self.save(updated, overwrite=True)
        return updated

    def ensure_defaults(self, agents: Iterable[AgentDefinition]) -> None:
        for agent in agents:
            if not self._path(agent.agent_id).exists():
                self.save(agent, overwrite=False)

    def resolve(self, agent: AgentDefinition) -> tuple[str, str]:
        provider = (
            str(self.config.get("providers.default", "ollama")).strip().lower()
            if agent.provider == "configured-default"
            else agent.provider
        )
        if provider not in SUPPORTED_PROVIDERS - {"configured-default"}:
            raise ValueError(f"Agent resolved to an unsupported provider: {provider}")
        model = (
            str(self.config.get(f"providers.{provider}.model", "configured-default")).strip()
            if agent.model == "configured-default"
            else agent.model
        )
        if not model:
            raise ValueError(f"Agent model is not configured: {agent.agent_id}")
        return provider, model

    def test(self, agent_id: str) -> AgentTestResult:
        agent = self.load(agent_id)
        if not agent.enabled:
            raise ValueError(f"Agent is disabled: {agent.agent_id}")
        if self.provider_factory is None:
            raise RuntimeError("Agent provider factory is unavailable.")
        provider_key, model = self.resolve(agent)
        try:
            provider = self.provider_factory.create(provider_key)
            if agent.model != "configured-default":
                if not hasattr(provider, "select_model"):
                    raise ValueError(f"{provider_key} does not support agent-specific models.")
                provider.select_model(agent.model)
        except Exception as exc:
            raise RuntimeError(
                f"Agent test provider setup failed ({type(exc).__name__})."
            ) from exc
        system_prompt = (
            f"You are the configured Orion agent {agent.name}.\n"
            f"Agent instructions:\n{agent.instructions}\n\n"
            "This is a bounded Phase 1 configuration test. No tools are available. "
            "Do not modify files, run commands, or perform Git actions.\n"
            "Return exactly one JSON object and no Markdown with these keys: "
            "summary (string), recommendations (non-empty array of strings), "
            "risks (array of strings), next_action (string)."
        )
        try:
            raw = provider.chat(
                "Confirm that you understand your configured assignment and describe how you would approach it safely.",
                system_prompt=system_prompt,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Agent test provider call failed ({type(exc).__name__})."
            ) from exc
        if len(str(raw)) > MAX_TEST_RESPONSE_CHARS:
            raise ValueError("Agent test response exceeded the 50,000-character limit.")
        response = self._parse_response(raw)
        return AgentTestResult(
            agent=agent,
            provider=provider_key,
            model=str(getattr(provider, "model", model)),
            response=response,
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
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Agent test returned invalid JSON.") from exc
        return AgentResponse.from_value(value)

    def _path(self, agent_id: str) -> Path:
        normalized_id = normalize_agent_id(agent_id)
        return self.root / f"{normalized_id}.yaml"


def built_in_agents(config_manager) -> tuple[AgentDefinition, ...]:
    """Create the first external definitions without overriding user-owned files."""

    permissions = AgentPermissions()

    def build(agent_id: str, name: str, instructions: str) -> AgentDefinition:
        provider = str(
            config_manager.get(f"team.roles.{agent_id}.provider", "configured-default")
        ).strip().lower()
        model = str(
            config_manager.get(f"team.roles.{agent_id}.model", "configured-default")
        ).strip()
        return AgentDefinition.from_value(agent_id, {
            "name": name,
            "enabled": True,
            "provider": provider,
            "model": model,
            "instructions": instructions,
            "tools": [],
            "limits": {"max_turns": 1, "can_modify_files": False},
            "permissions": permissions.to_dict(),
        })

    return (
        build(
            "architect",
            "Orion Architect",
            "Create small, provider-neutral, implementation-ready plans with clear boundaries, tests, configuration, persistence, and documentation considerations.",
        ),
        build(
            "engineer",
            "Orion Engineer Reviewer",
            "Critique implementation plans, identify gaps and unsafe assumptions, and return a consolidated ordered plan suitable for approval.",
        ),
        build(
            "reviewer",
            "Orion Reviewer",
            "Review structured work for correctness, safety, scope, and missing verification before approval.",
        ),
    )
