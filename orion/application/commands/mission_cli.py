"""Thin CLI adapter for Mission Engine and human-confirmed coordination."""
from __future__ import annotations

import shlex
from typing import Callable

from orion.application.missions import (
    MissionAdvanceRequest,
    MissionApplicationHandler,
    MissionHistoryRequest,
    MissionListRequest,
    MissionReferenceRequest,
    MissionResolveRequest,
)
from orion.application.results import ApplicationResult
from orion.interfaces.cli.renderer import ApplicationResultRenderer


class MissionCliAdapter:
    """Parse Mission syntax and delegate all behavior to the application handler."""

    def __init__(
        self,
        runtime,
        *,
        output_provider: Callable[[str], None] | None = None,
        input_provider: Callable[[str], str] | None = None,
    ) -> None:
        application = getattr(runtime, "mission_application", None)
        if application is None:
            raise ValueError("Mission application is not available.")
        self.application: MissionApplicationHandler = application
        self.renderer = ApplicationResultRenderer(output_provider)
        self.input = input_provider or input

    def handle(self, payload: str) -> ApplicationResult:
        try:
            tokens = shlex.split(str(payload), posix=True)
        except ValueError as exc:
            return self._render(ApplicationResult.failure(
                f"Mission command could not be read: {exc}",
                errors=(str(exc),),
            ))
        if not tokens:
            return self._render(self.application.list(MissionListRequest()))
        command = tokens[0].lower()
        args = tokens[1:]
        if command == "create":
            if len(args) != 1:
                return self._usage("Usage: mission create <proposal-id>")
            return self._render(self.application.create(args[0]))
        if command == "show":
            return self._reference(command, args, self.application.show)
        if command == "next":
            return self._reference(command, args, self.application.next)
        if command == "advance":
            return self._advance(args)
        if command == "resolve":
            return self._resolve(args)
        if command == "validate":
            return self._reference(command, args, self.application.validate)
        if command == "reconcile":
            return self._reference(command, args, self.application.reconcile)
        if command == "history":
            try:
                options, positional = self._options(args, {"limit"})
                if len(positional) != 1:
                    raise ValueError("Usage: mission history <mission-id> [--limit <n>]")
                limit = int(options.get("limit", "100"))
            except ValueError as exc:
                return self._usage(str(exc))
            return self._render(self.application.history(MissionHistoryRequest(
                positional[0],
                limit=limit,
            )))
        if command == "list":
            try:
                options, positional = self._options(
                    args,
                    {"status", "goal", "proposal", "limit"},
                )
                if positional:
                    raise ValueError(
                        "Usage: mission list [--status <status>] [--goal <goal-id>] "
                        "[--proposal <proposal-id>] [--limit <n>]"
                    )
                limit = int(options.get("limit", "100"))
            except ValueError as exc:
                return self._usage(str(exc))
            return self._render(self.application.list(MissionListRequest(
                status=options.get("status", ""),
                goal_id=options.get("goal", ""),
                proposal_id=options.get("proposal", ""),
                limit=limit,
            )))
        return self._usage(
            "Mission command not recognized. Use: mission create | show | list | "
            "history | validate | reconcile | next | advance | resolve"
        )

    def _advance(self, args: list[str]) -> ApplicationResult:
        try:
            options, positional = self._options(args, {"note"})
            if len(positional) != 1:
                raise ValueError(
                    "Usage: mission advance <mission-id> [--note <text>]"
                )
        except ValueError as exc:
            return self._usage(str(exc))
        mission_id = positional[0]
        note = options.get("note", "")
        preview = self.application.next(MissionReferenceRequest(mission_id))
        self.renderer.render(preview)
        if not preview.ok or preview.data.get("blocked") is True:
            return preview
        decision = ""
        if preview.data.get("capability_id") == "team.final_review":
            decision = self._prompt_decision()
            if decision is None:
                return self._cancelled(mission_id)
            # Re-preview with the decision so the advance token binds it.
            preview = self.application.next(MissionReferenceRequest(
                mission_id,
                decision=decision,
                note=note,
            ))
            self.renderer.render(preview)
            if not preview.ok or preview.data.get("blocked") is True:
                return preview
        token = str(preview.data.get("advance_token", "")).strip()
        if not token:
            return self._render(ApplicationResult.failure(
                "Mission advancement failed: preview did not issue a token.",
                errors=("Mission preview token is unavailable.",),
            ))
        while True:
            if decision:
                detail = f"\nFinal-review decision: {decision.replace('_', ' ')}"
                if note:
                    detail += f" (note: {note})"
                self.renderer.output(detail)
            self.renderer.output(
                "\nAdvance this Mission by exactly one operation? [Y/N/D]:"
            )
            try:
                answer = self.input("> ").strip().lower()
            except KeyboardInterrupt:
                return self._cancelled(mission_id)
            if answer in {"", "n", "no"}:
                return self._cancelled(mission_id)
            if answer in {"d", "details"}:
                self.renderer.render(preview)
                continue
            if answer in {"y", "yes"}:
                return self._render(self.application.advance(MissionAdvanceRequest(
                    mission_id=mission_id,
                    advance_token=token,
                    confirmed=True,
                    actor="user",
                    decision=decision,
                    note=note,
                )))
            self.renderer.output(
                "Please enter Y, N, or D. No Mission operation was dispatched."
            )

    def _prompt_decision(self) -> str | None:
        """Ask for the human final-review decision; None cancels the advance."""
        while True:
            self.renderer.output(
                "\nFinal review: accept the run, request changes, or reject it?"
                " [A/C/R/N]:"
            )
            try:
                answer = self.input("> ").strip().lower()
            except KeyboardInterrupt:
                return None
            if answer in {"", "n", "no"}:
                return None
            if answer in {"a", "accept", "accepted"}:
                return "accepted"
            if answer in {"c", "changes", "changes_requested"}:
                return "changes_requested"
            if answer in {"r", "reject", "rejected"}:
                return "rejected"
            self.renderer.output(
                "Please enter A, C, R, or N. No Mission operation was dispatched."
            )

    def _cancelled(self, mission_id: str) -> ApplicationResult:
        return self._render(ApplicationResult.success(
            "Mission advancement cancelled. No operation was dispatched.",
            data={
                "command": "advance",
                "mission_id": mission_id,
                "confirmed": False,
                "operation_dispatched": False,
            },
            next_actions=(f"mission next {mission_id}",),
        ))

    def _resolve(self, args: list[str]) -> ApplicationResult:
        if len(args) != 1:
            return self._usage("Usage: mission resolve <mission-id>")
        mission_id = args[0]
        preview = self.application.resolve_preview(
            MissionReferenceRequest(mission_id)
        )
        self.renderer.render(preview)
        if not preview.ok or preview.data.get("resolvable") is not True:
            return preview
        finding = ""
        if preview.data.get("audit_blocked") is True:
            finding = self._prompt_finding(
                str(preview.data.get("suggested_finding", ""))
            )
            if finding is None:
                return self._resolve_cancelled(mission_id)
            if finding == "ran":
                consequence = (
                    "This completes the stuck audit as succeeded. "
                    "The operation will NOT be re-dispatched."
                )
            else:
                consequence = (
                    "This clears the stuck audit so the operation can be "
                    "attempted again from a fresh preview and token."
                )
            self.renderer.output(f"\nResolve as {finding.replace('_', ' ')}?")
            self.renderer.output(consequence)
        else:
            self.renderer.output(
                "\nClear the stale coordination lock? No audit is stuck."
            )
        while True:
            self.renderer.output("Confirm the resolution? [Y/N]:")
            try:
                answer = self.input("> ").strip().lower()
            except KeyboardInterrupt:
                return self._resolve_cancelled(mission_id)
            if answer in {"", "n", "no"}:
                return self._resolve_cancelled(mission_id)
            if answer in {"y", "yes"}:
                return self._render(self.application.resolve(
                    MissionResolveRequest(
                        mission_id=mission_id,
                        finding=finding,
                        confirmed=True,
                        actor="user",
                    )
                ))
            self.renderer.output(
                "Please enter Y or N. No Mission state was changed."
            )

    def _prompt_finding(self, suggested: str) -> str | None:
        """Ask for the operator finding; None cancels the resolution."""
        while True:
            hint = ""
            if suggested:
                hint = (
                    f" The downstream evidence suggests "
                    f"{suggested.replace('_', ' ')}."
                )
            self.renderer.output(
                "\nDid the downstream operation run? [R]an / [D]id not run / "
                f"[C]annot determine / [N]o (cancel):{hint}"
            )
            try:
                answer = self.input("> ").strip().lower()
            except KeyboardInterrupt:
                return None
            if answer in {"", "n", "no"}:
                return None
            if answer in {"r", "ran"}:
                return "ran"
            if answer in {"d", "did_not_run", "did not run", "not run"}:
                return "did_not_run"
            if answer in {"c", "cannot_determine", "cannot determine", "unknown"}:
                self.renderer.output(
                    "Cannot determine leaves the block in place. "
                    "No Mission state was changed."
                )
                return None
            self.renderer.output(
                "Please enter R, D, C, or N. No Mission state was changed."
            )

    def _resolve_cancelled(self, mission_id: str) -> ApplicationResult:
        return self._render(ApplicationResult.success(
            "Mission resolution cancelled. No state was changed.",
            data={
                "command": "resolve",
                "mission_id": mission_id,
                "confirmed": False,
                "resolution_applied": False,
                "operation_dispatched": False,
            },
            next_actions=(f"mission show {mission_id}",),
        ))

    def _reference(self, command: str, args: list[str], handler) -> ApplicationResult:
        if len(args) != 1:
            return self._usage(f"Usage: mission {command} <mission-id>")
        return self._render(handler(MissionReferenceRequest(args[0])))

    @staticmethod
    def _options(
        args: list[str],
        allowed: set[str],
    ) -> tuple[dict[str, str], list[str]]:
        options: dict[str, str] = {}
        positional: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if not token.startswith("--"):
                positional.append(token)
                index += 1
                continue
            name = token[2:].lower()
            if name not in allowed:
                raise ValueError(f"Unknown Mission option: {token}")
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                raise ValueError(f"Mission option requires a value: {token}")
            options[name] = args[index + 1]
            index += 2
        return options, positional

    def _usage(self, message: str) -> ApplicationResult:
        return self._render(ApplicationResult.failure(message, errors=(message,)))

    def _render(self, result: ApplicationResult) -> ApplicationResult:
        self.renderer.render(result)
        return result


def dispatch_mission(runtime, raw_command: str) -> bool:
    """Recognize only the Mission command family and delegate it."""
    normalized = str(raw_command).strip().lower()
    if normalized != "mission" and not normalized.startswith("mission "):
        return False
    MissionCliAdapter(runtime).handle(
        str(raw_command).strip()[len("mission"):].strip()
    )
    return True
