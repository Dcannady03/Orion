# Orion v0.4.4 — Horizon

Horizon establishes Home as a first-class Orion Center rather than a CLI-only dashboard.

## Added

- `HomeService`, registered in Orion's central `ServiceRegistry`.
- Immutable `HomeSnapshot` and `HomeCard` models for interface-neutral dashboard data.
- Time-aware greetings and normalized user/location metadata.
- Provider error data that interfaces may reveal in Developer Mode.
- Dedicated Home Center unit tests.

## Changed

- Startup and the `home` command now request a snapshot from `HomeService`.
- The console renders Home snapshots instead of reaching into Orion services directly.
- Morning Star remains the provider aggregation layer; Home Center is now the reusable presentation boundary above it.

## Verification

- Full automated suite: **150 tests passing**.
- Existing weather, calendar, Connect, AI, companion, action, and plugin behavior remains covered.

## Architectural significance

The CLI, future GUI, web interface, and mobile companion can now consume the same Home Center contract without duplicating service logic.
