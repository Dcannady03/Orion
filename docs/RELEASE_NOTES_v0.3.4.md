# Orion v0.3.4 — Morning Star

## Mission

Create Orion's provider-neutral home screen before integrating external data sources.
Morning Star provides the architecture that Weather, Calendar, Email, Docker, Git, and
future services will use to contribute useful information at startup.

## What changed

- Added `BriefingItem`, `BriefingPriority`, and the `BriefingProvider` contract.
- Added a central `BriefingService` with provider registration and deterministic sorting.
- Added failure isolation: a broken provider is recorded but cannot stop Orion startup.
- Added a built-in System provider containing only live, verifiable Orion state.
- Added a dynamic startup briefing and an on-demand `briefing` command.
- Added provider diagnostics in Developer Mode.
- Added briefing information to `status` and completion/help surfaces.

## Provider contract

A provider exposes a name and returns zero or more `BriefingItem` objects. Lower priority
values render first: Critical, Important, then Informational. Providers should return no
item when they have nothing meaningful to report rather than inventing placeholder data.

## Safety and privacy

Morning Star performs no network calls and no actions. It only summarizes state already
available to Orion. Future providers remain responsible for their own permissions and
must use the Action framework for changes to the outside world.

## Compatibility

This is a backward-compatible upgrade from v0.3.3. Existing application catalogs,
aliases, trust decisions, history, memory, and project context remain compatible.

## Validation

```text
Ran 76 tests
OK
```
