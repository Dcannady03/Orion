# Orion v0.5.4 — Sentinel

Sentinel evolves Orion's explainable AI Routing Engine from fixed fallback rules into
a health-aware system informed by real provider and model performance.

## Private performance telemetry

Each routed attempt records only aggregate operational facts: provider, model,
duration, success or failure, and a bounded error message. Orion never stores prompts
or responses in performance telemetry. Data remains user-owned at
`~/.orion/cache/ai-routing-stats.json`.

## Health-aware routing

Routing profiles still decide which providers best fit conversation, coding,
research, and complex reasoning. Once a provider has enough observations, Sentinel
classifies it as healthy, degraded, or unhealthy. Repeatedly unhealthy providers are
moved behind healthier fallbacks. Adaptive ordering can be disabled through
`ai.routing.adaptive`.

## Commands

```text
ai stats
ai health
ai route status
ai route explain last
ai benchmark
```

The v0.5.4 regression suite contains 186 passing tests.
