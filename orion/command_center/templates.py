"""Immutable built-in Command Center department templates."""
from __future__ import annotations

from dataclasses import dataclass

from orion.command_center.models import normalize_id


@dataclass(frozen=True)
class DepartmentTemplate:
    template_id: str
    name: str
    description: str
    icon: str
    recommended_roles: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.template_id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "recommended_roles": list(self.recommended_roles),
        }


DEPARTMENT_TEMPLATES = (
    DepartmentTemplate(
        "engineering",
        "Engineering",
        "Plans, builds, validates, documents, secures, and releases software.",
        "engineering",
        (
            "Planner", "Architect", "Software Engineer", "Tester", "Reviewer",
            "Documentation Writer", "Security Reviewer", "Performance Reviewer",
            "Release Manager",
        ),
    ),
    DepartmentTemplate(
        "marketing",
        "Marketing",
        "Coordinates positioning, content, search, social, and visual communication.",
        "marketing",
        (
            "Marketing Manager", "Copywriter", "Content Strategist",
            "SEO Specialist", "Social Media Manager", "Graphic Designer",
        ),
    ),
    DepartmentTemplate(
        "business",
        "Business",
        "Supports revenue, customers, research, and financial administration.",
        "business",
        (
            "Sales Manager", "Customer Support", "Research Analyst",
            "Finance Assistant",
        ),
    ),
    DepartmentTemplate(
        "automation",
        "Automation",
        "Coordinates approved communication, scheduling, and systems workflows.",
        "automation",
        (
            "Automation Coordinator", "Email Assistant", "Calendar Assistant",
            "Discord Assistant", "Systems Operator",
        ),
    ),
)


def department_templates() -> tuple[DepartmentTemplate, ...]:
    return DEPARTMENT_TEMPLATES


def get_department_template(identifier: str) -> DepartmentTemplate:
    value = str(identifier).strip().casefold()
    try:
        normalized = normalize_id(identifier, "Department template ID")
    except ValueError:
        normalized = ""
    matches = [
        item for item in DEPARTMENT_TEMPLATES
        if item.template_id == normalized or item.name.casefold() == value
    ]
    if not matches:
        raise FileNotFoundError(f"Department template not found: {identifier}")
    return matches[0]
