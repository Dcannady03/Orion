# Orion v0.3.6.4 — Constellation Weather Resilience

This maintenance release hardens the weather experience discovered during First Contact testing.

## Changes

- Fresh weather reports are cached for five minutes.
- Morning Star and conversational weather questions now share the same recent report.
- If Open-Meteo temporarily returns an error after a successful fetch, Orion uses the last known report for the current session.
- Added two weather regression tests.

## Validation

- Full automated test suite passes.
