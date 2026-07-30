"""Small, dependency-free renderer for structured application results."""
from __future__ import annotations

import json
from typing import Callable, Mapping

from orion.application.results import ApplicationResult


class ApplicationResultRenderer:
    """Render an application result while keeping business logic UI-neutral."""

    def __init__(self, output: Callable[[str], None] | None = None) -> None:
        self.output = output or print

    def render(self, result: ApplicationResult) -> None:
        if not isinstance(result, ApplicationResult):
            raise TypeError("CLI renderer requires an ApplicationResult.")
        if result.message:
            self.output(result.message)
        elif result.data:
            self.output(
                json.dumps(
                    self._plain(result.data),
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                )
            )
        self._render_unique("Warning", result.warnings, result.message)
        self._render_unique("Error", result.errors, result.message)
        self._render_unique("Next", result.next_actions, result.message)

    def _render_unique(
        self,
        label: str,
        values: tuple[str, ...],
        rendered_message: str,
    ) -> None:
        for value in values:
            if value not in rendered_message:
                self.output(f"{label}: {value}")

    @classmethod
    def _plain(cls, value):
        if isinstance(value, Mapping):
            return {str(key): cls._plain(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [cls._plain(item) for item in value]
        return value


def render_application_result(
    result: ApplicationResult,
    *,
    output: Callable[[str], None] | None = None,
) -> None:
    ApplicationResultRenderer(output).render(result)
