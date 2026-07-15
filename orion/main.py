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
        help="run the guided first-launch experience again",
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
    orion.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
