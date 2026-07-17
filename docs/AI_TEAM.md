# Orion AI Team — Phase 1

Phase 1 proves that Orion can coordinate specialized AI roles and pass structured work
between them. It is intentionally planning-only.

## Workflow

```text
Goal -> Architect JSON -> Engineer Review JSON -> Final Plan -> Awaiting Approval
```

The orchestrator makes exactly two provider calls. It does not expose tools to either
role, retry indefinitely, modify source files, run tests, create commits, or open pull
requests.

## Commands

```text
team
team roles
team plan "Add OpenAI image generation"
team status <task-id>
```

## Role configuration

Each active role can use any provider already supported and configured by Orion. The
default `configured-default` value follows `providers.default` and that provider's
configured model.

```yaml
team:
  enabled: true
  roles:
    architect:
      provider: configured-default
      model: configured-default
    engineer:
      provider: configured-default
      model: configured-default
    reviewer:
      provider: configured-default
      model: configured-default
```

The Reviewer assignment is reserved for a later implementation phase and is not
called by `team plan`. Phase 1 accepts Orion's existing runtime providers (`ollama`,
`openai`, and `gemini`). Engineer specialization comes from its dedicated role prompt;
direct Codex execution is intentionally deferred until the approved implementation
phase.

## Structured role output

Every active role must return one JSON object with this schema:

```json
{
  "summary": "Short role result",
  "recommendations": ["Ordered step"],
  "risks": ["Concise risk"],
  "next_action": "What should happen next"
}
```

All four fields are required, and unknown fields are rejected. Invalid JSON or any
schema violation stops the run, records a sanitized failure state, and does not call
the next role.

## Persistence

Each task is stored as an individual JSON file under:

```text
~/.orion/team/tasks/<task-id>.json
```

The file contains the goal, status, artifacts, messages, final plan, timestamps, and
usage estimates. Files are written atomically with owner-only permissions where the
platform supports them. They are outside the application directory and survive Orion
updates.

Persisted tasks are validated before saving and after loading. Orion rejects missing
or mismatched task identity, unknown status values, malformed or timezone-free
timestamps, invalid message and usage records, and unexpected fields rather than
loading a partially valid task.

## Token and cost reporting

The existing provider contract returns text, so Phase 1 estimates tokens from request
and response length and labels them accordingly. Ollama's configured rate is zero.
Cloud cost remains `not configured` unless rates are supplied locally:

```yaml
team:
  pricing:
    openai:
      input_per_million: null
      output_per_million: null
```

This avoids embedding prices that may become outdated.
