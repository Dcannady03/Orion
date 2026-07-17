"""Small, bounded multi-role planning for Orion AI Team Phase 1."""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


TEAM_STATUS_PLANNING = "planning"
TEAM_STATUS_AWAITING_APPROVAL = "awaiting_approval"
TEAM_STATUS_FAILED = "failed"


class TeamPlanningError(RuntimeError):
    """Raised when a bounded planning run cannot produce structured output."""

    def __init__(self, message: str, *, task_id: str = "") -> None:
        super().__init__(message)
        self.task_id = task_id


@dataclass(frozen=True)
class RoleOutput:
    summary: str
    recommendations: tuple[str, ...]
    risks: tuple[str, ...]
    next_action: str

    @classmethod
    def from_value(cls, value: Any) -> "RoleOutput":
        if not isinstance(value, dict):
            raise ValueError("Role output must be a JSON object.")
        if not isinstance(value.get("summary"), str) or not isinstance(value.get("next_action"), str):
            raise ValueError("Role output requires summary and next_action strings.")
        summary = value["summary"].strip()
        next_action = value["next_action"].strip()
        recommendations = cls._string_tuple(value.get("recommendations"), "recommendations")
        risks = cls._string_tuple(value.get("risks", []), "risks", allow_empty=True)
        if not summary or not next_action:
            raise ValueError("Role output requires summary and next_action strings.")
        return cls(summary, recommendations, risks, next_action)

    @staticmethod
    def _string_tuple(value: Any, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise ValueError(f"Role output {name} must be a JSON array.")
        if any(not isinstance(item, str) for item in value):
            raise ValueError(f"Role output {name} must contain strings only.")
        items = tuple(item.strip() for item in value if item.strip())
        if not items and not allow_empty:
            raise ValueError(f"Role output {name} cannot be empty.")
        return items

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "recommendations": list(self.recommendations),
            "risks": list(self.risks),
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class TeamArtifact:
    role: str
    kind: str
    output: RoleOutput
    created_at: str


@dataclass(frozen=True)
class TeamMessage:
    sender: str
    recipient: str
    content: str
    created_at: str


@dataclass(frozen=True)
class RoleUsage:
    role: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None
    estimated_tokens: bool = True

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TeamTask:
    task_id: str
    goal: str
    status: str
    artifacts: list[TeamArtifact] = field(default_factory=list)
    messages: list[TeamMessage] = field(default_factory=list)
    usage: list[RoleUsage] = field(default_factory=list)
    final_plan: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    error: str = ""

    @property
    def total_tokens(self) -> int:
        return sum(item.total_tokens for item in self.usage)

    @property
    def estimated_cost_usd(self) -> float | None:
        if not self.usage or any(item.estimated_cost_usd is None for item in self.usage):
            return None
        return round(sum(item.estimated_cost_usd or 0.0 for item in self.usage), 8)

    def artifact(self, role: str) -> TeamArtifact | None:
        return next((item for item in self.artifacts if item.role == role), None)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for artifact in value["artifacts"]:
            artifact["output"]["recommendations"] = list(artifact["output"]["recommendations"])
            artifact["output"]["risks"] = list(artifact["output"]["risks"])
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TeamTask":
        artifacts = [
            TeamArtifact(
                role=str(item.get("role", "")),
                kind=str(item.get("kind", "")),
                output=RoleOutput.from_value(item.get("output", {})),
                created_at=str(item.get("created_at", "")),
            )
            for item in value.get("artifacts", [])
        ]
        messages = [TeamMessage(**item) for item in value.get("messages", [])]
        usage = [RoleUsage(**item) for item in value.get("usage", [])]
        return cls(
            task_id=str(value.get("task_id", "")),
            goal=str(value.get("goal", "")),
            status=str(value.get("status", "")),
            artifacts=artifacts,
            messages=messages,
            usage=usage,
            final_plan=[str(item) for item in value.get("final_plan", [])],
            created_at=str(value.get("created_at", "")),
            updated_at=str(value.get("updated_at", "")),
            error=str(value.get("error", "")),
        )


class TeamTaskStore:
    """Persist individual team tasks beneath Orion's external user-data root."""

    TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,80}")

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(self, task: TeamTask) -> Path:
        path = self._path(task.task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(task.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        temporary.replace(path)
        return path

    def load(self, task_id: str) -> TeamTask:
        path = self._path(task_id)
        if not path.is_file():
            raise FileNotFoundError(f"AI Team task not found: {task_id}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"AI Team task could not be read: {task_id}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"AI Team task is invalid: {task_id}")
        try:
            return TeamTask.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"AI Team task is invalid: {task_id}") from exc

    def recent(self, limit: int = 10) -> list[TeamTask]:
        if limit <= 0:
            return []
        tasks: list[TeamTask] = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                tasks.append(self.load(path.stem))
            except (OSError, ValueError):
                continue
            if len(tasks) >= limit:
                break
        return tasks

    def _path(self, task_id: str) -> Path:
        value = str(task_id).strip()
        if not self.TASK_ID.fullmatch(value):
            raise ValueError("Invalid AI Team task ID.")
        return self.root / f"{value}.json"


@dataclass(frozen=True)
class TeamRole:
    name: str
    provider: str
    model: str
    active: bool


class TeamOrchestrator:
    """Run exactly two planning roles, consolidate their work, then stop."""

    ROLE_NAMES = ("architect", "engineer", "reviewer")
    ACTIVE_ROLES = ("architect", "engineer")
    MAX_ROLE_RESPONSE_CHARS = 50_000
    ARCHITECT_SYSTEM_PROMPT = """You are Orion's Architect role.
Create a deliberately small, implementation-ready plan for the supplied goal.
Planning only: do not modify code, run tools, create branches, commits, or pull requests.
Return exactly one JSON object and no Markdown with these keys:
summary (string), recommendations (non-empty array of ordered plan steps),
risks (array of concise risks), next_action (string)."""
    ENGINEER_SYSTEM_PROMPT = """You are Orion's Engineer Review role.
Critique the Architect artifact and return the revised final implementation steps.
Put the consolidated ordered steps in recommendations, not merely review comments.
Planning only: do not modify code, run tools, create branches, commits, or pull requests.
Return exactly one JSON object and no Markdown with these keys:
summary (string), recommendations (non-empty array of ordered revised steps),
risks (array of concise risks), next_action (string)."""

    def __init__(
        self,
        config_manager,
        store: TeamTaskStore,
        provider_factory,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.config = config_manager
        self.store = store
        self.provider_factory = provider_factory
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or self._new_task_id

    def plan(self, goal: str) -> TeamTask:
        if not bool(self.config.get("team.enabled", True)):
            raise ValueError("AI Team is disabled in configuration.")
        normalized = " ".join(str(goal).split()).strip()
        if not normalized:
            raise ValueError("AI Team goal cannot be empty.")
        if len(normalized) > 4000:
            raise ValueError("AI Team goal must be 4,000 characters or fewer.")

        timestamp = self._timestamp()
        task = TeamTask(
            task_id=self._id_factory(),
            goal=normalized,
            status=TEAM_STATUS_PLANNING,
            created_at=timestamp,
            updated_at=timestamp,
        )
        task.messages.append(TeamMessage("coordinator", "architect", normalized, timestamp))
        self.store.save(task)

        active_role = "architect"
        try:
            architect_prompt = (
                f"Goal:\n{normalized}\n\n"
                "Produce a provider-neutral plan with clear boundaries, tests, configuration, "
                "persistence, and documentation considerations."
            )
            architect, architect_usage = self._run_role(
                "architect", architect_prompt, self.ARCHITECT_SYSTEM_PROMPT
            )
            architect_time = self._timestamp()
            task.artifacts.append(TeamArtifact("architect", "implementation_plan", architect, architect_time))
            task.usage.append(architect_usage)
            task.messages.append(TeamMessage(
                "architect", "engineer", json.dumps(architect.to_dict(), ensure_ascii=False), architect_time
            ))
            task.updated_at = architect_time
            self.store.save(task)

            active_role = "engineer"
            engineer_prompt = (
                f"Goal:\n{normalized}\n\n"
                "Architect artifact:\n"
                f"{json.dumps(architect.to_dict(), indent=2, ensure_ascii=False)}\n\n"
                "Return a consolidated final plan that addresses gaps and keeps the MVP bounded."
            )
            engineer, engineer_usage = self._run_role(
                "engineer", engineer_prompt, self.ENGINEER_SYSTEM_PROMPT
            )
            engineer_time = self._timestamp()
            task.artifacts.append(TeamArtifact("engineer", "engineering_review", engineer, engineer_time))
            task.usage.append(engineer_usage)
            task.messages.append(TeamMessage(
                "engineer", "coordinator", json.dumps(engineer.to_dict(), ensure_ascii=False), engineer_time
            ))
            task.final_plan = list(engineer.recommendations)
            task.status = TEAM_STATUS_AWAITING_APPROVAL
            task.updated_at = engineer_time
            self.store.save(task)
            return task
        except Exception as exc:
            failed_time = self._timestamp()
            category = type(exc).__name__
            task.status = TEAM_STATUS_FAILED
            task.error = f"{active_role.title()} role failed ({category})."
            task.messages.append(TeamMessage(active_role, "coordinator", task.error, failed_time))
            task.updated_at = failed_time
            self.store.save(task)
            if isinstance(exc, TeamPlanningError):
                raise TeamPlanningError(str(exc), task_id=task.task_id) from exc
            raise TeamPlanningError(task.error, task_id=task.task_id) from exc

    def task(self, task_id: str) -> TeamTask:
        return self.store.load(task_id)

    def recent(self, limit: int = 10) -> list[TeamTask]:
        return self.store.recent(limit)

    def roles(self) -> tuple[TeamRole, ...]:
        roles = []
        for name in self.ROLE_NAMES:
            provider, model = self._resolved_role(name)
            roles.append(TeamRole(name, provider, model, name in self.ACTIVE_ROLES))
        return tuple(roles)

    def _run_role(self, role: str, prompt: str, system_prompt: str) -> tuple[RoleOutput, RoleUsage]:
        provider_key, model = self._resolved_role(role)
        provider = self.provider_factory.create(provider_key)
        configured_model = str(
            self.config.get(f"team.roles.{role}.model", "configured-default")
        ).strip()
        if configured_model != "configured-default":
            if not hasattr(provider, "select_model"):
                raise ValueError(f"{provider_key} does not support role-specific model selection.")
            provider.select_model(configured_model)
            model = configured_model
        raw = provider.chat(prompt, system_prompt=system_prompt)
        if len(str(raw)) > self.MAX_ROLE_RESPONSE_CHARS:
            raise TeamPlanningError("AI Team role response exceeded the 50,000-character limit.")
        output = self._parse_output(raw)
        input_tokens = self._estimate_tokens(system_prompt + "\n" + prompt)
        output_tokens = self._estimate_tokens(raw)
        usage = RoleUsage(
            role=role,
            provider=provider_key,
            model=str(getattr(provider, "model", model)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=self._estimate_cost(provider_key, input_tokens, output_tokens),
        )
        return output, usage

    def _resolved_role(self, role: str) -> tuple[str, str]:
        configured_provider = str(
            self.config.get(f"team.roles.{role}.provider", "configured-default")
        ).strip().lower()
        provider = (
            str(self.config.get("providers.default", "ollama")).strip().lower()
            if configured_provider == "configured-default"
            else configured_provider
        )
        if provider not in {"ollama", "openai", "gemini"}:
            raise ValueError(f"Unsupported AI Team provider for {role}: {configured_provider}")
        configured_model = str(
            self.config.get(f"team.roles.{role}.model", "configured-default")
        ).strip()
        model = (
            str(self.config.get(f"providers.{provider}.model", "configured-default"))
            if configured_model == "configured-default"
            else configured_model
        )
        return provider, model

    def _estimate_cost(self, provider: str, input_tokens: int, output_tokens: int) -> float | None:
        input_rate = self.config.get(f"team.pricing.{provider}.input_per_million", None)
        output_rate = self.config.get(f"team.pricing.{provider}.output_per_million", None)
        if input_rate is None or output_rate is None:
            return None
        try:
            return round(
                (input_tokens * float(input_rate) + output_tokens * float(output_rate)) / 1_000_000,
                8,
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_output(raw: str) -> RoleOutput:
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
            raise TeamPlanningError("AI Team role returned invalid JSON.") from exc
        try:
            return RoleOutput.from_value(value)
        except ValueError as exc:
            raise TeamPlanningError(f"AI Team role returned an invalid schema: {exc}") from exc

    @staticmethod
    def _estimate_tokens(value: str) -> int:
        return max(1, (len(value) + 3) // 4)

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat()

    def _new_task_id(self) -> str:
        stamp = self._now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"team-{stamp}-{uuid4().hex[:6]}"
