"""CLI adapter for Orion's AgentManager service."""
from __future__ import annotations

import shlex
from dataclasses import replace
from typing import Callable

from orion.agents.models import (
    AgentPermissionPolicy,
    ManagedAgentDefinition,
    PermissionPolicy,
)


class AgentCommandHandler:
    """Keep Agent System command parsing out of Orion's core router."""

    def __init__(
        self,
        manager,
        *,
        input_provider: Callable[[str], str] | None = None,
        output_provider: Callable[[str], None] | None = None,
    ) -> None:
        self.manager = manager
        self.input = input_provider or input
        self.output = output_provider or print

    def handle(self, payload: str) -> None:
        try:
            tokens = shlex.split(payload, posix=True)
        except ValueError as exc:
            self.output(f"Agent command could not be read: {exc}")
            return
        command = tokens[0].lower() if tokens else "list"
        args = tokens[1:]
        try:
            if command == "list":
                self.list_agents(args)
            elif command == "show":
                self.show(args)
            elif command == "templates":
                self.show_templates()
            elif command == "create":
                self.create(args)
            elif command == "edit":
                self.edit(args)
            elif command == "delete":
                self.delete(args)
            elif command in {"enable", "disable"}:
                self.enable(args, command == "enable")
            elif command == "validate":
                self.validate(args)
            elif command == "promote":
                self.promote(args)
            elif command == "copy":
                self.copy(args)
            elif command == "test":
                self.test(args)
            else:
                self.usage()
        except (
            ConnectionError, FileExistsError, FileNotFoundError, OSError,
            PermissionError, RuntimeError, TypeError, ValueError,
        ) as exc:
            self.output(f"Agent command failed: {exc}")

    def list_agents(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if positional:
            raise ValueError("Usage: agent list [--scope permanent|workspace]")
        scope = options.get("scope")
        agents = self.manager.all(scope=scope)
        self.output("Agents")
        self.output("-" * 86)
        if not agents:
            self.output("No agents are configured in this scope.")
            return
        for agent in agents:
            state = "enabled" if agent.enabled else "disabled"
            runtime = f"{agent.execution.provider}:{agent.execution.model}"
            self.output(
                f"  {agent.agent_id:<24} {agent.name:<25} "
                f"{agent.scope:<10} {runtime:<20} {state}"
            )
        self.output(f"Permanent: {self.manager.permanent_repository.root}")
        self.output(f"Workspace: {self.manager.workspace_repository.root}")

    def show(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if len(positional) != 1:
            raise ValueError("Usage: agent show <agent> [--scope permanent|workspace]")
        agent = self.manager.load(positional[0], scope=options.get("scope"))
        try:
            provider, model = self.manager.resolve(agent)
            resolved = f"{provider}:{model}"
        except ValueError:
            resolved = "unavailable"
        self.output(f"Agent: {agent.name}")
        self.output("-" * 72)
        self.output(f"ID: {agent.agent_id}")
        self.output(f"Schema: {agent.schema_version}")
        self.output(f"Scope: {agent.scope}")
        self.output(f"Status: {'Enabled' if agent.enabled else 'Disabled'}")
        self.output(f"Description: {agent.description or 'Not set'}")
        self.output(f"Job: {agent.role.job}")
        self.output(f"Specialty: {agent.role.specialty or 'Not set'}")
        self.output(f"Personality: {agent.role.personality}")
        self.output(f"Instructions: {agent.role.instructions or 'Not set'}")
        self.output(
            f"Provider/model: {agent.execution.provider}:{agent.execution.model} "
            f"-> {resolved}"
        )
        self.output(f"Routing profile: {agent.execution.routing_profile}")
        self.output(f"Temperature: {agent.execution.temperature}")
        self.output(
            "Capabilities: "
            + (", ".join(agent.capabilities) if agent.capabilities else "none")
        )
        self.output(f"Workspace access: {agent.workspace_access}")
        policies = agent.permissions.to_dict()
        self.output(
            "Permissions: "
            + ", ".join(f"{key}={value}" for key, value in policies.items())
        )
        self.output(
            "Permissions grant eligibility to request an action; Orion approvals "
            "and safety boundaries still apply."
        )

    def show_templates(self) -> None:
        self.output("Agent Templates")
        self.output("-" * 72)
        for template in self.manager.templates():
            self.output(
                f"  {template.agent_id:<24} {template.name:<25} {template.description}"
            )

    def create(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if positional:
            raise ValueError(
                "Use --name and --id for non-interactive agent creation."
            )
        template = options.get("from-template")
        if template:
            scope = self._scope(options.get("scope", "permanent"))
            agent = self.manager.create_from_template(
                template,
                scope=scope,
                name=options.get("name"),
                agent_id=options.get("id"),
            )
            self._created(agent)
            return

        preset_scope = options.get("scope")
        interactive = not options or set(options) <= {"scope"}
        if interactive:
            self.output("Create Agent")
            self.output("-" * 72)
            name = self.input("Agent name: ").strip()
            if preset_scope:
                scope = self._scope(preset_scope)
            else:
                scope_answer = self.input(
                    "Scope [1 permanent / 2 current workspace] (1): "
                ).strip().lower()
                scope = (
                    "workspace"
                    if scope_answer in {"2", "workspace", "w"}
                    else "permanent"
                )
            options = {
                "name": name,
                "scope": scope,
                "description": self.input("Description (optional): ").strip(),
                "job": self.input(f"Job ({name}): ").strip() or name,
                "specialty": self.input("Specialty (optional): ").strip(),
                "personality": self.input(
                    "Personality (Practical, direct, and collaborative): "
                ).strip() or "Practical, direct, and collaborative.",
                "instructions": self.input("System instructions (optional): ").strip(),
                "provider": self.input("Preferred provider (auto): ").strip() or "auto",
                "model": self.input("Preferred model (auto): ").strip() or "auto",
                "routing-profile": self.input(
                    "Routing profile (balanced): "
                ).strip() or "balanced",
                "capabilities": self.input(
                    "Capabilities, comma-separated (read_workspace): "
                ).strip() or "read_workspace",
            }
        name = options.get("name", "").strip()
        if not name:
            raise ValueError("Agent creation requires --name.")
        capabilities = self._csv(options.get("capabilities", "read_workspace"))
        workspace_access = options.get(
            "workspace-access",
            "read_write" if "write_workspace" in capabilities
            else "read_only" if "read_workspace" in capabilities
            else "none",
        )
        permissions = self._permission_options(
            options,
            capabilities=capabilities,
        )
        temperature = options.get("temperature")
        agent = self.manager.create_profile(
            name=name,
            scope=self._scope(options.get("scope", "permanent")),
            agent_id=options.get("id"),
            description=options.get("description", ""),
            job=options.get("job") or name,
            specialty=options.get("specialty", ""),
            personality=options.get(
                "personality", "Practical, direct, and collaborative."
            ),
            instructions=options.get("instructions", ""),
            provider=options.get("provider", "auto"),
            model=options.get("model", "auto"),
            routing_profile=options.get("routing-profile", "balanced"),
            temperature=float(temperature) if temperature is not None else None,
            capabilities=capabilities,
            permissions=permissions,
            workspace_access=workspace_access,
        )
        self._created(agent)

    def edit(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if len(positional) != 1:
            raise ValueError("Usage: agent edit <agent> [--name ... --job ...]")
        agent = self.manager.load(positional[0], scope=options.pop("scope", None))
        if not options:
            options = {
                "name": self.input(f"Name ({agent.name}): ").strip() or agent.name,
                "description": self.input(
                    f"Description ({agent.description}): "
                ).strip() or agent.description,
                "job": self.input(f"Job ({agent.role.job}): ").strip() or agent.role.job,
                "specialty": self.input(
                    f"Specialty ({agent.role.specialty}): "
                ).strip() or agent.role.specialty,
                "personality": self.input(
                    f"Personality ({agent.role.personality}): "
                ).strip() or agent.role.personality,
                "instructions": self.input(
                    "Instructions (blank keeps current): "
                ).strip() or agent.role.instructions,
                "provider": self.input(
                    f"Provider ({agent.execution.provider}): "
                ).strip() or agent.execution.provider,
                "model": self.input(
                    f"Model ({agent.execution.model}): "
                ).strip() or agent.execution.model,
            }
        value = agent.to_dict()
        for key in ("name", "description", "enabled", "workspace_access"):
            option_key = key.replace("_", "-")
            if option_key in options:
                value[key] = options[option_key]
        for key in ("job", "specialty", "personality", "instructions"):
            if key in options:
                value["role"][key] = options[key]
        for key in ("provider", "model", "routing-profile", "temperature"):
            if key in options:
                schema_key = key.replace("-", "_")
                value["execution"][schema_key] = (
                    float(options[key]) if key == "temperature" else options[key]
                )
        if "capabilities" in options:
            value["capabilities"] = list(self._csv(options["capabilities"]))
        value["metadata"]["updated_at"] = agent.metadata.updated_at
        updated = ManagedAgentDefinition.from_value(value)
        updated = self.manager.update(updated)
        self.output(f"[OK] Agent {updated.agent_id} updated.")

    def delete(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if len(positional) != 1:
            raise ValueError("Usage: agent delete <agent> [--scope ...] [--yes]")
        agent = self.manager.load(positional[0], scope=options.get("scope"))
        if "yes" not in options:
            answer = self.input(
                f"Delete {agent.name} ({agent.scope})? [y/N]: "
            ).strip().lower()
            if answer not in {"y", "yes"}:
                self.output("Agent deletion cancelled.")
                return
        path = self.manager.delete(agent.agent_id, scope=agent.scope)
        self.output(f"Deleted agent {agent.agent_id}: {path}")

    def enable(self, args: list[str], enabled: bool) -> None:
        options, positional = self._options(args)
        if len(positional) != 1:
            raise ValueError(
                "Usage: agent enable|disable <agent> [--scope permanent|workspace]"
            )
        agent = self.manager.set_enabled(
            positional[0], enabled, scope=options.get("scope")
        )
        self.output(
            f"Agent {agent.agent_id} is now {'enabled' if enabled else 'disabled'}."
        )

    def validate(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if len(positional) != 1:
            raise ValueError("Usage: agent validate <agent> [--scope ...]")
        agent = self.manager.validate(positional[0], scope=options.get("scope"))
        self.output(
            f"[OK] {agent.agent_id} is valid (schema {agent.schema_version}, "
            f"{agent.scope} scope)."
        )

    def promote(self, args: list[str]) -> None:
        _, positional = self._options(args)
        if len(positional) != 1:
            raise ValueError("Usage: agent promote <workspace-agent>")
        agent = self.manager.promote(positional[0])
        self.output(f"[OK] Promoted {agent.agent_id} to permanent scope.")

    def copy(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if len(positional) != 2:
            raise ValueError(
                "Usage: agent copy <agent> <new-name> [--scope ...] [--id ...]"
            )
        agent = self.manager.copy(
            positional[0],
            positional[1],
            scope=options.get("scope"),
            new_id=options.get("id"),
        )
        self._created(agent)

    def test(self, args: list[str]) -> None:
        _, positional = self._options(args)
        if len(positional) != 1:
            raise ValueError("Usage: agent test <agent>")
        self.output("Running one bounded agent test; no tools are available...")
        result = self.manager.test(positional[0])
        self.output(f"Agent Test: {result.agent.name}")
        self.output(f"Runtime: {result.provider}:{result.model}")
        self.output(result.response.summary)
        for item in result.response.recommendations:
            self.output(f"  - {item}")
        if result.response.risks:
            self.output("Risks:")
            for item in result.response.risks:
                self.output(f"  - {item}")
        self.output(f"Next action: {result.response.next_action}")

    def usage(self) -> None:
        self.output(
            "Agent commands: list, show, templates, create, edit, delete, "
            "enable, disable, validate, promote, copy, test"
        )

    def _created(self, agent) -> None:
        path = self.manager.repository(agent.scope).root / f"{agent.agent_id}.yaml"
        self.output(f'[OK] Agent "{agent.name}" created.')
        self.output(f"Saved to: {path}")

    @staticmethod
    def _options(args: list[str]) -> tuple[dict[str, str], list[str]]:
        options: dict[str, str] = {}
        positional: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if not token.startswith("--"):
                positional.append(token)
                index += 1
                continue
            key = token[2:].strip().lower()
            if not key:
                raise ValueError("Invalid empty option.")
            if key in {"yes"}:
                options[key] = "true"
                index += 1
                continue
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                raise ValueError(f"Option --{key} requires a value.")
            options[key] = args[index + 1]
            index += 2
        return options, positional

    @staticmethod
    def _scope(value: str) -> str:
        scope = str(value).strip().lower()
        if scope not in {"permanent", "workspace"}:
            raise ValueError("Scope must be permanent or workspace.")
        return scope

    @staticmethod
    def _csv(value: str) -> tuple[str, ...]:
        return tuple(
            item.strip().lower() for item in str(value).split(",") if item.strip()
        )

    @staticmethod
    def _permission_options(
        options: dict[str, str],
        *,
        capabilities: tuple[str, ...],
    ) -> AgentPermissionPolicy:
        defaults = AgentPermissionPolicy(
            network=(
                PermissionPolicy.APPROVAL
                if {"use_network", "web_research"} & set(capabilities)
                else PermissionPolicy.DENIED
            ),
            shell=(
                PermissionPolicy.APPROVAL
                if {"run_tests", "run_commands"} & set(capabilities)
                else PermissionPolicy.DENIED
            ),
            write_files=(
                PermissionPolicy.APPROVAL
                if "write_workspace" in capabilities
                else PermissionPolicy.DENIED
            ),
            git_write=(
                PermissionPolicy.APPROVAL
                if "write_git" in capabilities
                else PermissionPolicy.DENIED
            ),
            image_generation=(
                PermissionPolicy.APPROVAL
                if "generate_images" in capabilities
                else PermissionPolicy.DENIED
            ),
        )
        mapping = {
            "network": "network",
            "shell": "shell",
            "write-files": "write_files",
            "git-write": "git_write",
            "calendar": "calendar",
            "email": "email",
            "image-generation": "image_generation",
        }
        changes = {}
        for option, field_name in mapping.items():
            if option in options:
                changes[field_name] = PermissionPolicy.parse(
                    options[option], f"Agent {field_name} permission"
                )
        return replace(defaults, **changes)
