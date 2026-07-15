# Orion v0.4.0 — Polaris

Polaris begins Orion's AI Federation architecture. Orion remains the coordinator, identity, memory, context, approval boundary, and tool owner; providers are replaceable intelligence engines.

## New commands

```text
ai providers
ai provider configure openai
ai provider configure gemini
ai provider use ollama
ai provider use openai
ai provider use gemini
ai provider models openai
ai provider models gemini
```

## Provider support

- **Ollama** remains enabled by default.
- **OpenAI** uses the Responses API.
- **Gemini** uses the Gemini generateContent API.

## API-key handling

Orion first checks the provider environment variable:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY`

When a user explicitly configures a provider inside Orion, the key is written to `.orion/secrets.yaml`, separate from `config/default.yaml`. Orion applies restrictive file permissions where the operating system supports them. Users should protect the Orion folder and never commit `.orion/secrets.yaml`.

## Architecture principle

> Models think. Orion decides and acts.

Agent roles and task delegation will be built on this provider-neutral foundation in later Polaris releases.

## Validation

- 127 automated tests passing.
- No API keys, authentication tokens, generated indexes, caches, or Git history included in the release package.
