"""Small, bounded multi-role planning for Orion AI Team Phase 1."""
from __future__ import annotations

import json
import math
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
TEAM_STATUSES = frozenset({
    TEAM_STATUS_PLANNING,
    TEAM_STATUS_AWAITING_APPROVAL,
    TEAM_STATUS_FAILED,
})
TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,80}")


def _exact_mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    keys = set(value)
    missing = sorted(fields - keys)
    unknown = sorted(keys - fields)
    if missing:
        raise ValueError(f"{label} is missing required fields: {missing}")
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {unknown}")
    return value


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value.strip()


def _optional_string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    return value


def _timestamp(value: Any, label: str) -> tuple[str, datetime]:
    text = _required_string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset.")
    return text, parsed


def _string_list(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a JSON array of strings.")
    items = [item.strip() for item in value if item.strip()]
    if not allow_empty and not items:
        raise ValueError(f"{label} cannot be empty.")
    return items


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
        value = _exact_mapping(
            value,
            {"summary", "recommendations", "risks", "next_action"},
            "Role output",
        )
        summary = _required_string(value["summary"], "Role output summary")
        next_action = _required_string(value["next_action"], "Role output next_action")
        recommendations = cls._string_tuple(value["recommendations"], "recommendations")
        risks = cls._string_tuple(value["risks"], "risks", allow_empty=True)
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

    @classmethod
    def from_value(cls, value: Any) -> "TeamArtifact":
        value = _exact_mapping(value, {"role", "kind", "output", "created_at"}, "Team artifact")
        created_at, _ = _timestamp(value["created_at"], "Team artifact created_at")
        return cls(
            role=_required_string(value["role"], "Team artifact role"),
            kind=_required_string(value["kind"], "Team artifact kind"),
            output=RoleOutput.from_value(value["output"]),
            created_at=created_at,
        )


@dataclass(frozen=True)
class TeamMessage:
    sender: str
    recipient: str
    content: str
    created_at: str

    @classmethod
    def from_value(cls, value: Any) -> "TeamMessage":
        value = _exact_mapping(
            value, {"sender", "recipient", "content", "created_at"}, "Team message"
        )
        created_at, _ = _timestamp(value["created_at"], "Team message created_at")
        return cls(
            sender=_required_string(value["sender"], "Team message sender"),
            recipient=_required_string(value["recipient"], "Team message recipient"),
            content=_required_string(value["content"], "Team message content"),
            created_at=created_at,
        )


@dataclass(frozen=True)
class RoleUsage:
    role: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None
    estimated_tokens: bool = True

    @classmethod
    def from_value(cls, value: Any) -> "RoleUsage":
        value = _exact_mapping(
            value,
            {
                "role", "provider", "model", "input_tokens", "output_tokens",
                "estimated_cost_usd", "estimated_tokens",
            },
            "Team usage",
        )
        input_tokens = cls._token_count(value["input_tokens"], "Team usage input_tokens")
        output_tokens = cls._token_count(value["output_tokens"], "Team usage output_tokens")
        cost = value["estimated_cost_usd"]
        if cost is not None:
            if isinstance(cost, bool) or not isinstance(cost, (int, float)):
                raise ValueError("Team usage estimated_cost_usd must be a number or null.")
            cost = float(cost)
            if cost < 0 or not math.isfinite(cost):
                raise ValueError("Team usage estimated_cost_usd must be finite and non-negative.")
        if not isinstance(value["estimated_tokens"], bool):
            raise ValueError("Team usage estimated_tokens must be a boolean.")
        return cls(
            role=_required_string(value["role"], "Team usage role"),
            provider=_required_string(value["provider"], "Team usage provider"),
            model=_required_string(value["model"], "Team usage model"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            estimated_tokens=value["estimated_tokens"],
        )

    @staticmethod
    def _token_count(value: Any, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{label} must be a non-negative integer.")
        return value

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
        value = _exact_mapping(
            value,
            {
                "task_id", "goal", "status", "artifacts", "messages", "usage",
                "final_plan", "created_at", "updated_at", "error",
            },
            "Team task",
        )
        task_id = _required_string(value["task_id"], "Team task task_id")
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError("Team task task_id has an invalid format.")
        goal = _required_string(value["goal"], "Team task goal")
        status = _required_string(value["status"], "Team task status")
        if status not in TEAM_STATUSES:
            raise ValueError(f"Team task status is not recognized: {status}")
        created_at, created = _timestamp(value["created_at"], "Team task created_at")
        updated_at, updated = _timestamp(value["updated_at"], "Team task updated_at")
        if updated < created:
            raise ValueError("Team task updated_at cannot precede created_at.")
        for field_name in ("artifacts", "messages", "usage"):
            if not isinstance(value[field_name], list):
                raise ValueError(f"Team task {field_name} must be a JSON array.")
        artifacts = [TeamArtifact.from_value(item) for item in value["artifacts"]]
        messages = [TeamMessage.from_value(item) for item in value["messages"]]
        usage = [RoleUsage.from_value(item) for item in value["usage"]]
        final_plan = _string_list(value["final_plan"], "Team task final_plan")
        error = _optional_string(value["error"], "Team task error")
        if status == TEAM_STATUS_AWAITING_APPROVAL and not final_plan:
            raise ValueError("Awaiting-approval tasks require a final plan.")
        if status == TEAM_STATUS_FAILED and not error.strip():
            raise ValueError("Failed tasks require a sanitized error category.")
        return cls(
            task_id=task_id,
            goal=goal,
            status=status,
            artifacts=artifacts,
            messages=messages,
            usage=usage,
            final_plan=final_plan,
            created_at=created_at,
            updated_at=updated_at,
            error=error,
        )


class TeamTaskStore:
    """Persist individual team tasks beneath Orion's external user-data root."""

    TASK_ID = TASK_ID_PATTERN

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(self, task: TeamTask) -> Path:
        path = self._path(task.task_id)
        payload = task.to_dict()
        TeamTask.from_dict(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
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
            task = TeamTask.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"AI Team task is invalid: {task_id}") from exc
        if task.task_id != str(task_id).strip():
            raise ValueError(f"AI Team task identity does not match its filename: {task_id}")
        return task

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
