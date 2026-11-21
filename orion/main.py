"""Orion entry point."""
from __future__ import annotations

import argparse
import sys

from orion.core.onboarding import FirstContact
from orion.core.orion import Orion
from orion.core.paths import OrionPaths
from orion.core.singleton import SingletonLock, SingletonLockHeld


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Orion AI Operating System")
    parser.add_argument(
        "--first-contact",
        action="store_true",
        help="configure profile, workspace, AI providers, Vault, routing, and services",
    )
    parser.add_argument(
        "--discord",
        action="store_true",
        help="start the approved two-way Discord interface beside the CLI",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="run headless: start the loopback-only local API, skip the console",
    )
    parser.add_argument(
        "--server-setup",
        action="store_true",
        help="set the server password (local only, hidden prompt, no Orion start)",
    )
    return parser


def main(argv: list[str] | None = None):
    """Prepare the local profile, then launch Orion."""
    args = build_parser().parse_args(argv)
    if args.server_setup:
        # The setup command runs without the singleton lock and without
        # constructing Orion: it only writes auth.json and a recovery
        # record, so it works while the server is running.
        if args.server or args.first_contact or args.discord:
            print(
                "Orion --server-setup cannot be combined with --server, "
                "--first-contact, or --discord.",
                file=sys.stderr,
            )
            return 1
        from orion.server.setup import run_server_setup

        return run_server_setup()
    if args.server and args.first_contact:
        print(
            "Orion server mode is headless; --first-contact is interactive. "
            "Run first-contact setup from the CLI without --server.",
            file=sys.stderr,
        )
        return 1

    # One Orion per user-data root, in both modes: the OS-held lock is
    # taken before anything else runs and held for the process lifetime.
    # The OS releases it automatically on process death, including
    # abrupt termination, so there is no stale lock to clean up.
    mode = "server" if args.server else "cli"
    lock = SingletonLock(OrionPaths().server_lock, mode=mode)
    try:
        lock.acquire()
    except SingletonLockHeld as exc:
        print(f"Orion cannot start: {exc}", file=sys.stderr)
        return 2
    try:
        onboarding = FirstContact()
        if args.server and onboarding.is_required:
            print(
                "Orion server mode requires completed first-contact setup. "
                "Run Orion once from the CLI to finish setup, then start "
                "the server.",
                file=sys.stderr,
            )
            return 1
        onboarding.run(force=args.first_contact)

        # A cancelled first contact leaves required files absent. Exit cleanly
        # instead of presenting a configuration traceback to a new user.
        if onboarding.is_required:
            return 1

        orion = Orion()
        discord_enabled = bool(orion.config_manager.get("connect.discord_bot.enabled", False))
        try:
            orion.start(discord=args.discord or discord_enabled, server=args.server)
        except (RuntimeError, OSError) as exc:
            if not args.server:
                raise
            print(f"Orion server failed to start: {exc}", file=sys.stderr)
            return 1
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
