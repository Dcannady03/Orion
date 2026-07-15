# Orion v0.4.3.1 — Signal Access Controls

This maintenance release makes the two-way Discord gateway safe for a single-channel deployment.

## Added

- Explicit Discord channel allowlisting.
- Optional required-role allowlisting for human Discord users.
- Automatic gateway startup after configuration.
- Commands to enable and disable the Discord interface.
- Expanded bot status showing configured users, channels, roles, enabled state, and running state.

## Security model

A server message reaches Orion only when all configured checks pass:

1. The message mentions Orion.
2. The author is an approved Discord user.
3. The message is in an allowed channel.
4. The author has an allowed role when role filtering is configured.

Direct messages are accepted only from approved user IDs.

Discord's own channel permissions should additionally give the Orion bot role access only to the designated development channel.

## Validation

141 automated tests pass.
