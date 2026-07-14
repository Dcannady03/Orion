"""Application discovery, matching, aliases, and launching for Orion."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import subprocess
from typing import Callable, Iterable


@dataclass(frozen=True)
class Application:
    """A launchable application discovered on the host."""

    name: str
    path: str
    source: str

    @property
    def key(self) -> str:
        return normalize_name(self.name)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ApplicationMatch:
    application: Application
    score: float
    matched_by: str


class ApplicationCatalog:
    """Persistent, workspace-local application catalog and alias store."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.state_dir = self.workspace_root / ".orion"
        self.catalog_path = self.state_dir / "app-catalog.json"
        self.alias_path = self.state_dir / "app-aliases.json"
        self._applications: list[Application] = []
        self._aliases: dict[str, str] = {}
        self.load()

    def bind(self, workspace_root: str | Path) -> None:
        """Move catalog persistence to a newly selected Orion workspace."""
        self.workspace_root = Path(workspace_root).resolve()
        self.state_dir = self.workspace_root / ".orion"
        self.catalog_path = self.state_dir / "app-catalog.json"
        self.alias_path = self.state_dir / "app-aliases.json"
        self.load()

    def load(self) -> None:
        self._applications = []
        self._aliases = {}
        if self.catalog_path.exists():
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            self._applications = [Application(**item) for item in payload.get("applications", [])]
        if self.alias_path.exists():
            payload = json.loads(self.alias_path.read_text(encoding="utf-8"))
            self._aliases = {
                normalize_name(alias): str(target)
                for alias, target in payload.get("aliases", {}).items()
            }

    def replace(self, applications: Iterable[Application]) -> None:
        unique: dict[tuple[str, str], Application] = {}
        for app in applications:
            unique[(app.key, os.path.normcase(app.path))] = app
        self._applications = sorted(unique.values(), key=lambda item: (item.name.lower(), item.path.lower()))
        self._write_catalog()

    def applications(self) -> tuple[Application, ...]:
        return tuple(self._applications)

    def aliases(self) -> dict[str, str]:
        return dict(self._aliases)

    def set_alias(self, alias: str, target_path: str) -> None:
        key = normalize_name(alias)
        if not key:
            raise ValueError("Alias cannot be empty.")
        if not any(os.path.normcase(app.path) == os.path.normcase(target_path) for app in self._applications):
            raise KeyError(f"Application is not in the catalog: {target_path}")
        self._aliases[key] = target_path
        self._write_aliases()

    def remove_alias(self, alias: str) -> bool:
        removed = self._aliases.pop(normalize_name(alias), None) is not None
        if removed:
            self._write_aliases()
        return removed

    def _write_catalog(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {"applications": [app.to_dict() for app in self._applications]}
        self.catalog_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _write_aliases(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.alias_path.write_text(json.dumps({"aliases": self._aliases}, indent=2), encoding="utf-8")


class ApplicationDiscoveryService:
    """Discovers launchable applications from Start Menu and desktop locations."""

    SUPPORTED_SUFFIXES = {".lnk", ".url", ".exe", ".appref-ms"}

    def __init__(self, catalog: ApplicationCatalog, roots: Iterable[str | Path] | None = None) -> None:
        self.catalog = catalog
        self.roots = tuple(Path(root).expanduser() for root in (roots or self.default_roots()))

    @staticmethod
    def default_roots() -> tuple[Path, ...]:
        roots: list[Path] = []
        program_data = os.environ.get("PROGRAMDATA")
        app_data = os.environ.get("APPDATA")
        user_profile = os.environ.get("USERPROFILE")
        if program_data:
            roots.append(Path(program_data) / "Microsoft/Windows/Start Menu/Programs")
        if app_data:
            roots.append(Path(app_data) / "Microsoft/Windows/Start Menu/Programs")
        if user_profile:
            roots.append(Path(user_profile) / "Desktop")
        public = os.environ.get("PUBLIC")
        if public:
            roots.append(Path(public) / "Desktop")
        return tuple(roots)

    def scan(self) -> tuple[Application, ...]:
        discovered: list[Application] = []
        for root in self.roots:
            if not root.exists() or not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in self.SUPPORTED_SUFFIXES:
                    continue
                name = friendly_name(path)
                if not name:
                    continue
                discovered.append(Application(name=name, path=str(path.resolve()), source=self._source_name(root)))
        self.catalog.replace(discovered)
        return self.catalog.applications()

    @staticmethod
    def _source_name(root: Path) -> str:
        lower = str(root).lower()
        return "Desktop" if "desktop" in lower else "Start Menu"


class ApplicationMatcher:
    """Resolves natural-language names against aliases and discovered applications."""

    def __init__(self, catalog: ApplicationCatalog) -> None:
        self.catalog = catalog

    def find(self, query: str, limit: int = 5) -> tuple[ApplicationMatch, ...]:
        normalized = normalize_name(query)
        if not normalized:
            return ()

        aliases = self.catalog.aliases()
        alias_target = aliases.get(normalized)
        if alias_target:
            app = next((item for item in self.catalog.applications() if os.path.normcase(item.path) == os.path.normcase(alias_target)), None)
            if app:
                return (ApplicationMatch(app, 1.0, "alias"),)

        # Multiple shortcuts can represent the same logical application (for
        # example, one in the Start Menu and another on the Desktop). Match
        # against one preferred representative per normalized name so those
        # duplicate launch points are not mistaken for ambiguity.
        representatives: dict[str, Application] = {}
        for app in self.catalog.applications():
            current = representatives.get(app.key)
            if current is None or application_preference(app) < application_preference(current):
                representatives[app.key] = app

        matches: list[ApplicationMatch] = []
        for app in representatives.values():
            candidate = app.key
            if candidate == normalized:
                score, reason = 1.0, "exact"
            elif normalized in candidate:
                score, reason = 0.92 - min(0.2, (len(candidate) - len(normalized)) / 100), "contains"
            elif candidate in normalized:
                score, reason = 0.88, "expanded"
            else:
                score, reason = SequenceMatcher(None, normalized, candidate).ratio(), "fuzzy"
            if score >= 0.45:
                matches.append(ApplicationMatch(app, score, reason))
        matches.sort(key=lambda item: (-item.score, item.application.name.lower()))
        return tuple(matches[: max(1, limit)])

    def resolve(self, query: str, minimum_score: float = 0.62, ambiguity_gap: float = 0.08) -> ApplicationMatch | None:
        matches = self.find(query, limit=2)
        if not matches or matches[0].score < minimum_score:
            return None
        if len(matches) > 1 and matches[0].matched_by != "alias" and matches[0].score - matches[1].score < ambiguity_gap:
            return None
        return matches[0]


class ApplicationLauncherService:
    """Launches catalog applications, with an optional Windows Search fallback."""

    def __init__(
        self,
        matcher: ApplicationMatcher,
        launch_path: Callable[[str], None] | None = None,
        search_fallback: Callable[[str], None] | None = None,
    ) -> None:
        self.matcher = matcher
        self._launch_path = launch_path or launch_application_path
        self._search_fallback = search_fallback or launch_windows_search

    def launch(self, query: str, *, allow_search_fallback: bool = True) -> str:
        match = self.matcher.resolve(query)
        if match is not None:
            self._launch_path(match.application.path)
            return f"Opening {match.application.name}."
        if allow_search_fallback:
            self._search_fallback(query)
            return f"No confident catalog match. Searching Windows for {query}."
        raise FileNotFoundError(f"No application match found for: {query}")



def application_preference(application: Application) -> tuple[int, str]:
    """Rank duplicate launch points; lower values are preferred."""
    source_rank = {"Start Menu": 0, "Desktop": 1}.get(application.source, 2)
    return source_rank, application.path.lower()

def normalize_name(value: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else " " for character in value)
    return " ".join(cleaned.split())


def friendly_name(path: Path) -> str:
    name = path.stem.strip()
    for suffix in (" - Shortcut", " Shortcut"):
        if name.lower().endswith(suffix.lower()):
            name = name[: -len(suffix)].strip()
    return name


def launch_application_path(path: str) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
        return
    subprocess.Popen([path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch_windows_search(query: str) -> None:
    """Open Windows Search for an unknown target without using a shell command."""
    if os.name != "nt":
        raise OSError("Windows Search fallback is only available on Windows.")
    os.startfile(f"search-ms:query={query}")  # type: ignore[attr-defined]
