"""Centralized Orion application and user-data paths."""
from __future__ import annotations

import os
from pathlib import Path


class OrionPaths:
    """Resolve immutable application files and mutable per-user data."""

    def __init__(self, install_root: str | Path | None = None, user_root: str | Path | None = None):
        self.install_root = Path(install_root or Path(__file__).resolve().parents[2]).resolve()
        configured = user_root or os.environ.get("ORION_USER_DATA")
        self.user_root = Path(configured).expanduser().resolve() if configured else (Path.home() / ".orion").resolve()

    @property
    def defaults(self) -> Path:
        return self.install_root / "config" / "default.yaml"

    @property
    def config(self) -> Path:
        return self.user_root / "config.yaml"

    @property
    def legacy_local_config(self) -> Path:
        return self.user_root / "config" / "local.yaml"

    @property
    def profile(self) -> Path:
        return self.user_root / "profile.yaml"

    @property
    def vault(self) -> Path:
        return self.user_root / "vault" / "vault.yaml"

    @property
    def tokens(self) -> Path:
        return self.user_root / "tokens"

    @property
    def backups(self) -> Path:
        return self.user_root / "backups"

    def ensure(self) -> None:
        for path in (
            self.user_root,
            self.user_root / "vault",
            self.tokens,
            self.user_root / "memory",
            self.user_root / "logs",
            self.user_root / "cache",
            self.backups,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def user_file(self, value: str | Path, *, category: str | None = None) -> Path:
        """Resolve legacy runtime paths into the external user-data root."""
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        parts = path.parts
        if parts and parts[0] == ".orion":
            path = Path(*parts[1:])
        if category:
            return self.user_root / category / path.name
        return self.user_root / path
