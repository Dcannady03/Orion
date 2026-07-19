# Orion v0.5.8 — Prism

Prism updates Orion's First Contact experience to match its current provider-neutral
AI architecture. New installations and explicit reruns can configure Ollama, OpenAI,
Google Gemini, more than one provider, or no AI provider yet.

## Shared provider setup

First Contact now delegates to the same configuration, provider, Vault, and routing
services used by Orion's normal AI commands. Ollama discovery checks the configured
local endpoint and lists installed models dynamically. OpenAI and Gemini credentials
are verified against their model endpoints before Orion changes stored credentials or
the active provider.

Multiple-provider setup lets the user choose the initial active provider and apply the
existing Fast, Balanced, Coding, or Research routing profile. Orion remains
provider-neutral: local AI is supported, and no cloud provider or execution engine is
mandatory.

## Safe reruns and credentials

- Candidate API keys remain in memory until verification succeeds.
- Verified keys are persisted only through external Orion Vault.
- Keys never enter normal YAML configuration, console output, logs, or task artifacts.
- Blank, rejected, unavailable, or failed provider setup preserves existing credentials
  and the active provider.
- Forced First Contact reruns merge existing profile and configuration values rather
  than replacing the full documents.
- The legacy `setup_orion.py` entry point now delegates to the supported First Contact
  workflow instead of maintaining a second First Light-era scaffolder.

## Completion summary

The final First Contact summary reports the active provider and model, other connected
providers, routing profile, workspace, configured services, and detected execution
engines. Codex CLI is reported when available, while ChatGPT Desktop is explicitly
identified as a desktop application rather than a CLI implementation engine.

## Verification

The v0.5.8 regression suite contains **273 passing tests**, including Ollama-only,
OpenAI, Gemini, multiple-provider, skip, cancellation, rerun preservation, failed
credential verification, secret isolation, shared command paths, and execution-engine
summary coverage.
