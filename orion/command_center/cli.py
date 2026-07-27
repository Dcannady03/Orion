"""Terminal adapter for Orion Command Center."""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Callable


class CommandCenterCommandHandler:
    """Parse Command Center commands without leaking rendering into the domain."""

    def __init__(
        self,
        service,
        *,
        input_provider: Callable[[str], str] | None = None,
        output_provider: Callable[[str], None] | None = None,
    ) -> None:
        self.service = service
        self.input = input_provider or input
        self.output = output_provider or print

    def handle(self, payload: str) -> None:
        try:
            tokens = shlex.split(payload, posix=True)
        except ValueError as exc:
            self.output(f"Command Center command could not be read: {exc}")
            return
        command = tokens[0].lower() if tokens else "status"
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
            TypeError,
            ValueError,
        ) as exc:
            self.output(f"Command Center command failed: {exc}")

    def status(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if positional or set(options) - {"json"}:
            raise ValueError("Usage: command-center status [--json]")
        snapshot = self.service.snapshot()
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
        self._json(self.service.snapshot())

    def departments(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if options or positional:
            raise ValueError("Usage: command-center departments")
        departments = self.service.departments()
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
                self.output(
                    f"[OK] Added {positional[1]} to {department.name}."
                )
            else:
                department = self.service.remove_agent(*positional)
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
        self.output(f'[OK] Department "{department.name}" created.')

    def _show_department(self, department) -> None:
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
        self.output("Command Center Department Templates")
        self.output("-" * 72)
        for template in self.service.templates():
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
        self.output(f"Department Template: {template.name}")
        self.output("-" * 72)
        self.output(template.description)
        self.output("Recommended roles:")
        for role in template.recommended_roles:
            self.output(f"  - {role}")
        self.output("No agents are created until you explicitly create them.")

    def jobs(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if positional or set(options) - {"status"}:
            raise ValueError("Usage: command-center jobs [--status <status>]")
        jobs = self.service.jobs()
        if "status" in options:
            requested = options["status"].strip().lower()
            jobs = tuple(item for item in jobs if item.status.value == requested)
        self.output("Command Center Jobs")
        self.output("-" * 90)
        if not jobs:
            self.output("No matching jobs.")
            return
        for job in jobs:
            self.output(
                f"  {job.job_id:<38} {job.status.value:<18} "
                f"{job.progress:>3}%  {job.title}"
            )

    def job(self, args: list[str]) -> None:
        if not args:
            raise ValueError(
                "Usage: command-center job "
                "<create|show|assign|status|progress|cancel> ..."
            )
        action = args[0].lower()
        remaining = args[1:]
        if action == "create":
            self._create_job(remaining)
        elif action == "show":
            options, positional = self._options(remaining)
            if options or len(positional) != 1:
                raise ValueError("Usage: command-center job show <job-id>")
            self._show_job(self.service.job(positional[0]))
        elif action == "assign":
            options, positional = self._options(remaining)
            if options or len(positional) != 2:
                raise ValueError(
                    "Usage: command-center job assign <job-id> <agent>"
                )
            job = self.service.assign_job(*positional)
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
            self.output(f"[OK] Job {job.job_id} progress is {job.progress}%.")
        elif action == "cancel":
            options, positional = self._options(remaining)
            if options or len(positional) != 1:
                raise ValueError("Usage: command-center job cancel <job-id>")
            job = self.service.cancel_job(positional[0])
            self.output(f"[OK] Job {job.job_id} was cancelled.")
        else:
            raise ValueError(f"Unknown job command: {action}")

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
        self.output(f'[OK] Job "{job.title}" created as {job.job_id}.')
        self.output("Creation did not start planning or execution.")

    def _show_job(self, job) -> None:
        self.output(f"Command Center Job: {job.title}")
        self.output("-" * 72)
        self.output(f"ID: {job.job_id}")
        self.output(f"Status: {job.status.value}")
        self.output(f"Priority: {job.priority.value}")
        self.output(f"Progress: {job.progress}%")
        self.output(f"Stage: {job.current_stage or 'Not started'}")
        self.output(f"Approval: {job.approval_state.value}")
        self.output(f"Department: {job.department_id or 'Unassigned'}")
        self.output(
            "Agents: "
            + (
                ", ".join(job.assigned_agent_ids)
                if job.assigned_agent_ids
                else "Unassigned"
            )
        )
        workspace_name = (
            Path(job.workspace_reference).name if job.workspace_reference else ""
        )
        self.output(f"Workspace: {workspace_name or 'Not set'}")
        self.output(f"Goal: {job.goal}")
        if job.result_summary:
            self.output(f"Result: {job.result_summary}")
        if job.error_summary:
            self.output(f"Error: {job.error_summary}")

    def activity(self, args: list[str]) -> None:
        options, positional = self._options(args)
        if positional or set(options) - {"limit"}:
            raise ValueError("Usage: command-center activity [--limit <1-1000>]")
        try:
            limit = int(options.get("limit", "20"))
        except ValueError as exc:
            raise ValueError("Activity limit must be an integer.") from exc
        events = self.service.activity(limit)
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
            "templates, template, jobs, job, activity, doctor"
        )

    @staticmethod
    def _options(args: list[str]) -> tuple[dict[str, str], list[str]]:
        options: dict[str, str] = {}
        positional: list[str] = []
        flags = {"json"}
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
        self.output(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))

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
