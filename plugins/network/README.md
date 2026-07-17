# Network Watch Plugin

Monitors Orion's local gateway and two public Internet targets so outages can be
classified as local-network failures or likely ISP failures.

Commands: `network status`, `network watch [seconds]`, `network report`,
`network stop`, and `network config`.

The default targets are the router at `10.0.0.1`, Cloudflare at `1.1.1.1`, and
Google at `8.8.8.8`. JSON Lines logs are stored in `~/.orion/logs/network/`.
