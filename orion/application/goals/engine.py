"""Deterministic, planning-only Goal Engine."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping

from orion.application.capabilities import (
    CapabilityDefinition,
    CapabilityRegistry,
)
from orion.application.goals.models import (
    CapabilityStep,
    GoalClassification,
    GoalContext,
    GoalExplanation,
    GoalPlan,
    GoalPreview,
    GoalRequest,
)


class GoalPlanningError(ValueError):
    """Raised when a safe, deterministic goal plan cannot be produced."""


@dataclass(frozen=True)
class _CapabilityRole:
    role: str
    required_terms: tuple[str, ...]
    preferred_terms: tuple[str, ...]
    stage: str
    reason: str


_CLASSIFICATION_RULES = (
    (
        "Release",
        (
            "release", "ship", "deployment", "deploy", "production ready",
            "publish version", "release candidate",
        ),
    ),
    (
        "Security",
        (
            "security", "secure", "vulnerability", "threat", "harden",
            "penetration", "permissions audit",
        ),
    ),
    (
        "Marketing",
        (
            "marketing", "campaign", "brand", "social media", "seo",
            "advertising", "promotion", "launch campaign",
        ),
    ),
    (
        "Documentation",
        (
            "documentation", "docs", "readme", "user guide", "manual",
            "document the", "write guide",
        ),
    ),
    (
        "Automation",
        (
            "automation", "automate", "scheduled workflow", "recurring",
            "workflow automation", "bot",
        ),
    ),
    (
        "Research",
        (
            "research", "analyze", "analyse", "compare", "investigate",
            "study", "evaluate options",
        ),
    ),
    (
        "Operations",
        (
            "operations", "monitoring", "incident", "reliability",
            "infrastructure", "uptime", "runbook",
        ),
    ),
    (
        "Personal Productivity",
        (
            "calendar", "email", "inbox", "schedule my", "organize my",
            "productivity", "today's agenda",
        ),
    ),
    (
        "Engineering",
        (
            "build", "implement", "develop", "code", "fix", "refactor",
            "website", "application", "app", "mod", "feature", "repository",
        ),
    ),
    (
        "Planning",
        (
            "plan", "strategy", "roadmap", "design", "prepare", "review",
            "outline",
        ),
    ),
)

_CATEGORY_ROLES = {
    "Engineering": ("inspect", "plan", "implement", "validate", "documentation"),
    "Marketing": ("job_create", "plan", "implement", "documentation"),
    "Documentation": ("plan", "implement", "documentation"),
    "Research": ("inspect", "plan"),
    "Automation": ("job_create", "plan", "implement", "validate"),
    "Security": ("inspect", "plan", "implement", "validate", "documentation"),
    "Operations": ("inspect", "plan", "implement", "validate"),
    "Planning": ("inspect", "plan"),
    "Release": ("plan", "implement", "validate", "documentation"),
    "Personal Productivity": ("plan",),
}

_CAPABILITY_ROLES = {
    "inspect": _CapabilityRole(
        "inspect",
        ("workspace", "inspect"),
        ("read", "active"),
        "context",
        "Workspace inspection establishes the project context before planning.",
    ),
    "job_create": _CapabilityRole(
        "job_create",
        ("job", "create"),
        ("command", "center", "inert"),
        "intake",
        "Command Center intake can represent the proposed cross-functional work.",
    ),
    "plan": _CapabilityRole(
        "plan",
        ("plan",),
        ("team", "bounded", "goal"),
        "planning",
        "Structured planning is required before any implementation is considered.",
    ),
    "implement": _CapabilityRole(
        "implement",
        ("implement",),
        ("team", "approval", "workspace"),
        "implementation",
        "Implementation would be required to produce the requested outcome.",
    ),
    "validate": _CapabilityRole(
        "validate",
        ("validate",),
        ("team", "test", "read"),
        "validation",
        "Validation would verify the proposed implementation against the goal.",
    ),
    "documentation": _CapabilityRole(
        "documentation",
        ("documentation", "review"),
        ("team", "read"),
        "documentation_review",
        "Documentation review would check that the outcome remains understandable.",
    ),
    "email_search": _CapabilityRole(
        "email_search",
        ("email", "search"),
        ("connected", "read"),
        "information_collection",
        "Read-only email search would gather the requested communication context.",
    ),
    "calendar_today": _CapabilityRole(
        "calendar_today",
        ("calendar", "today"),
        ("show", "read"),
        "information_collection",
        "Read-only calendar inspection would gather the requested schedule context.",
    ),
    "image_generate": _CapabilityRole(
        "image_generate",
        ("image", "generate"),
        ("provider",),
        "asset_generation",
        "Image generation would create the requested visual asset.",
    ),
    "agent_create": _CapabilityRole(
        "agent_create",
        ("agent", "create"),
        ("orion",),
        "configuration",
        "Agent creation would define the requested reusable Orion role.",
    ),
}

_DEPARTMENT_PREFERENCES = {
    "Engineering": ("engineering",),
    "Marketing": ("marketing",),
    "Documentation": ("documentation", "engineering"),
    "Research": ("research", "business"),
    "Automation": ("automation",),
    "Security": ("security", "engineering"),
    "Operations": ("operations", "automation"),
    "Planning": ("planning", "business", "engineering"),
    "Release": ("release", "engineering"),
    "Personal Productivity": ("automation", "business"),
}


class GoalEngine:
    """Build deterministic capability plans without invoking capabilities."""

    SAFETY_BOUNDARY = (
        "Informational plan only: no capability, provider, agent, job, approval, "
        "execution engine, repository write, or workspace change was invoked."
    )

    def __init__(
        self,
        capability_registry: CapabilityRegistry,
        *,
        workspace_manager=None,
        project_context=None,
        command_center=None,
    ) -> None:
        if not isinstance(capability_registry, CapabilityRegistry):
            raise TypeError("Goal Engine requires a CapabilityRegistry.")
        self.capability_registry = capability_registry
        self.workspace_manager = workspace_manager
        self.project_context = project_context
        self.command_center = command_center

    def classify(self, goal: GoalRequest | str) -> GoalClassification:
        """Classify a goal using authoritative deterministic rules only."""
        if isinstance(goal, GoalRequest):
            text = " ".join(
                item
                for item in (goal.goal_text, goal.requested_outcome)
                if item
            )
        else:
            text = str(goal).strip()
        if not text:
            raise GoalPlanningError("Goal text is required for classification.")
        normalized = " ".join(re.findall(r"[a-z0-9']+", text.casefold()))
        matches: list[tuple[str, tuple[str, ...]]] = []
        for category, terms in _CLASSIFICATION_RULES:
            found = tuple(term for term in terms if self._contains(normalized, term))
            if found:
                matches.append((category, found))
        if not matches:
            raise GoalPlanningError(
                "The goal could not be classified deterministically. "
                "Add an outcome such as build, research, document, automate, "
                "secure, operate, plan, market, or release."
            )
        category, matched = matches[0]
        competing = tuple(item[0] for item in matches[1:])
        confidence = min(0.98, 0.78 + (0.06 * min(len(matched), 3)))
        if competing:
            confidence = max(0.68, confidence - 0.08)
        evidence = ", ".join(f'"{item}"' for item in matched)
        reason = (
            f"{category} is the first authoritative category whose rules matched "
            f"{evidence}."
        )
        if competing:
            reason += (
                " More-specific precedence resolved additional matches for "
                + ", ".join(competing)
                + "."
            )
        return GoalClassification(category, round(confidence, 2), reason, matched)

    def plan(self, request: GoalRequest) -> GoalPlan:
        """Resolve context and return a registry-backed proposal only."""
        if not isinstance(request, GoalRequest):
            raise TypeError("Goal Engine plan requires a GoalRequest.")
        classification = self.classify(request)
        workspace, workspace_reason, project_name = self._resolve_workspace(request)
        department, department_reason, department_warning = (
            self._resolve_department(request, classification.category)
        )
        context = GoalContext(
            workspace=str(workspace["path"]),
            workspace_name=str(workspace["name"]),
            workspace_source=str(workspace["source"]),
            workspace_mode=str(workspace["mode"]),
            project_name=project_name,
            department_id=str(department.get("id", "")),
            department_name=str(department.get("name", "")),
            priority=request.priority,
        )
        roles = self._roles_for(request, classification.category)
        definitions, discovery_warnings = self._discover_capabilities(roles)
        if not definitions:
            raise GoalPlanningError(
                "No registered Orion capabilities match this goal. "
                "Register a compatible planning capability before retrying."
            )
        steps = self._build_steps(definitions, classification.category)
        approval_required = any(item.requires_approval for item in steps)
        stages = self._estimated_stages(steps)
        warnings = list(discovery_warnings)
        if department_warning:
            warnings.append(department_warning)
        if request.allow_ai_planning:
            warnings.append(
                "AI planning was requested but is not enabled in v0.8.2; "
                "the authoritative deterministic planner was used."
            )
        if request.attachments:
            warnings.append(
                "Attachment references were recorded but not opened or inspected."
            )
        if request.provider_preferences:
            warnings.append(
                "Provider preferences were recorded but no provider was contacted."
            )
        risks = (
            (
                "Future execution includes state-changing capabilities; their "
                "existing application-layer safeguards still apply."
            ),
            (
                "This preview estimates capability fit from registry metadata and "
                "does not prove provider, agent, or execution-engine availability."
            ),
        )
        if not any(item.mutates_state for item in steps):
            risks = (risks[1],)
        goal_id = self._goal_id(request, classification, context, steps)
        approval_boundaries = tuple(
            f"{item.estimated_stage}: approval required before {item.capability_id}"
            for item in steps
            if item.requires_approval
        )
        capability_reasons = tuple(
            f"{item.capability_id}: {item.reason}" for item in steps
        )
        explanation = GoalExplanation(
            summary=(
                f"The deterministic planner classified this as "
                f"{classification.category} and selected {len(steps)} "
                "currently registered capabilities."
            ),
            classification_reason=classification.reason,
            workspace_reason=workspace_reason,
            department_reason=department_reason,
            capability_reasons=capability_reasons,
            approval_reason=(
                "Approval is predicted because at least one selected capability "
                "declares requires_approval=true in the registry."
                if approval_required
                else "No selected capability declares an approval requirement."
            ),
            safety_boundary=self.SAFETY_BOUNDARY,
        )
        preview = GoalPreview(
            goal_id=goal_id,
            goal=request.goal_text,
            classification=classification.category,
            workspace=context.workspace,
            department=context.department_name or "Unassigned",
            execution_plan=tuple(
                f"{item.estimated_stage}: {item.capability_id}"
                for item in steps
            ),
            approval_boundaries=approval_boundaries or (
                "No registry-declared approval boundary in this plan.",
            ),
            approval_required=approval_required,
            estimated_stages=stages,
        )
        quoted_goal = request.goal_text.replace('"', '\\"')
        return GoalPlan(
            goal_id=goal_id,
            goal=request.goal_text,
            classification=classification.category,
            confidence=classification.confidence,
            context=context,
            capability_steps=steps,
            estimated_stages=stages,
            approval_required=approval_required,
            warnings=tuple(dict.fromkeys(warnings)),
            risks=risks,
            explanation=explanation,
            execution_preview=preview,
            next_actions=(
                f'goal explain "{quoted_goal}"',
                f'goal validate "{quoted_goal}"',
            ),
        )

    @staticmethod
    def _contains(normalized: str, term: str) -> bool:
        return re.search(
            rf"(?:^|\s){re.escape(term)}(?:$|\s)",
            normalized,
        ) is not None

    def _resolve_workspace(
        self,
        request: GoalRequest,
    ) -> tuple[dict[str, str], str, str]:
        source = ""
        if request.workspace:
            root = Path(request.workspace).expanduser().resolve()
            source = "explicit"
        else:
            active = getattr(self.workspace_manager, "root", None)
            if active is not None:
                root = Path(active).expanduser().resolve()
                source = "active_workspace"
            else:
                project_root = getattr(self.project_context, "workspace_root", None)
                if project_root is None:
                    raise GoalPlanningError(
                        "No workspace could be resolved. Supply --workspace or "
                        "bind an active Orion workspace."
                    )
                root = Path(project_root).expanduser().resolve()
                source = "project_context"
        if not root.exists():
            raise GoalPlanningError(f"Resolved workspace does not exist: {root}")
        if not root.is_dir():
            raise GoalPlanningError(f"Resolved workspace is not a directory: {root}")

        project_name = ""
        bound = False
        context_root = getattr(self.project_context, "workspace_root", None)
        if context_root is not None:
            try:
                bound = Path(context_root).expanduser().resolve() == root
            except (OSError, RuntimeError, ValueError):
                bound = False
        if bound and bool(getattr(self.project_context, "initialized", False)):
            try:
                project = self.project_context.project()
                project_name = str(project.get("name", "")).strip()
            except (FileNotFoundError, OSError, RuntimeError, ValueError):
                project_name = ""
        mode = "bound" if bound else source
        reason = (
            f"Workspace {root} was resolved from {source.replace('_', ' ')} "
            "without changing Orion's active workspace."
        )
        return (
            {
                "path": str(root),
                "name": root.name or str(root),
                "source": source,
                "mode": mode,
            },
            reason,
            project_name,
        )

    def _resolve_department(
        self,
        request: GoalRequest,
        category: str,
    ) -> tuple[dict[str, str], str, str]:
        try:
            departments = tuple(
                item
                for item in (
                    self.command_center.departments()
                    if self.command_center is not None
                    else ()
                )
                if bool(getattr(item, "enabled", False))
            )
        except (OSError, PermissionError, RuntimeError, ValueError) as exc:
            raise GoalPlanningError(
                f"Command Center departments could not be inspected: {exc}"
            ) from exc
        if request.department:
            value = request.department.casefold()
            matches = tuple(
                item
                for item in departments
                if value
                in {
                    str(getattr(item, "department_id", "")).casefold(),
                    str(getattr(item, "name", "")).casefold(),
                }
            )
            if not matches:
                available = ", ".join(
                    str(getattr(item, "name", ""))
                    for item in departments
                ) or "none"
                raise GoalPlanningError(
                    f"Department not found or disabled: {request.department}. "
                    f"Available departments: {available}."
                )
            selected = matches[0]
            return (
                {
                    "id": str(getattr(selected, "department_id", "")),
                    "name": str(getattr(selected, "name", "")),
                },
                (
                    f"Department {getattr(selected, 'name', '')} was explicitly "
                    "requested and exists in Command Center."
                ),
                "",
            )
        preferences = _DEPARTMENT_PREFERENCES[category]
        for preference in preferences:
            for item in departments:
                identity = str(getattr(item, "department_id", "")).casefold()
                name = str(getattr(item, "name", "")).casefold()
                if preference in {identity, name}:
                    selected_name = str(getattr(item, "name", ""))
                    return (
                        {
                            "id": str(getattr(item, "department_id", "")),
                            "name": selected_name,
                        },
                        (
                            f"{selected_name} is an existing enabled department "
                            f"and is the deterministic owner for {category} goals."
                        ),
                        "",
                    )
        warning = (
            f"No enabled Command Center department matches {category}; "
            "the plan remains unassigned."
        )
        return (
            {"id": "", "name": ""},
            warning,
            warning,
        )

    def _roles_for(self, request: GoalRequest, category: str) -> tuple[str, ...]:
        roles = list(_CATEGORY_ROLES[category])
        normalized = request.goal_text.casefold()
        optional: list[tuple[tuple[str, ...], str]] = [
            (("email", "inbox"), "email_search"),
            (("calendar", "agenda", "schedule"), "calendar_today"),
            (("image", "logo", "illustration", "artwork"), "image_generate"),
            (("agent",), "agent_create"),
        ]
        for terms, role in optional:
            if any(term in normalized for term in terms) and role not in roles:
                roles.insert(-1 if roles else 0, role)
        return tuple(roles)

    def _discover_capabilities(
        self,
        roles: tuple[str, ...],
    ) -> tuple[tuple[tuple[_CapabilityRole, CapabilityDefinition], ...], tuple[str, ...]]:
        catalog = self.capability_registry.list()
        selected: list[tuple[_CapabilityRole, CapabilityDefinition]] = []
        warnings: list[str] = []
        used: set[str] = set()
        for role_name in roles:
            role = _CAPABILITY_ROLES[role_name]
            candidates = []
            for definition in catalog:
                if definition.capability_id in used:
                    continue
                searchable = self._capability_terms(definition)
                if not all(term in searchable for term in role.required_terms):
                    continue
                score = sum(
                    4 for term in role.required_terms
                    if term in self._id_terms(definition.capability_id)
                )
                score += sum(
                    1 for term in role.preferred_terms
                    if term in searchable
                )
                candidates.append((score, definition.capability_id, definition))
            if not candidates:
                warnings.append(
                    f"No registered capability matched the {role.stage} stage."
                )
                continue
            definition = sorted(
                candidates,
                key=lambda item: (-item[0], item[1].casefold()),
            )[0][2]
            used.add(definition.capability_id)
            selected.append((role, definition))
        return tuple(selected), tuple(warnings)

    def _build_steps(
        self,
        definitions: tuple[tuple[_CapabilityRole, CapabilityDefinition], ...],
        category: str,
    ) -> tuple[CapabilityStep, ...]:
        steps = []
        for number, (role, definition) in enumerate(definitions, 1):
            required = self._schema_strings(definition.input_schema, "required")
            output_properties = definition.output_schema.get("properties", {})
            output_required = self._schema_strings(
                definition.output_schema,
                "required",
            )
            if output_required:
                outputs = output_required
            elif isinstance(output_properties, Mapping):
                outputs = tuple(sorted(str(key) for key in output_properties))
            else:
                outputs = ()
            steps.append(CapabilityStep(
                step_number=number,
                capability_id=definition.capability_id,
                reason=f"{category} goal: {role.reason}",
                requires_approval=definition.requires_approval,
                mutates_state=definition.mutates_state,
                estimated_stage=role.stage,
                required_inputs=required,
                expected_outputs=outputs,
                required_permissions=definition.required_permissions,
            ))
        return tuple(steps)

    @staticmethod
    def _schema_strings(schema: Mapping[str, object], key: str) -> tuple[str, ...]:
        value = schema.get(key, ())
        if not isinstance(value, (tuple, list)):
            return ()
        return tuple(str(item) for item in value)

    @staticmethod
    def _id_terms(capability_id: str) -> frozenset[str]:
        return frozenset(
            term
            for term in re.split(r"[._-]+", capability_id.casefold())
            if term
        )

    @classmethod
    def _capability_terms(
        cls,
        definition: CapabilityDefinition,
    ) -> frozenset[str]:
        return cls._id_terms(definition.capability_id) | frozenset(
            re.findall(r"[a-z0-9]+", definition.description.casefold())
        )

    @staticmethod
    def _estimated_stages(
        steps: tuple[CapabilityStep, ...],
    ) -> tuple[str, ...]:
        stages: list[str] = []
        for item in steps:
            if item.requires_approval and "approval" not in stages:
                stages.append("approval")
            if item.estimated_stage not in stages:
                stages.append(item.estimated_stage)
        return tuple(stages)

    @staticmethod
    def _goal_id(
        request: GoalRequest,
        classification: GoalClassification,
        context: GoalContext,
        steps: tuple[CapabilityStep, ...],
    ) -> str:
        canonical = json.dumps(
            {
                "request": request.to_dict(),
                "classification": classification.category,
                "context": context.to_dict(),
                "capabilities": [item.capability_id for item in steps],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return "goal-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
