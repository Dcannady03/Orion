# Orion Configuration Reference

Orion layers application defaults from `config/default.yaml` with private user
overrides in `~/.orion/config.yaml`. Normal commands write only the external user file,
so provider choices, workspaces, services, and AI Team assignments survive application
updates. Existing `~/.orion/config/local.yaml` installations are migrated for backward
compatibility.

Do not put API keys, OAuth tokens, or service secrets in either configuration file.
Orion Vault and the external token stores remain the only credential locations.

## AI Team role assignments

```yaml
team:
  assignments:
    architect: active-planning-model
    engineer_reviewer: active-planning-model
    implementation: codex
    tester: codex
    documentation: active-planning-model
```

Model-backed roles accept one of these forms:

```text
active-planning-model
ollama:<available-model>
openai:<available-model>
gemini:<available-model>
```

`active-planning-model` resolves the active provider/model and may use Orion's existing
routing profile when that dynamic choice is unavailable. Explicit provider/model
assignments are validated and do not silently fall back. Provider credentials are read
through Vault and never copied into role configuration or task artifacts.

Execution-backed roles accept an execution engine ID. `codex` is the default and the
currently supported implementation adapter. Orion validates installation, CLI support,
and required adapter capability before saving or running an assignment. There is no
execution fallback; an unavailable Implementation Engine fails closed before approval
is consumed or the workspace changes.

The Tester is also execution-backed, but it runs only after implementation has
completed. An unavailable Tester records `Validation Unavailable` without launching a
check or changing the completed implementation. Automatic validation limits may be
overridden externally:

```yaml
team:
  validation:
    command_timeout_seconds: 120  # each command; allowed range 1–900
    max_output_bytes: 250000      # captured then discarded; 1,000–5,000,000
```

These settings are not permissions. The Tester remains network-disabled, read-only
toward implementation files, unable to launch nested commands or Git, and confined to
an Orion-controlled temporary directory for writes.

Use Orion commands instead of editing YAML directly:

```text
team roles
team role show <role>
team role set <role> <provider:model|engine>
team role reset <role>
```

Legacy `team.roles.*` agent/provider/model settings remain readable so existing
installations keep their behavior. New provider/model and engine choices are stored
under `team.assignments`.

See `AI_TEAM.md` for workflow and artifact details and `AGENT_REGISTRY.md` for the
separate YAML-defined worker configuration.
