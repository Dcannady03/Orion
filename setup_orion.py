"""Legacy compatibility launcher for Orion First Contact.

The old First Light project scaffolder was removed because it duplicated an obsolete
setup workflow. New and existing installations use ``python -m orion.main
--first-contact``; this file remains only so older shortcuts delegate to that one
supported onboarding path.
"""

from orion.main import main


if __name__ == "__main__":
    raise SystemExit(main(["--first-contact"]))
