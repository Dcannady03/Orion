# Orion Configuration Reference

Orion layers application defaults from `config/default.yaml` with private user
overrides in `~/.orion/config.yaml`. Normal commands write only the external user file,
so provider choices, workspaces, services, and AI Team assignments survive application
updates. Existing `~/.orion/config/local.yaml` installations are migrated for backward
compatibility.

Do not put API keys, OAuth tokens, or service secrets in either configuration file.
Orion Vault and the external token stores remain the only credential locations.

## Image Center

Image-provider selection is independent from `providers.default` and persists in the
external user configuration:

```yaml
image:
  enabled: true
  provider: openai
  history_limit: 100
  history_display_limit: 10
  max_prompt_chars: 4000
  prompt_preview_chars: 300
  max_images_per_request: 1
  max_image_bytes: 20971520
  request_timeout_seconds: 120
  max_concurrent_jobs: 2
  output_format: png
  openai:
    model: gpt-image-2
    size: 1024x1024
    quality: medium
```

Use `image provider use <provider>` instead of editing the selection manually. OpenAI
credentials continue to live only in Orion Vault and are reused by the image adapter;
there is no image-specific plaintext key. Provider status is a local readiness check
and does not make a paid API call. The current adapter supports one image per request.

Discord image generation is a separate opt-in restriction beneath the existing bot:

```yaml
connect:
  discord_bot:
    image_generation:
      enabled: false
      allowed_user_ids: []
      cooldown_seconds: 30
      max_prompt_chars: 1000
      max_upload_bytes: 8388608
      max_concurrent_channels: 2
```

The base Discord approved-user/channel/role/DM/mention policy is always evaluated
first. `image_generation.allowed_user_ids` narrows that access further; an empty list
authorizes no Discord image requests. Cooldowns and channel concurrency are process
local. Artifacts and bounded metadata live under `~/.orion/images/`, never in this
configuration file or the application tree. See `IMAGE_CENTER.md` for the complete
storage, copy-approval, and Discord safety contract.

## AI Team role assignments

Reusable agent definitions are not configuration or Vault entries. Permanent YAML
profiles live under `~/.orion/agents/`; workspace profiles live under
`<workspace>/.orion/agents/`. Provider credentials continue to come from Vault.
Agent `execution.provider`, `execution.model`, and `execution.routing_profile`
preferences are resolved through the services documented below. Agent permission
values (`denied`, `approval`, or `allowed`) declare eligibility and never alter the
central approval policy.

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

The model-backed Documentation Reviewer runs after every Tester outcome when enabled:

```yaml
team:
  documentation_review:
    enabled: true                  # run automatically after validation
    max_documents: 24             # allowed range 5–100
    max_findings: 30              # allowed range 1–100
    max_diff_summary_chars: 24000 # bounded sanitized context; 4,000–200,000
```

The exact configuration keys are `team.documentation_review.enabled`,
`team.documentation_review.max_documents`,
`team.documentation_review.max_findings`, and
`team.documentation_review.max_diff_summary_chars`. Disabling the stage leaves new
runs at `Documentation Not Run`; it does not change the persistent
`team.assignments.documentation` role assignment.

These bounds limit inventory, structured findings, and the sanitized context sent to
the configured planning model. They never grant file, shell, Git, Codex, approval,
Vault, OAuth, or repair access. `team docs` uses the same settings and fails clearly
when Documentation Review is disabled.

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
