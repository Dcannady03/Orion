"""Local-only server password setup command (Phase B).

Run on the server itself (via SSH or RustDesk):

    python -m orion.main --server-setup

The command prompts for the password with hidden input (getpass, the
``ai connect`` pattern), hashes it with scrypt, and writes the hash
directly to ``~/.orion/server/auth.json``. There is no setup token, no
web setup page, and nothing credential-related is written to any log:
a headless scheduled task has no console to read a token from, and a
setup token in a persistent log would be a credential sitting in a
file.

The command runs WITHOUT the singleton lock and WITHOUT constructing
Orion: it only touches auth.json and the recovery history, so it works
while the server is running. Changing the password signs out every
device (all sessions are revoked and the CSRF secret rotates).

Recovery: delete ``~/.orion/server/auth.json`` and re-run this command.
Losing the password is recoverable only with server access, by design —
there is no email reset, no security question, and no backdoor.

When stdin is not a terminal (piped input, a non-interactive SSH
command), the prompt reads the password from stdin instead of hiding
it — the same fallback getpass uses on POSIX when /dev/tty is
unavailable. This matters on Windows, where getpass reads the console
via msvcrt.getch() even with stdin redirected, and blocks forever when
no console is attached.
"""
from __future__ import annotations

import sys
from getpass import getpass
from typing import Callable

from orion.core.paths import OrionPaths
from orion.server.auth import (
    MIN_PASSWORD_LENGTH,
    CredentialStore,
    record_auth_event,
)


def _prompt_password(prompt: str) -> str:
    """Hidden prompt on a terminal; plain stdin read without one."""
    if sys.stdin.isatty():
        return getpass(prompt)
    print(
        "No interactive terminal; reading the password from stdin.",
        file=sys.stderr,
    )
    return input(prompt)


def run_server_setup(
    paths: OrionPaths | None = None,
    *,
    input_provider: Callable[[str], str] = _prompt_password,
    output: Callable[[str], None] = print,
) -> int:
    """Set the server password. Returns 0 on success, 1 on failure."""
    paths = paths or OrionPaths()
    store = CredentialStore(paths)
    existed = store.exists
    if existed:
        output(
            "A server password already exists. Setting a new one signs "
            "out every device."
        )
    try:
        password = input_provider(
            f"New server password (min {MIN_PASSWORD_LENGTH} characters): "
        )
        confirm = input_provider("Confirm server password: ")
    except (KeyboardInterrupt, EOFError):
        output("Setup cancelled.")
        return 1
    if not password:
        output("Setup cancelled.")
        return 1
    if password != confirm:
        output("Passwords do not match.")
        return 1
    if len(password) < MIN_PASSWORD_LENGTH:
        output(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
        return 1
    try:
        changed = store.set_password(password)
    except OSError as exc:
        output(f"Could not write the password file: {exc}")
        return 1
    if changed:
        record_auth_event(
            paths,
            "password-change",
            "server password",
            detail=(
                "Server password changed via local setup command; "
                "all sessions revoked."
            ),
        )
        output("Server password changed. Every device has been signed out.")
    else:
        record_auth_event(
            paths,
            "setup",
            "server password",
            detail="Server password set via local setup command.",
        )
        output(
            "Server password set. Start the server with "
            "python -m orion.main --server"
        )
    return 0
