# Orion v0.4.3.3 — Signal: Discord Diagnostics

This maintenance release hardens Orion's real two-way Discord gateway.

## Improvements

- `@Orion ask what's the weather?` and `@Orion what's the weather?` now normalize to the same shared request.
- Startup reports when Discord begins connecting and when the bot is online.
- The terminal reports authorized requests and explains when a request is ignored because of user, channel, or role restrictions.
- Immediate Discord login or configuration failures are surfaced during startup.
- Weather and Calendar continue to route through Orion services before the active AI provider.
- Missing `discord.py` remains a friendly install prompt rather than a traceback.
