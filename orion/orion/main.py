"""
Orion Entry Point
"""

from orion.core.orion import Orion


def main():
    """Launch Orion."""
    orion = Orion()
    orion.start()


if __name__ == "__main__":
    main()
