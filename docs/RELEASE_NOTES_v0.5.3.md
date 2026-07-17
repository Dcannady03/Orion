# Orion v0.5.3 — Watchtower

Watchtower adds continuous local-network and Internet health monitoring through
Orion's plugin system.

## Commands

```text
network status
network watch [seconds]
network report
network stop
network config
```

The plugin checks the local gateway and two public Internet targets. It tracks
outages, packet loss, average and peak latency, and latency spikes, then explains
whether a failure is most likely on the local network or upstream with the ISP.

Background monitoring runs in a daemon thread and writes inspectable JSON Lines logs
to `~/.orion/logs/network/`. Monitoring is isolated from Orion's core startup and can
be stopped through the plugin lifecycle.

The v0.5.3 regression suite contains 181 passing tests.
