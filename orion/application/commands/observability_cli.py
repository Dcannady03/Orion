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
from orion.application.observability.recovery import DEFAULT_RECOVERY_LIMIT
from orion.application.results import ApplicationResult
from orion.interfaces.cli.renderer import ApplicationResultRenderer


_COMMAND_WORDS = frozenset(
    {"health", "logs", "diagnostics", "graphs", "backup", "recovery", "audit", "runbooks", "retry", "remediate", "docker"}
)


def _split_command(payload: str) -> list[str]:
    """Split a command line, keeping Windows backslashes intact.

    shlex in POSIX mode strips backslashes, which mangles Windows paths
    (``C:\\Users\\...`` becomes ``C:Users...``). Split without POSIX
    processing instead and strip one layer of surrounding quotes.
    """
    tokens = shlex.split(str(payload), posix=False)
    cleaned: list[str] = []
    for token in tokens:
        if (
            len(token) >= 2
            and token[0] == token[-1]
            and token[0] in {"'", '"'}
        ):
            cleaned.append(token[1:-1])
        else:
            cleaned.append(token)
    return cleaned


class ObservabilityCliAdapter:
    """Parse observability commands and render ApplicationResult."""

    def __init__(
        self,
        runtime,
        *,
        output_provider: Callable[[str], None] | None = None,
        input_provider: Callable[[str], str] | None = None,
    ) -> None:
        application = getattr(runtime, "observability_application", None)
        self.application = application
        self.renderer = ApplicationResultRenderer(output_provider)
        self._output = output_provider or print
        self._input = input_provider or input

    def handle(self, payload: str) -> ApplicationResult:
        if not isinstance(self.application, ObservabilityApplicationHandler):
            return self._render(ApplicationResult.failure(
                "Observability application handler is unavailable.",
                data={"read_only": True},
                errors=("Observability application handler is unavailable.",),
            ))
        try:
            tokens = _split_command(payload)
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
            elif command == "backup":
                result, as_json = self._backup(tokens[1:])
            elif command == "recovery":
                result, as_json = self._recovery(tokens[1:])
            elif command == "audit":
                result, as_json = self._audit(tokens[1:])
            elif command == "runbooks":
                result, as_json = self._runbooks(tokens[1:])
            elif command == "retry":
                result, as_json = self._retry(tokens[1:])
            elif command == "remediate":
                result, as_json = self._remediate(tokens[1:])
            elif command == "docker":
                result, as_json = self._docker(tokens[1:])
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

    def _backup(self, tokens: list[str]) -> tuple[ApplicationResult, bool]:
        as_json = False
        args: list[str] = []
        for token in tokens:
            if token.casefold() == "--json":
                as_json = True
            else:
                args.append(token)
        if not args:
            raise ValueError(
                "backup needs a subcommand: export, list, verify, or import."
            )
        subcommand = args[0].casefold()
        if subcommand == "export":
            return self._backup_export(args[1:], as_json)
        if subcommand == "list":
            if args[1:]:
                raise ValueError("backup list takes no arguments.")
            return self.application.backup_list(), as_json
        if subcommand == "verify":
            if len(args) != 2:
                raise ValueError("backup verify needs a path.")
            return self.application.backup_verify(args[1]), as_json
        if subcommand == "import":
            if as_json:
                raise ValueError(
                    "backup import is interactive and does not support --json."
                )
            if len(args) != 2:
                raise ValueError("backup import needs a path.")
            return self._backup_import(args[1])
        raise ValueError(f"Unknown backup subcommand: {args[0]}")

    def _backup_export(
        self, args: list[str], as_json: bool
    ) -> tuple[ApplicationResult, bool]:
        name = None
        index = 0
        while index < len(args):
            if args[index].casefold() == "--name":
                if index + 1 >= len(args):
                    raise ValueError("backup export --name needs a value.")
                name = args[index + 1]
                index += 2
            else:
                raise ValueError(f"Unknown backup export option: {args[index]}")
        return self.application.backup_export(name), as_json

    def _backup_import(self, path: str) -> tuple[ApplicationResult, bool]:
        plan = self.application.backup_import_plan(path)
        if not plan.ok:
            return plan, False
        self._render(plan)
        self._output("Sensitive action: replace Orion data with this backup.")
        answer = (self._input("Approve? [y/N]: ") or "").strip().lower()
        if answer not in {"y", "yes"}:
            return ApplicationResult.failure(
                "Import cancelled.",
                data={"read_only": True},
                errors=("Import cancelled.",),
            ), False
        return self.application.backup_import(path, confirm=True), False

    def _recovery(self, tokens: list[str]) -> tuple[ApplicationResult, bool]:
        as_json = False
        positional: list[str] = []
        options: dict[str, str] = {}
        value_options = {"--kind", "--status", "--limit"}
        index = 0
        while index < len(tokens):
            token = tokens[index]
            folded = token.casefold()
            if folded == "--json":
                as_json = True
                index += 1
            elif folded in value_options:
                if index + 1 >= len(tokens):
                    raise ValueError(f"recovery {token} needs a value.")
                options[folded] = tokens[index + 1]
                index += 2
            elif token.startswith("--"):
                raise ValueError(f"Unknown recovery option: {token}")
            else:
                positional.append(token)
                index += 1
        if len(positional) > 1:
            raise ValueError("recovery accepts at most one record id.")
        limit = DEFAULT_RECOVERY_LIMIT
        if "--limit" in options:
            try:
                limit = int(options["--limit"])
            except ValueError:
                raise ValueError("recovery --limit must be an integer.")
        if positional:
            return self.application.recovery_show(positional[0]), as_json
        return self.application.recovery_list(
            kind=options.get("--kind"),
            status=options.get("--status"),
            limit=limit,
        ), as_json

    def _audit(self, tokens: list[str]) -> tuple[ApplicationResult, bool]:
        as_json = False
        for token in tokens:
            if token.casefold() == "--json":
                as_json = True
            else:
                raise ValueError(f"Unknown audit option: {token}")
        return self.application.audit(), as_json

    def _runbooks(self, tokens: list[str]) -> tuple[ApplicationResult, bool]:
        as_json = False
        args: list[str] = []
        for token in tokens:
            if token.casefold() == "--json":
                as_json = True
            elif token.startswith("--"):
                raise ValueError(f"Unknown runbooks option: {token}")
            else:
                args.append(token)
        if not args:
            return self.application.runbooks_list(), as_json
        if args[0].casefold() == "diagnose" and len(args) == 1:
            return self.application.runbooks_diagnose(), as_json
        if args[0].casefold() == "for":
            query = " ".join(args[1:]).strip()
            if not query:
                raise ValueError("runbooks for needs a symptom description.")
            return self.application.runbooks_match(query), as_json
        if len(args) == 1:
            return self.application.runbooks_show(args[0]), as_json
        raise ValueError("Usage: runbooks [diagnose | for <symptom> | <id>]")

    def _retry(self, tokens: list[str]) -> tuple[ApplicationResult, bool]:
        from orion.application.observability.retry import (
            DEFAULT_MAX_ATTEMPTS,
            MAX_ATTEMPTS_LIMIT,
        )

        as_json = False
        max_attempts = DEFAULT_MAX_ATTEMPTS
        args: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.casefold() == "--json":
                as_json = True
            elif token.casefold() == "--attempts":
                if index + 1 >= len(tokens):
                    raise ValueError("retry --attempts needs a number.")
                try:
                    max_attempts = int(tokens[index + 1])
                except ValueError:
                    raise ValueError(
                        f"retry --attempts needs a number, got: {tokens[index + 1]}"
                    ) from None
                if max_attempts < 1 or max_attempts > MAX_ATTEMPTS_LIMIT:
                    raise ValueError(
                        f"retry --attempts must be between 1 and {MAX_ATTEMPTS_LIMIT}."
                    )
                index += 1
            elif token.startswith("--"):
                raise ValueError(f"Unknown retry option: {token}")
            else:
                args.append(token)
            index += 1
        if not args:
            raise ValueError(
                "Usage: retry provider <name> | retry health [section] | retry backup <path>"
            )
        operation = args[0].casefold()
        if operation == "provider":
            if len(args) != 2:
                raise ValueError("Usage: retry provider <name>")
            return (
                self.application.retry_provider(args[1], max_attempts=max_attempts),
                as_json,
            )
        if operation == "health":
            if len(args) > 2:
                raise ValueError("Usage: retry health [section]")
            section = args[1] if len(args) == 2 else None
            return (
                self.application.retry_health(section, max_attempts=max_attempts),
                as_json,
            )
        if operation == "backup":
            if len(args) != 2:
                raise ValueError("Usage: retry backup <path>")
            return (
                self.application.retry_backup(args[1], max_attempts=max_attempts),
                as_json,
            )
        raise ValueError(
            "Usage: retry provider <name> | retry health [section] | retry backup <path>"
        )

    def _remediate(self, tokens: list[str]) -> tuple[ApplicationResult, bool]:
        as_json = False
        apply_all = False
        args: list[str] = []
        for token in tokens:
            if token.casefold() == "--json":
                as_json = True
            elif token.casefold() == "--all":
                apply_all = True
            elif token.startswith("--"):
                raise ValueError(f"Unknown remediate option: {token}")
            else:
                args.append(token)
        if not args:
            if apply_all:
                raise ValueError("Usage: remediate [apply <id> | apply --all] [--json]")
            return self.application.remediate_plan(), as_json
        if args[0].casefold() != "apply":
            raise ValueError("Usage: remediate [apply <id> | apply --all] [--json]")
        if apply_all and len(args) == 1:
            return self._remediate_apply_all(), False
        if not apply_all and len(args) == 2:
            return self._remediate_apply_one(args[1]), False
        raise ValueError("Usage: remediate [apply <id> | apply --all] [--json]")

    def _remediate_apply_one(self, proposal_id: str) -> ApplicationResult:
        plan = self.application.remediate_plan()
        if not plan.ok:
            return plan
        self._render(plan)
        self._output(
            "Sensitive action: apply the remediation above "
            f"({proposal_id}). Some fixes cannot be undone."
        )
        answer = (self._input("Approve? [y/N]: ") or "").strip().lower()
        if answer not in {"y", "yes"}:
            return ApplicationResult.failure(
                "Remediation cancelled.",
                data={"read_only": True},
                errors=("Remediation cancelled.",),
            )
        return self.application.remediate_apply(proposal_id, confirm=True)

    def _remediate_apply_all(self) -> ApplicationResult:
        plan = self.application.remediate_plan()
        if not plan.ok:
            return plan
        self._render(plan)
        self._output(
            "Sensitive action: apply ALL remediations above. "
            "Some fixes cannot be undone."
        )
        answer = (self._input("Approve? [y/N]: ") or "").strip().lower()
        if answer not in {"y", "yes"}:
            return ApplicationResult.failure(
                "Remediation cancelled.",
                data={"read_only": True},
                errors=("Remediation cancelled.",),
            )
        return self.application.remediate_apply_all(confirm=True)

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

    def _docker(self, tokens: list[str]) -> tuple[ApplicationResult, bool]:
        as_json = False
        tail = 50
        tail_seen = False
        args: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.casefold() == "--json":
                as_json = True
                index += 1
            elif token.casefold() == "--tail":
                if index + 1 >= len(tokens):
                    raise ValueError("docker logs --tail needs a value.")
                try:
                    tail = int(tokens[index + 1])
                except ValueError:
                    raise ValueError(
                        "docker logs --tail must be an integer."
                    ) from None
                tail_seen = True
                index += 2
            elif token.startswith("--"):
                raise ValueError(f"Unknown docker option: {token}")
            else:
                args.append(token)
                index += 1
        if not args:
            raise ValueError(
                "Usage: docker list | docker inspect <name> | "
                "docker start|stop|restart <name> | docker health | "
                "docker logs <name> [--tail N]"
            )
        subcommand = args[0].casefold()
        if subcommand in {"list", "ps"}:
            if len(args) != 1:
                raise ValueError("docker list takes no arguments.")
            if tail_seen:
                raise ValueError("docker list takes no --tail option.")
            return self.application.docker_list(), as_json
        if subcommand == "inspect":
            if len(args) != 2:
                raise ValueError("Usage: docker inspect <name>")
            if tail_seen:
                raise ValueError("docker inspect takes no --tail option.")
            return self.application.docker_inspect(args[1]), as_json
        if subcommand in {"start", "stop", "restart"}:
            if len(args) != 2:
                raise ValueError(f"Usage: docker {subcommand} <name>")
            if tail_seen:
                raise ValueError(f"docker {subcommand} takes no --tail option.")
            return self._docker_control(subcommand, args[1])
        if subcommand == "health":
            if len(args) != 1:
                raise ValueError("docker health takes no arguments.")
            if tail_seen:
                raise ValueError("docker health takes no --tail option.")
            return self.application.docker_health(), as_json
        if subcommand == "logs":
            if len(args) != 2:
                raise ValueError("Usage: docker logs <name> [--tail N]")
            return self.application.docker_logs(args[1], tail=tail), as_json
        raise ValueError(f"Unknown docker subcommand: {args[0]}")

    def _docker_control(self, action: str, name: str) -> tuple[ApplicationResult, bool]:
        detail = self.application.docker_inspect(name)
        if not detail.ok:
            return detail, False
        self._render(detail)
        self._output(
            f"Sensitive action: docker {action} '{detail.data['name']}' "
            f"(currently {detail.data['state']})."
        )
        answer = (self._input("Approve? [y/N]: ") or "").strip().lower()
        if answer not in {"y", "yes"}:
            return ApplicationResult.failure(
                f"Cancelled: did not {action} '{detail.data['name']}'.",
                data={"read_only": True},
                errors=("Cancelled.",),
            ), False
        return self.application.docker_control(action, name, confirm=True), False

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
            "       backup export [--name NAME] [--json]\n"
            "       backup list [--json]\n"
            "       backup verify PATH [--json]\n"
            "       backup import PATH\n"
            "       recovery [ID] [--kind K] [--status S] [--limit N] [--json]\n"
            "       audit [--json]\n"
            "       runbooks [diagnose | for <symptom> | <id>] [--json]\n"
            "       retry provider <name> [--attempts N] [--json]\n"
            "       retry health [section] [--attempts N] [--json]\n"
            "       retry backup <path> [--attempts N] [--json]\n"
            "       remediate [--json]\n"
            "       remediate apply <id> | apply --all\n"
            "       docker list [--json]\n"
            "       docker inspect <name> [--json]\n"
            "       docker start|stop|restart <name>\n"
            "       docker health [--json]\n"
            "       docker logs <name> [--tail N] [--json]\n"
            "Health checks, log searches, diagnostics, and graphs are read-only\n"
            "except `health --record`, which saves one snapshot for the graphs.\n"
            "Backup import replaces data and always asks for approval first."
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
