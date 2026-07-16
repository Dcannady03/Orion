# Orion on Bazzite

This package adds native Linux/Bazzite launch support to Orion without modifying Bazzite's immutable base image.

## Files to copy into the Orion repository

Copy the `scripts` folder into the root of the Orion repository:

```text
Orion/
├── orion/
├── requirements.txt
└── scripts/
    ├── install-bazzite.sh
    ├── update-bazzite.sh
    └── uninstall-bazzite.sh
```

## Install

From the Orion repository:

```bash
chmod +x scripts/*.sh
./scripts/install-bazzite.sh
```

After installation, launch Orion from any terminal:

```bash
orion
```

Or open **Orion** from KDE's application launcher.

## What the installer does

- Creates `.venv` inside the Orion repository
- Installs `requirements.txt` inside the virtual environment
- Creates `~/.local/bin/orion`
- Creates `~/.local/share/applications/orion.desktop`
- Uses Orion's icon when one is found
- Does not use `dnf` or alter Bazzite's immutable system image

## Update Orion

```bash
./scripts/update-bazzite.sh
```

## Remove the launchers

```bash
./scripts/uninstall-bazzite.sh
```

This intentionally leaves the repository, `.venv`, configuration, memory, and project data untouched.

## First-time clone on Bazzite

```bash
mkdir -p ~/Documents/GitHub
cd ~/Documents/GitHub
git clone https://github.com/Dcannady03/Orion.git
cd Orion
```

Then copy in the scripts and run the installer.
