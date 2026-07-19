"""Orion entry point."""

from __future__ import annotations

import argparse

from orion.core.onboarding import FirstContact
from orion.core.orion import Orion


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
    return parser


def main(argv: list[str] | None = None):
    """Prepare the local profile, then launch Orion."""
    args = build_parser().parse_args(argv)
    onboarding = FirstContact()
    onboarding.run(force=args.first_contact)

    # A cancelled first contact leaves required files absent. Exit cleanly
    # instead of presenting a configuration traceback to a new user.
    if onboarding.is_required:
        return 1

    orion = Orion()
    discord_enabled = bool(orion.config_manager.get("connect.discord_bot.enabled", False))
    orion.start(discord=args.discord or discord_enabled)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
