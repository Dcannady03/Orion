# Orion v0.3.8.1 — Homecoming: Default Model Choice

## Summary

Model changes are now explicit about persistence. Orion switches immediately, then asks whether the selected Ollama model should become the startup default.

## Changes

- `ai use <model>` and the interactive Ollama model picker now switch the current session first.
- Orion asks whether the selected model should be saved as the default.
- Choosing Yes updates `config/default.yaml`; choosing No keeps the previous startup default.
- `ai status` now displays Current model, Default model, and Session override state.
- Selecting the already-active default model no longer triggers an unnecessary prompt.
