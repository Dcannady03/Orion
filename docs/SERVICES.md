# Orion Service Registry

Orion's `ServiceRegistry` is the canonical source for shared runtime services.
It prevents global state and lets future skills discover dependencies through a
stable interface.

## Registered services in v0.2.0

- `workspace` → `WorkspaceManager`
- `session_memory` → `SessionMemory`
- `code` → `CodeSkill`

## Usage

```python
workspace = orion.services.get("workspace")
memory = orion.services.get("session_memory")
code = orion.services.get("code")
```

Existing attributes remain available for compatibility:

```python
orion.workspace_manager
orion.session_memory
orion.code_skill
```
