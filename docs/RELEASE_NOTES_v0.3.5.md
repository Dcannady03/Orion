# Orion v0.3.5 — Weather

Weather is Orion's first live external information integration. It uses Open-Meteo's
forecast and geocoding APIs without requiring an account or API key.

## New commands

```text
weather
weather tomorrow
weather Sacramento
weather in Sacramento
what is the weather today?
ask will it rain today?
```

## Morning Star integration

The startup briefing now receives a live Weather item through the same provider contract
introduced in v0.3.4. If the network or API is unavailable, the failure is isolated and
Orion still starts normally. Developer Mode exposes provider diagnostics.

## Configuration

By default Orion uses the `location` field in `config/profile.yaml`. Override it in
`config/default.yaml` when needed:

```yaml
weather:
  location: "Yuba City, California"
  units: imperial
  timeout_seconds: 5
```

Set `units: metric` for Celsius and km/h.

## Privacy and safety

Weather requests send only the configured or requested place name to Open-Meteo's
geocoding service and its resolved coordinates to the forecast service. No API secret is
stored. Weather is read-only and does not use the Action approval pipeline.

## Verification

```text
Ran 82 tests
OK
```

## Hotfix: structured location resolution
The initial v0.3.5 build passed a full profile string such as `Yuba City, California` into Open-Meteo's city-name field. The hotfix separates the settlement name from region qualifiers, searches for the city, and ranks candidate locations by state or country. This restores both the `weather` command and the Morning Star weather briefing for qualified profile locations.
