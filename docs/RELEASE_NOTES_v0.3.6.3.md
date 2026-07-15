# Orion v0.3.6.3 — Constellation: First Contact

## Overview

This patch introduces Orion's guided first-launch experience. A new user no longer
needs to understand YAML or manually create configuration files before Orion can boot.

## Added

- Conversational **First Contact** setup before core service initialization.
- Guided collection of name, location, timezone, language, intended use, workspace,
  Ollama connection, weather, calendar, email, and Docker preferences.
- Clear numbered choices, recommended defaults, validation, and a final review screen.
- Automatic creation of `config/default.yaml` and `config/profile.yaml`.
- Atomic YAML writes to reduce the risk of partial configuration files.
- Backups with the `.before-first-contact` suffix when setup is deliberately rerun.
- `python -m orion.main --first-contact` for demos, testing, or profile reconfiguration.
- Friendly cancellation behavior without a startup traceback.

## Architecture

First Contact runs before the Orion service graph is created. It produces the same
configuration and profile formats used by the existing managers, keeping onboarding
out of the runtime services and avoiding permanent first-run branches throughout the
application.

## Quality

- Added five onboarding regression tests.
- Full suite: **106 tests passing**.
