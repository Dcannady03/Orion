"""
Orion Core
"""


class Orion:

    VERSION = "0.0.1"
    CODENAME = "First Light"

    def __init__(self):
        self.status = "READY"

    def start(self):
        self.banner()

        print("Hello Daniel.")
        print()
        print("System Initialized.")
        print(f"Status: {self.status}")
        print()
        print("Welcome to Orion.")

    def banner(self):
        print("=" * 50)
        print("                 ORION")
        print("=" * 50)
        print(f"Version : {self.VERSION}")
        print(f"Codename: {self.CODENAME}")
        print()