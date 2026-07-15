# Orion v0.3.7 — AI Control Center

## Highlights

- Promoted AI model management to a first-class Orion service.
- Added rich Ollama metadata: parameters, disk size, context, and inferred capabilities.
- Added natural model switching: `ai use`, `use <model>`, and `switch to <model>`.
- Added transparent recommendations for fastest, coding, reasoning, and vision use cases.
- Added persistent AI profiles: balanced, coding, creative, lightweight, and vision.
- Added an opt-in quick latency benchmark with a resource warning.
- Preserved `change ollama model` and immediate no-restart switching.
- Added regression coverage; all 115 tests pass.

## Commands

```text
ai
ai status
ai models
ai use qwen3.5:9b
use the fastest model
switch to qwen3.6:35b
ai profiles
ai profile coding
ai benchmark
```

Orion deliberately reports measured latency only. It does not manufacture subjective quality scores.
