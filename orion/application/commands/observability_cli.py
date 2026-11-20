"""Thin CLI adapter for Orion observability commands."""
from __future__ import annotations

import shlex
from typing import Callable

from orion.application.observability.handler import (
    ObservabilityApplicationHandler,
)
from orion.application.observability.health import HEALTH_SECTIONS
from orion.application.results import ApplicationResult
from orion.interfaces.cli.renderer import ApplicationResultRenderer


_COMMAND_WORDS = frozenset({"health"})


class ObservabilityCliAdapter:
    """Parse observability commands and render ApplicationResult."""

    def __init__(
        self,
        runtime,
        *,
        output_provider: Callable[[str], None] | None = None,
    ) -> None:
        application = getattr(runtime, "observability_application", None)
        self.application = application
        self.renderer = ApplicationResultRenderer(output_provider)
        self._output = output_provider or print

    def handle(self, payload: str) -> ApplicationResult:
        if not isinstance(self.application, ObservabilityApplicationHandler):
            return self._render(ApplicationResult.failure(
                "Observability application handler is unavailable.",
                data={"read_only": True},
                errors=("Observability application handler is unavailable.",),
            ))
        try:
            tokens = shlex.split(str(payload), posix=True)
        except ValueError as exc:
            return self._render(self._usage(
                f"Observability command could not be read: {exc}"
            ))
        if not tokens:
            return self._render(self._usage())
        command = tokens[0].casefold()
        if command not in _COMMAND_WORDS:
            return self._render(self._usage(
                f"Unknown observability command: {tokens[0]}"
            ))
        as_json = False
        try:
            if command == "health":
                result, as_json = self._health(tokens[1:])
            else:  # pragma: no cover - guarded by _COMMAND_WORDS
                result = self._usage(f"Unknown observability command: {tokens[0]}")
        except ValueError as exc:
            result = self._usage(str(exc))
        if as_json and result.ok:
            self._output(result.to_json(indent=2))
            return result
        return self._render(result)

    def _health(self, tokens: list[str]) -> tuple[ApplicationResult, bool]:
        as_json = False
        sections: list[str] = []
        for token in tokens:
            if token.casefold() == "--json":
                as_json = True
            elif token.startswith("--"):
                raise ValueError(f"Unknown health option: {token}")
            else:
                sections.append(token)
        if len(sections) > 1:
            raise ValueError("health accepts at most one section.")
        section = sections[0] if sections else None
        return self.application.health(section), as_json

    @staticmethod
    def _usage(detail: str = "") -> ApplicationResult:
        message = (
            f"{detail}\n" if detail else ""
        ) + (
            "Usage: health [disk|providers|services|config|workspace|events] [--json]\n"
            "Health checks are read-only and never change runtime state."
        )
        return ApplicationResult.failure(
            message,
            data={"read_only": True, "sections": list(HEALTH_SECTIONS)},
            errors=(detail or "An observability command is required.",),
        )

    def _render(self, result: ApplicationResult) -> ApplicationResult:
        self.renderer.render(result)
        return result


def dispatch_observability(runtime, raw_command: str) -> bool:
    """Recognize and delegate only the observability command family."""
    text = str(raw_command).strip()
    if not text:
        return False
    first = text.split(None, 1)[0].casefold()
    if first not in _COMMAND_WORDS:
        return False
    ObservabilityCliAdapter(runtime).handle(text)
    return True
