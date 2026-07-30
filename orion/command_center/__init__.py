"""Orion Command Center domain and application services."""

from orion.command_center.models import (
    ACTIVE_JOB_STATUSES,
    COMMAND_CENTER_SCHEMA_VERSION,
    COMMAND_CENTER_SNAPSHOT_SCHEMA_VERSION,
    COMMAND_CENTER_TEAM_INTEGRATION_SCHEMA_VERSION,
    DEFAULT_ORGANIZATION_ID,
    DEFAULT_ORGANIZATION_NAME,
    JOB_TRANSITIONS,
    TERMINAL_JOB_STATUSES,
    ActivityEvent,
    ActivitySeverity,
    ActivitySourceType,
    ApprovalState,
    Department,
    Job,
    JobPriority,
    JobStatus,
    JobTeamIntegration,
    Organization,
    TeamIntegrationLink,
    WorkflowAgentAssignment,
    WorkflowStage,
)
from orion.command_center.templates import (
    DepartmentTemplate,
    department_templates,
    get_department_template,
)
from orion.command_center.repository import (
    CommandCenterRepository,
    FileCommandCenterRepository,
    RepositoryDiagnostic,
)
from orion.command_center.service import CommandCenterService
from orion.command_center.integrations import (
    CommandCenterJobUpdateAdapter,
    CommandCenterTeamIntegrationService,
    LaunchPreview,
    LaunchResult,
    LaunchValidationError,
    ProviderRouteSummary,
    ResolvedCommandCenterWorkflow,
    SyncResult,
)
__all__ = [
    "ACTIVE_JOB_STATUSES",
    "COMMAND_CENTER_SCHEMA_VERSION",
    "COMMAND_CENTER_SNAPSHOT_SCHEMA_VERSION",
    "COMMAND_CENTER_TEAM_INTEGRATION_SCHEMA_VERSION",
    "DEFAULT_ORGANIZATION_ID",
    "DEFAULT_ORGANIZATION_NAME",
    "JOB_TRANSITIONS",
    "TERMINAL_JOB_STATUSES",
    "ActivityEvent",
    "ActivitySeverity",
    "ActivitySourceType",
    "ApprovalState",
    "Department",
    "DepartmentTemplate",
    "CommandCenterRepository",
    "FileCommandCenterRepository",
    "RepositoryDiagnostic",
    "CommandCenterService",
    "CommandCenterJobUpdateAdapter",
    "CommandCenterTeamIntegrationService",
    "CommandCenterCommandHandler",
    "Job",
    "JobPriority",
    "JobStatus",
    "JobTeamIntegration",
    "LaunchPreview",
    "LaunchResult",
    "LaunchValidationError",
    "Organization",
    "ProviderRouteSummary",
    "ResolvedCommandCenterWorkflow",
    "SyncResult",
    "TeamIntegrationLink",
    "WorkflowAgentAssignment",
    "WorkflowStage",
    "department_templates",
    "get_department_template",
]


def __getattr__(name: str):
    """Load the CLI adapter lazily so domain imports stay interface-neutral."""
    if name == "CommandCenterCommandHandler":
        from orion.command_center.cli import CommandCenterCommandHandler

        return CommandCenterCommandHandler
    raise AttributeError(name)
