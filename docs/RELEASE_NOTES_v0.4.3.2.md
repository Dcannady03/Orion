# Orion v0.4.3.2 — Signal: Shared Brain Routing

- Added a shared natural-language request router for every Orion interface.
- Discord weather and calendar questions now use Orion's live services before AI fallback.
- CLI `ask` requests use the same route as Discord and future interfaces.
- Missing `discord.py` no longer crashes Orion at startup.
- Orion offers to install the optional Discord dependency using the active Python interpreter.
- Declining or failed installation continues startup safely without Discord.
