# Orion v0.4.3.6 — Signal: Access Fix

This maintenance release fixes Discord channel-wide conversation. Any human member in an approved channel may mention Orion and ask informational questions, while only configured owner Discord IDs may use DMs or request sensitive actions involving the computer, files, Git, Docker, email sending, provider changes, Vault changes, or software installation.

The Discord gateway now records messages before filtering, uses Discord-native mention detection, exposes exact ignore reasons through `connect debug`, and sends all accepted requests through Orion's shared Request Router.

Validation: 148 automated tests passing.
