# Orion v0.3.6.2 — Constellation Polish

## Overview

This maintenance release moves Calendar provider management into Orion's conversational CLI. Users no longer need to edit `config/default.yaml` just to enable, disable, or configure Google and Microsoft calendars.

## New commands

```text
calendar providers
calendar enable google
calendar enable microsoft
calendar disable google
calendar disable microsoft
calendar configure google
calendar configure microsoft
calendar connect google
calendar connect microsoft
```

Provider state and configuration changes are persisted to `config/default.yaml`. Both built-in providers stay registered even while disabled, allowing them to be enabled during the current Orion session.

## Example Microsoft setup

```text
Orion> calendar enable microsoft
Personal Outlook is now enabled.
Client ID is not configured yet. Run: calendar configure microsoft

Orion> calendar configure microsoft
Enter your Microsoft Application (client) ID: ...
Microsoft Calendar configuration saved.
Run: calendar connect microsoft
```

## Safety

OAuth still begins only after an explicit `calendar connect <provider>` command. Enabling or configuring a provider does not open a browser.

## Tests

101 tests passing.
