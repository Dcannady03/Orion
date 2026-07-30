# Sanitized test fixtures

Only deterministic, non-secret development samples belong here. Tests must create
mutable runtime state in temporary directories and must never depend on the
repository-local `.orion/` directory or a developer's `~/.orion` data.
