from pathlib import Path

# ==========================================
# ORION PROJECT SETUP
# Version 0.0.1 - First Light
# ==========================================

project_root = Path.cwd()

folders = [
    "docs",
    "tests",
    "config",

    "orion",
    "orion/core",
    "orion/memory",
    "orion/providers",
    "orion/agents",
    "orion/skills",
    "orion/cli",
    "orion/voice",
    "orion/ui",
]

files = [
    "README.md",
    ".gitignore",
    "requirements.txt",

    "orion/__init__.py",
    "orion/main.py",
]

print("=" * 40)
print(" ORION PROJECT SETUP")
print("=" * 40)

# Create folders
for folder in folders:
    path = project_root / folder
    path.mkdir(parents=True, exist_ok=True)
    print(f"[+] Created folder: {folder}")

# Create files
for file in files:
    path = project_root / file

    if not path.exists():
        path.touch()
        print(f"[+] Created file: {file}")
    else:
        print(f"[ ] Exists: {file}")

print("\nProject structure created successfully!")
print("Welcome to Orion.")