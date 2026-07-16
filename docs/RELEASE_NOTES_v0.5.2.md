# Orion v0.5.2 — Navigator

Navigator introduces Orion's first explainable AI Routing Engine. Orion can now select among Ollama, OpenAI, and Gemini according to the active routing profile and request type.

## Profiles

- **Fast** — prefers the local Ollama model.
- **Balanced** — uses local AI for short requests and cloud AI for complex work.
- **Coding** — prefers OpenAI for coding and architecture, then falls back locally.
- **Research** — prefers Gemini for research and long-context work, then OpenAI.

## Commands

```text
ai route status
ai route on
ai route off
ai route explain last
ai profile fast
ai profile balanced
ai profile coding
ai profile research
```

Provider failures and timeouts automatically advance to the next configured provider. Each decision records the profile, task type, selected provider/model, fallback order, reason, and duration.
