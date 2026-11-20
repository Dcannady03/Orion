"""Thin CLI adapter for Orion observability commands."""
from __future__ import annotations

import shlex
from typing import Callable

from orion.application.observability.handler import (
    ObservabilityApplicationHandler,
)
from orion.application.observability.health import HEALTH_SECTIONS
from orion.application.observability.logs import (
    DEFAULT_LOG_LIMIT,
    LogSearchRequest,
)
from orion.application.results import ApplicationResult
from orion.interfaces.cli.renderer import ApplicationResultRenderer


_COMMAND_WORDS = frozenset({"health", "logs", "diagnostics", "graphs"})


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
            elif command == "logs":
                result, as_json = self._logs(tokens[1:])
            elif command == "diagnostics":
                result, as_json = self._diagnostics(tokens[1:])
            elif command == "graphs":
                result, as_json = self._graphs(tokens[1:])
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
        record = False
        sections: list[str] = []
        for token in tokens:
            folded = token.casefold()
            if folded == "--json":
                as_json = True
            elif folded == "--record":
                record = True
            elif token.startswith("--"):
                raise ValueError(f"Unknown health option: {token}")
            else:
                sections.append(token)
        if len(sections) > 1:
            raise ValueError("health accepts at most one section.")
        section = sections[0] if sections else None
        return self.application.health(section, record=record), as_json

    def _graphs(self, tokens: list[str]) -> tuple[ApplicationResult, bool]:
        as_json = False
        sections: list[str] = []
        for token in tokens:
            if token.casefold() == "--json":
                as_json = True
            elif token.startswith("--"):
                raise ValueError(f"Unknown graphs option: {token}")
            else:
                sections.append(token)
        if len(sections) > 1:
            raise ValueError("graphs accepts at most one section.")
        section = sections[0] if sections else None
        return self.application.graphs(section), as_json

    def _logs(self, tokens: list[str]) -> tuple[ApplicationResult, bool]:
        as_json = False
        options: dict[str, str] = {}
        value_options = {
            "--severity", "--type", "--search", "--start", "--end",
            "--limit", "--source",
        }
        index = 0
        while index < len(tokens):
            token = tokens[index]
            folded = token.casefold()
            if folded == "--json":
                as_json = True
                index += 1
            elif folded in value_options:
                if index + 1 >= len(tokens):
                    raise ValueError(f"logs {token} needs a value.")
                options[folded] = tokens[index + 1]
                index += 2
            else:
                raise ValueError(f"Unknown logs option: {token}")
        limit = DEFAULT_LOG_LIMIT
        if "--limit" in options:
            try:
                limit = int(options["--limit"])
            except ValueError:
                raise ValueError("logs --limit must be an integer.")
        request = LogSearchRequest(
            severity=options.get("--severity"),
            event_type=options.get("--type"),
            search=options.get("--search"),
            start_time=options.get("--start"),
            end_time=options.get("--end"),
            limit=limit,
            source=options.get("--source", "all"),
        )
        return self.application.logs(request), as_json

    def _diagnostics(self, tokens: list[str]) -> tuple[ApplicationResult, bool]:
        as_json = False
        for token in tokens:
            if token.casefold() == "--json":
                as_json = True
            else:
                raise ValueError(f"Unknown diagnostics option: {token}")
        return self.application.diagnostics(), as_json

    @staticmethod
    def _usage(detail: str = "") -> ApplicationResult:
        message = (
            f"{detail}\n" if detail else ""
        ) + (
            "Usage: health [disk|providers|services|config|workspace|events] [--json] [--record]\n"
            "       logs [--severity S] [--type T] [--search TEXT]\n"
            "            [--start ISO] [--end ISO] [--limit N]\n"
            "            [--source all|events|files] [--json]\n"
            "       diagnostics [--json]\n"
            "       graphs [disk|providers|services|config|workspace|events|dependencies] [--json]\n"
            "Health checks, log searches, diagnostics, and graphs are read-only\n"
            "except `health --record`, which saves one snapshot for the graphs."
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
