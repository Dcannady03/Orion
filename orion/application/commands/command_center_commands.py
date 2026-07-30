"""Application handler for Orion Command Center operations."""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Callable

from orion.application.results import ApplicationResult
from orion.application.team_reconciliation import synchronize_command_center_team
from orion.command_center.models import JobTeamIntegration


class CommandCenterApplicationHandler:
    """Parse requests, coordinate services, and return structured results."""

    def __init__(
        self,
        service,
        *,
        integration=None,
        input_provider: Callable[[str], str] | None = None,
    ) -> None:
        self.service = service
        self.integration = integration or getattr(service, "team_integration", None)
        self.input = input_provider or input
        self._lines: list[str] = []
        self._payload = None
        self._warnings: list[str] = []
        self._errors: list[str] = []
        self._next_actions: list[str] = []
        self._command = ""

    def handle(self, payload: str) -> ApplicationResult:
        self._lines = []
        self._payload = None
        self._warnings = []
        self._errors = []
        self._next_actions = []
        try:
            tokens = shlex.split(payload, posix=True)
        except ValueError as exc:
            message = f"Command Center command could not be read: {exc}"
            return ApplicationResult.failure(message, errors=(str(exc),))
        command = tokens[0].lower() if tokens else "status"
        self._command = command
        args = tokens[1:]
        try:
            if command == "status":
                self.status(args)
            elif command == "snapshot":
                self.snapshot(args)
            elif command == "departments":
                self.departments(args)
            elif command == "department":
                self.department(args)
            elif command == "templates":
                self.templates(args)
            elif command == "template":
                self.template(args)
            elif command == "jobs":
                self.jobs(args)
            elif command == "job":
                self.job(args)
            elif command == "activity":
                self.activity(args)
            elif command == "doctor":
                self.doctor(args)
            else:
                self.usage()
        except (
            FileExistsError,
            FileNotFoundError,
            NotADirectoryError,
            OSError,
            PermissionError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            message = f"Command Center command failed: {exc}"
            return ApplicationResult.failure(
                message,
                data={"command": command},
                errors=(str(exc),),
            )
        data = {"command": command}
        if self._payload is not None:
            data["result"] = self._serialize(self._payload)
        message = "\n".join(self._lines)
        if self._errors:
            return ApplicationResult.failure(
                message,
                data=data,
                errors=tuple(dict.fromkeys(self._errors)),
                warnings=tuple(dict.fromkeys(self._warnings)),
                next_actions=tuple(dict.fromkeys(self._next_actions)),
            )
        return ApplicationResult.success(
            message,
            data=data,
            warnings=tuple(dict.fromkeys(self._warnings)),
            next_actions=tuple(dict.fromkeys(self._next_actions)),
        )

    def output(self, value: object = "") -> None:
        self._lines.append(str(value))

    def _set_payload(self, value) -> None:
        self._payload = value

    @classmethod
    def _serialize(cls, value):
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return cls._serialize(value.to_dict())
        if isinstance(value, dict):
            return {str(key): cls._serialize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._serialize(item) for item in value]
        return value

    def status(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if positional or set(options) - {"json"}:
            raise ValueError("Usage: command-center status [--json]")
        snapshot = self.service.snapshot()
        self._set_payload(snapshot)
        if "json" in options:
            self._json(snapshot)
            return
        self.output("Orion Command Center")
        self.output("-" * 60)
        organization = snapshot["organization"]
        self.output(
            f"Organization : {organization['name'] if organization else 'Unavailable'}"
        )
        self.output(f"Departments  : {len(snapshot['departments'])}")
        counts = snapshot["agent_counts"]
        self.output(
            f"Agents       : {counts['enabled']} enabled / {counts['total']} total"
        )
        self.output(
            f"Jobs         : {len(snapshot['active_jobs'])} active / "
            f"{len(snapshot['queued_jobs'])} queued"
        )
        self.output(
            f"Approvals    : {len(snapshot['jobs_awaiting_approval'])} awaiting approval"
        )
        self.output(
            f"Reviews      : {len(snapshot['jobs_awaiting_review'])} awaiting review"
        )
        workflow = snapshot.get("workflow_summary", {})
        self.output(
            "Workflow     : "
            f"{workflow.get('planning', 0)} planning / "
            f"{workflow.get('implementing', 0)} implementing / "
            f"{workflow.get('testing', 0)} testing"
        )
        workspace = snapshot["workspace"]
        self.output(
            f"Workspace    : {workspace['name'] if workspace['available'] else 'Unavailable'}"
        )
        self.output(f"AI Health    : {self._health_text(snapshot['provider_health'])}")
        self.output("-" * 60)
        self.output("")
        self.output("Departments")
        if not snapshot["departments"]:
            self.output("  No departments configured. View starters with: cc templates")
        for department in snapshot["departments"]:
            workload = (
                f"{department['active_job_count']} active job"
                + ("s" if department["active_job_count"] != 1 else "")
                if department["active_job_count"]
                else f"{department['queued_job_count']} queued job"
                + ("s" if department["queued_job_count"] != 1 else "")
                if department["queued_job_count"]
                else "idle"
            )
            self.output(
                f"  {department['name']:<18} "
                f"{department['agent_count']:>3} agents   {workload}"
            )
        self.output("")
        self.output("Recent Activity")
        activity = list(reversed(snapshot["recent_activity"][-5:]))
        if not activity:
            self.output("  No activity recorded.")
        for event in activity:
            stamp = self._time(event["timestamp"])
            self.output(f"  {stamp}  {event['message']}")
        if snapshot["warnings"]:
            self.output("")
            self.output(f"Warnings: {len(snapshot['warnings'])} (run: cc doctor)")

    def snapshot(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if positional or set(options) - {"json"}:
            raise ValueError("Usage: command-center snapshot [--json]")
        snapshot = self.service.snapshot()
        self._set_payload(snapshot)
        self._json(snapshot)

    def departments(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if options or positional:
            raise ValueError("Usage: command-center departments")
        departments = self.service.departments()
        self._set_payload(departments)
        self.output("Command Center Departments")
        self.output("-" * 72)
        if not departments:
            self.output("No departments configured.")
            return
        for item in departments:
            state = "enabled" if item.enabled else "disabled"
            self.output(
                f"  {item.department_id:<20} {item.name:<24} "
                f"{len(item.agent_ids):>3} agents  {state}"
            )

    def department(self, args: list[str]) -> None:
        if not args:
            raise ValueError(
                "Usage: command-center department "
                "<show|create|add-agent|remove-agent> ..."
            )
        action = args[0].lower()
        remaining = args[1:]
        if action == "show":
            options, positional = self._options(remaining)
            if options or len(positional) != 1:
                raise ValueError(
                    "Usage: command-center department show <name-or-id>"
                )
            self._show_department(self.service.department(positional[0]))
        elif action == "create":
            self._create_department(remaining)
        elif action in {"add-agent", "remove-agent"}:
            options, positional = self._options(remaining)
            if options or len(positional) != 2:
                raise ValueError(
                    f"Usage: command-center department {action} "
                    "<department> <agent>"
                )
            if action == "add-agent":
                department = self.service.add_agent(*positional)
                self._set_payload(department)
                self.output(
                    f"[OK] Added {positional[1]} to {department.name}."
                )
            else:
                department = self.service.remove_agent(*positional)
                self._set_payload(department)
                self.output(
                    f"[OK] Removed {positional[1]} from {department.name}; "
                    "the agent was not deleted."
                )
        else:
            raise ValueError(f"Unknown department command: {action}")

    def _create_department(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if positional:
            raise ValueError(
                "Use --name or --template for noninteractive department creation."
            )
        if not options:
            self.output("Create Command Center Department")
            self.output("-" * 60)
            template = self.input(
                "Template (Engineering/Marketing/Business/Automation, optional): "
            ).strip()
            name = self.input("Department name (blank uses template): ").strip()
            description = self.input("Description (optional): ").strip()
            options = {
                "template": template,
                "name": name,
                "description": description,
            }
        allowed = {"name", "id", "description", "icon", "workflow", "template"}
        unknown = set(options) - allowed
        if unknown:
            raise ValueError(f"Unsupported department options: {sorted(unknown)}")
        department = self.service.create_department(
            name=options.get("name") or None,
            department_id=options.get("id") or None,
            description=options.get("description", ""),
            icon=options.get("icon", ""),
            workflow_policy_reference=options.get("workflow", ""),
            template=options.get("template") or None,
        )
        self._set_payload(department)
        self.output(f'[OK] Department "{department.name}" created.')

    def _show_department(self, department) -> None:
        self._set_payload(department)
        self.output(f"Department: {department.name}")
        self.output("-" * 72)
        self.output(f"ID: {department.department_id}")
        self.output(f"Status: {'Enabled' if department.enabled else 'Disabled'}")
        self.output(f"Description: {department.description or 'Not set'}")
        self.output(f"Icon: {department.icon or 'Not set'}")
        self.output(
            f"Workflow policy: {department.workflow_policy_reference or 'Not set'}"
        )
        self.output(
            "Agents: "
            + (", ".join(department.agent_ids) if department.agent_ids else "none")
        )

    def templates(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if options or positional:
            raise ValueError("Usage: command-center templates")
        templates = self.service.templates()
        self._set_payload(templates)
        self.output("Command Center Department Templates")
        self.output("-" * 72)
        for template in templates:
            self.output(
                f"  {template.template_id:<16} {template.name:<18} "
                f"{len(template.recommended_roles):>2} recommended roles"
            )
        self.output("Templates never create agents automatically.")

    def template(self, args: list[str]) -> None:
        if not args or args[0].lower() != "show":
            raise ValueError("Usage: command-center template show <template>")
        options, positional = self._options(args[1:])
        if options or len(positional) != 1:
            raise ValueError("Usage: command-center template show <template>")
        template = self.service.template(positional[0])
        self._set_payload(template)
        self.output(f"Department Template: {template.name}")
        self.output("-" * 72)
        self.output(template.description)
        self.output("Recommended roles:")
        for role in template.recommended_roles:
            self.output(f"  - {role}")
        self.output("No agents are created until you explicitly create them.")

    def jobs(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if positional or set(options) - {"status", "json"}:
            raise ValueError(
                "Usage: command-center jobs [--status <status>] [--json]"
            )
        jobs = self.service.jobs()
        if "status" in options:
            requested = options["status"].strip().lower()
            jobs = tuple(item for item in jobs if item.status.value == requested)
        views = [self._job_view(item) for item in jobs]
        self._set_payload(views)
        if "json" in options:
            self._json(views)
            return
        self.output("Command Center Jobs")
        self.output("-" * 118)
        if not jobs:
            self.output("No matching jobs.")
            return
        self.output(
            f"  {'ID':<38} {'Status':<18} {'Stage':<20} "
            f"{'Progress':>8}  Department"
        )
        for job in jobs:
            self.output(
                f"  {job.job_id:<38} {job.status.value:<18} "
                f"{(job.current_stage or 'not_started'):<20} "
                f"{job.progress:>7}%  {job.department_id or 'unassigned'}"
            )

    def job(self, args: list[str]) -> None:
        if not args:
            raise ValueError(
                "Usage: command-center job "
                "<create|launch|sync|show|assign|status|progress|cancel> ..."
            )
        action = args[0].lower()
        remaining = args[1:]
        if action == "create":
            self._create_job(remaining)
        elif action == "launch":
            self._launch_job(remaining)
        elif action == "sync":
            self._sync_job(remaining)
        elif action == "show":
            options, positional = self._options(remaining)
            if set(options) - {"json"} or len(positional) != 1:
                raise ValueError(
                    "Usage: command-center job show <job-id> [--json]"
                )
            job = self.service.job(positional[0])
            view = self._job_view(job)
            self._set_payload(view)
            self._warnings.extend(view["warnings"])
            self._next_actions.append(view["next_action"])
            if "json" in options:
                self._json(view)
            else:
                self._show_job(job)
        elif action == "assign":
            options, positional = self._options(remaining)
            if options or len(positional) != 2:
                raise ValueError(
                    "Usage: command-center job assign <job-id> <agent>"
                )
            job = self.service.assign_job(*positional)
            self._set_payload(job)
            self.output(f"[OK] Agent assigned to {job.job_id}.")
        elif action == "status":
            options, positional = self._options(remaining)
            if set(options) - {"stage"} or len(positional) != 2:
                raise ValueError(
                    "Usage: command-center job status <job-id> <status> "
                    "[--stage <name>]"
                )
            job = self.service.update_job_status(
                positional[0],
                positional[1],
                current_stage=options.get("stage"),
            )
            self._set_payload(job)
            self.output(f"[OK] Job {job.job_id} is {job.status.value}.")
        elif action == "progress":
            options, positional = self._options(remaining)
            if set(options) - {"stage"} or len(positional) != 2:
                raise ValueError(
                    "Usage: command-center job progress <job-id> <0-100> "
                    "[--stage <name>]"
                )
            try:
                progress = int(positional[1])
            except ValueError as exc:
                raise ValueError("Job progress must be an integer from 0 through 100.") from exc
            job = self.service.update_job_progress(
                positional[0],
                progress,
                current_stage=options.get("stage"),
            )
            self._set_payload(job)
            self.output(f"[OK] Job {job.job_id} progress is {job.progress}%.")
        elif action == "cancel":
            options, positional = self._options(remaining)
            if options or len(positional) != 1:
                raise ValueError("Usage: command-center job cancel <job-id>")
            job = self.service.cancel_job(positional[0])
            self._set_payload(job)
            self.output(f"[OK] Job {job.job_id} was cancelled.")
        else:
            raise ValueError(f"Unknown job command: {action}")

    def _launch_job(self, args: list[str]) -> None:
        if self.integration is None:
            raise ValueError("Command Center AI Team integration is unavailable.")
        options, positional = self._options(args)
        if (
            set(options) - {"workflow", "workspace", "dry-run", "json"}
            or len(positional) != 1
        ):
            raise ValueError(
                "Usage: command-center job launch <job-id> "
                "[--workflow <name>] [--workspace <path>] [--dry-run] [--json]"
            )
        job_id = positional[0]
        workflow = options.get("workflow", "")
        workspace = options.get("workspace", "")
        if "dry-run" in options:
            preview = self.integration.preview_launch(
                job_id,
                workflow=workflow,
                workspace=workspace,
            )
            self._set_payload(preview)
            self._warnings.extend(preview.warnings)
            self._errors.extend(preview.errors)
            if "json" in options:
                self._json(preview.to_dict())
                return
            self._show_launch_preview(preview)
            return
        result = self.integration.launch(
            job_id,
            workflow=workflow,
            workspace=workspace,
        )
        self._set_payload(result)
        self._warnings.extend(result.warnings)
        next_action = self.integration.describe_next_action(job_id)
        self._next_actions.append(next_action)
        if "json" in options:
            self._json(result.to_dict())
            return
        self.output(f"[OK] Job {result.job.job_id} launched through AI Team.")
        self.output(f"Team Task     : {result.team_task_id}")
        self.output(f"Status        : {result.job.status.value}")
        self.output(f"Stage         : {result.job.current_stage}")
        self.output(f"Next Action   : {next_action}")
        for warning in result.warnings:
            self.output(f"Warning       : {warning}")

    def _sync_job(self, args: list[str]) -> None:
        if self.integration is None:
            raise ValueError("Command Center AI Team integration is unavailable.")
        options, positional = self._options(args)
        if set(options) - {"json"} or len(positional) != 1:
            raise ValueError(
                "Usage: command-center job sync <job-id> [--json]"
            )
        result = self.integration.sync_job(positional[0])
        self._set_payload(result)
        self._warnings.extend(result.warnings)
        if "json" in options:
            self._json(result.to_dict())
            return
        state = "updated" if result.changed else "already current"
        self.output(f"[OK] Job {result.job.job_id} is {state}.")
        self.output(
            f"Status: {result.job.status.value}  "
            f"Stage: {result.job.current_stage}  "
            f"Progress: {result.job.progress}%"
        )
        for warning in result.warnings:
            self.output(f"[!] {warning}")

    def _show_launch_preview(self, preview) -> None:
        self._set_payload(preview)
        self.output("Command Center Launch Preview")
        self.output("-" * 72)
        self.output(f"Allowed        : {'Yes' if preview.allowed else 'No'}")
        self.output(
            f"Workflow       : "
            f"{preview.workflow.workflow_id if preview.workflow else 'Unresolved'}"
        )
        self.output(f"Department     : {preview.department_name or 'Unassigned'}")
        self.output(
            f"Workspace      : "
            f"{Path(preview.workspace_root).name if preview.workspace_root else 'Unresolved'}"
        )
        self.output(f"Team Task Type : {preview.intended_team_task_type}")
        self.output(
            f"Approval       : "
            f"{'Required before implementation' if preview.approval_required else 'Not required'}"
        )
        self.output(f"Execution      : {preview.execution_engine}")
        if preview.workflow is not None:
            self.output("Resolved Agents:")
            for assignment in preview.workflow.role_assignments:
                self.output(
                    f"  {assignment.stage.value:<20} {assignment.agent_id}"
                )
        self.output("Provider Routes:")
        for route in preview.provider_routes:
            self.output(
                f"  {route.agent_id:<24} {route.provider}:{route.model}"
            )
        for warning in preview.warnings:
            self.output(f"[!] {warning}")
        for error in preview.errors:
            self.output(f"[X] {error}")
        self.output("Dry run made no changes and called no provider or executor.")

    def _create_job(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if positional:
            raise ValueError("Use --title and --goal for noninteractive job creation.")
        if not options:
            self.output("Create Command Center Job")
            self.output("-" * 60)
            options = {
                "title": self.input("Title: ").strip(),
                "goal": self.input("Goal: ").strip(),
                "department": self.input("Department (optional): ").strip(),
                "workspace": self.input("Workspace (optional): ").strip(),
                "priority": self.input(
                    "Priority [low/normal/high/urgent] (normal): "
                ).strip() or "normal",
                "agents": self.input(
                    "Assigned agents, comma-separated (optional): "
                ).strip(),
            }
        allowed = {
            "title", "goal", "department", "workspace", "priority", "agents",
            "created-by", "id",
        }
        unknown = set(options) - allowed
        if unknown:
            raise ValueError(f"Unsupported job options: {sorted(unknown)}")
        if not options.get("title", "").strip() or not options.get("goal", "").strip():
            raise ValueError("Job creation requires --title and --goal.")
        job = self.service.create_job(
            title=options["title"],
            goal=options["goal"],
            priority=options.get("priority", "normal"),
            department=options.get("department", ""),
            assigned_agents=self._csv(options.get("agents", "")),
            workspace_reference=options.get("workspace", ""),
            created_by=options.get("created-by", "user"),
            job_id=options.get("id") or None,
        )
        self._set_payload(job)
        self._next_actions.append("Launch the job when ready.")
        self.output(f'[OK] Job "{job.title}" created as {job.job_id}.')
        self.output("Creation did not start planning or execution.")

    def _show_job(self, job) -> None:
        view = self._job_view(job)
        self._set_payload(view)
        self.output("Command Center Job")
        self.output("-" * 72)
        rows = (
            ("ID", view["id"]),
            ("Title", view["title"]),
            ("Department", view["department"]),
            ("Status", view["status"]),
            ("Stage", view["stage"]),
            ("Progress", f"{view['progress']}%"),
            ("Approval", view["approval_state"]),
            ("Approval ID", view["approval_id"] or "Not recorded"),
            ("Workspace", view["workspace"]),
            ("Workflow", view["workflow"] or "Not linked"),
            ("Team Task", view["team_task_id"] or "Not linked"),
            ("Team Run", view["team_run_id"] or "Not started"),
            ("Active Agent", view["active_agent_id"] or "None"),
            ("Created", view["created_at"]),
            ("Last Updated", view["updated_at"]),
            ("Next Action", view["next_action"]),
        )
        for label, value in rows:
            self.output(f"{label:<15} : {value}")
        self.output("")
        self.output("Assignments")
        if view["assignments"]:
            for assignment in view["assignments"]:
                self.output(
                    f"  {assignment['stage']:<20} {assignment['agent_id']}"
                )
        else:
            agents = job.assigned_agent_ids
            self.output(
                "  " + (", ".join(agents) if agents else "Unassigned")
            )
        if job.result_summary:
            self.output(f"\nResult: {job.result_summary}")
        if job.error_summary:
            self.output(f"Error: {job.error_summary}")
        self.output("")
        self.output("Recent Activity")
        if view["recent_activity"]:
            for event in view["recent_activity"]:
                self.output(
                    f"  {self._time(event['timestamp'])}  {event['message']}"
                )
        else:
            self.output("  No job activity recorded.")
        for warning in view["warnings"]:
            self.output(f"[!] {warning}")

    def activity(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if positional or set(options) - {"limit"}:
            raise ValueError("Usage: command-center activity [--limit <1-1000>]")
        try:
            limit = int(options.get("limit", "20"))
        except ValueError as exc:
            raise ValueError("Activity limit must be an integer.") from exc
        events = self.service.activity(limit)
        self._set_payload(events)
        self.output("Command Center Activity")
        self.output("-" * 90)
        if not events:
            self.output("No activity recorded.")
            return
        for event in reversed(events):
            self.output(
                f"  {self._time(event.timestamp)}  "
                f"{event.severity.value:<7} {event.message}"
            )

    def doctor(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if positional or set(options) - {"json"}:
            raise ValueError("Usage: command-center doctor [--json]")
        report = self.service.doctor()
        self._set_payload(report)
        if "json" in options:
            self._json(report)
            return
        self.output("Orion Command Center Doctor")
        self.output("-" * 72)
        if not report["issues"]:
            self.output("[OK] Storage, schemas, references, and workspaces are healthy.")
        for issue in report["issues"]:
            marker = "[X]" if issue["severity"] == "error" else "[!]"
            record = f" ({issue['record']})" if issue["record"] else ""
            self.output(
                f"{marker} {issue['code']}{record}: {issue['message']}"
            )
        self.output("-" * 72)
        self.output(
            f"Errors: {report['error_count']}  "
            f"Warnings: {report['warning_count']}  "
            f"Result: {'healthy' if report['ok'] else 'attention required'}"
        )
        self.output("Doctor is read-only and made no repairs.")

    def usage(self) -> None:
        self.output(
            "Command Center commands: status, snapshot, departments, department, "
            "templates, template, jobs, job create|launch|sync|show, "
            "activity, doctor"
        )

    @staticmethod
    def _options(args: list[str]) -> tuple[dict[str, str], list[str]]:
        options: dict[str, str] = {}
        positional: list[str] = []
        flags = {"json", "dry-run"}
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
            if key in flags:
                options[key] = "true"
                index += 1
                continue
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                raise ValueError(f"Option --{key} requires a value.")
            options[key] = args[index + 1]
            index += 2
        return options, positional

    @staticmethod
    def _csv(value: str) -> tuple[str, ...]:
        return tuple(item.strip() for item in str(value).split(",") if item.strip())

    def _json(self, value) -> None:
        self._set_payload(value)
        self.output(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))

    def _job_view(self, job) -> dict:
        link = None
        warnings = []
        assignments = []
        try:
            integration = JobTeamIntegration.from_job(job)
            link = integration.active_link
            if link is None and integration.links:
                link = integration.links[-1]
        except ValueError as exc:
            warnings.append(str(exc)[:500])
        if link is not None:
            warnings.extend(link.synchronization_warnings)
            assignments = [
                item.to_dict() for item in link.role_assignments
            ]
        try:
            events = [
                event.to_dict()
                for event in self.service.activity(1_000)
                if event.job_id == job.job_id
            ][-5:]
        except (OSError, PermissionError, ValueError):
            events = []
            warnings.append("Recent job activity is unavailable.")
        workspace_name = (
            Path(job.workspace_reference).name if job.workspace_reference else ""
        )
        return {
            "id": job.job_id,
            "title": job.title,
            "department": job.department_id or "Unassigned",
            "status": job.status.value,
            "stage": job.current_stage or "not_started",
            "progress": job.progress,
            "priority": job.priority.value,
            "approval_state": job.approval_state.value,
            "approval_id": link.approval_id if link else "",
            "workspace": workspace_name or "Not set",
            "workflow": link.workflow_id if link else "",
            "team_task_id": link.team_task_id if link else "",
            "team_run_id": link.team_run_id if link else "",
            "active_agent_id": link.active_agent_id if link else "",
            "external_status": link.external_status if link else "",
            "execution_engine": link.execution_engine if link else "",
            "next_action": (
                link.next_action
                if link is not None
                else "Launch the job when ready."
            ),
            "assignments": assignments,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "completed_at": job.completed_at,
            "result_summary": job.result_summary,
            "error_summary": job.error_summary,
            "warnings": list(dict.fromkeys(warnings)),
            "recent_activity": events,
        }

    @staticmethod
    def _time(timestamp: str) -> str:
        try:
            return timestamp[11:16]
        except (IndexError, TypeError):
            return "--:--"

    @staticmethod
    def _health_text(health: dict) -> str:
        if not health.get("available"):
            return "Unavailable"
        return ", ".join(
            f"{item['id'].title()} {item['status']}"
            for item in health["providers"]
        ) or "No providers configured"
