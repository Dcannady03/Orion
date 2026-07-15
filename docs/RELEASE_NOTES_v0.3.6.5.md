# Orion v0.3.6.5 — Constellation: Model Selector

## Added

- `change ollama model` interactive command.
- `ollama model` and `ollama models` aliases.
- Live discovery of locally installed models through Ollama `/api/tags`.
- Numbered model picker with current-model marker and cancellation.
- Immediate model switching without restarting Orion.
- Persistent selection in `config/default.yaml`.
- Command completion and help entry.

## Reliability

- Friendly handling when Ollama is offline or no models are installed.
- Invalid selections are rejected without changing configuration.
- Three dedicated model-selection tests.
- Full regression suite: 111 tests passing.
