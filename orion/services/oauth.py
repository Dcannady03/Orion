"""Shared, external, owner-only OAuth token helpers for Orion services."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Iterable


class OAuthError(ConnectionError):
    """Sanitized OAuth failure safe for Orion output and diagnostics."""


class OAuthCancelledError(OAuthError):
    """The user cancelled an interactive authorization flow."""


class OAuthScopeError(OAuthError):
    """A cached authorization does not contain the requested capability."""


def _owner_only(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _write_secret(path: Path, value: str) -> None:
    """Atomically persist sensitive OAuth state outside normal configuration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8")
        _owner_only(temporary)
        temporary.replace(path)
        _owner_only(path)
    finally:
        if temporary.exists():
            temporary.unlink()


class GoogleInstalledAppOAuth:
    """Google installed-app OAuth with explicit, non-expanding scope sets."""

    def __init__(
        self,
        credentials_path: str | Path,
        token_path: str | Path,
        scopes: Iterable[str],
        *,
        service_name: str,
        connect_command: str,
    ) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.scopes = tuple(dict.fromkeys(str(scope).strip() for scope in scopes if str(scope).strip()))
        self.service_name = service_name
        self.connect_command = connect_command
        if not self.scopes:
            raise ValueError("Google OAuth requires at least one scope.")

    @staticmethod
    def _imports():
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:  # pragma: no cover - dependency environment
            raise OAuthError(
                "Google OAuth dependencies are not installed. "
                "Run: python -m pip install -r requirements.txt"
            ) from exc
        return Request, Credentials, InstalledAppFlow

    @property
    def configured(self) -> bool:
        return self.credentials_path.is_file()

    @property
    def connected(self) -> bool:
        return self.token_path.is_file()

    def cached_scopes(self) -> tuple[str, ...]:
        if not self.token_path.is_file():
            return ()
        try:
            value = json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return ()
        scopes = value.get("scopes", []) if isinstance(value, dict) else []
        if isinstance(scopes, str):
            scopes = scopes.split()
        if not isinstance(scopes, list):
            return ()
        return tuple(sorted({str(scope) for scope in scopes if str(scope).strip()}))

    def _has_scopes(self, credentials) -> bool:
        try:
            return bool(credentials.has_scopes(self.scopes))
        except (AttributeError, TypeError, ValueError):
            cached = set(self.cached_scopes())
            return set(self.scopes).issubset(cached)

    def credentials(self, *, interactive: bool = False):
        Request, Credentials, InstalledAppFlow = self._imports()
        credentials = None
        if self.token_path.is_file():
            try:
                credentials = Credentials.from_authorized_user_file(str(self.token_path))
            except Exception as exc:
                raise OAuthError(f"{self.service_name} authorization cache could not be read.") from exc

        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                self._save(credentials)
            except Exception as exc:
                raise OAuthError(f"{self.service_name} authorization refresh failed.") from exc

        if credentials and credentials.valid and self._has_scopes(credentials):
            return credentials

        if credentials and credentials.valid and not self._has_scopes(credentials) and not interactive:
            raise OAuthScopeError(
                f"{self.service_name} needs additional read permission. "
                f"Run '{self.connect_command}' to approve it."
            )
        if not interactive:
            raise OAuthError(
                f"{self.service_name} is not connected. Run '{self.connect_command}'."
            )
        if not self.credentials_path.is_file():
            raise OAuthError(
                f"Google OAuth client configuration was not found at {self.credentials_path}."
            )
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_path),
                self.scopes,
            )
            credentials = flow.run_local_server(port=0)
        except (KeyboardInterrupt, EOFError) as exc:
            raise OAuthCancelledError(f"{self.service_name} authorization was cancelled.") from exc
        except Exception as exc:
            raise OAuthError(f"{self.service_name} authorization failed.") from exc
        if credentials is None or not getattr(credentials, "valid", False):
            raise OAuthCancelledError(f"{self.service_name} authorization was cancelled.")
        if not self._has_scopes(credentials):
            raise OAuthScopeError(f"{self.service_name} read permission was not granted.")
        self._save(credentials)
        return credentials

    def _save(self, credentials) -> None:
        try:
            value = credentials.to_json()
        except Exception as exc:
            raise OAuthError(f"{self.service_name} authorization could not be saved.") from exc
        _write_secret(self.token_path, value)

    def connect(self) -> None:
        self.credentials(interactive=True)

    def disconnect(self) -> None:
        try:
            self.token_path.unlink(missing_ok=True)
        except OSError as exc:
            raise OAuthError(f"{self.service_name} authorization could not be removed.") from exc


class MicrosoftPublicClientOAuth:
    """MSAL desktop OAuth using one scope-specific external token cache."""

    MSAL_RESERVED_SCOPES = frozenset({"openid", "profile", "offline_access"})

    def __init__(
        self,
        client_id: str,
        token_path: str | Path,
        scopes: Iterable[str],
        *,
        tenant: str = "common",
        service_name: str,
        connect_command: str,
    ) -> None:
        self.client_id = str(client_id).strip()
        self.token_path = Path(token_path)
        self.scopes = tuple(dict.fromkeys(str(scope).strip() for scope in scopes if str(scope).strip()))
        self.tenant = str(tenant).strip() or "common"
        self.service_name = service_name
        self.connect_command = connect_command
        if not self.scopes:
            raise ValueError("Microsoft OAuth requires at least one scope.")

    @property
    def request_scopes(self) -> tuple[str, ...]:
        """Return API scopes; MSAL adds its reserved OIDC scopes automatically."""
        return tuple(
            scope for scope in self.scopes
            if scope.lower() not in self.MSAL_RESERVED_SCOPES
        )

    @staticmethod
    def _import_msal():
        try:
            import msal
        except ImportError as exc:  # pragma: no cover - dependency environment
            raise OAuthError(
                "Microsoft OAuth dependencies are not installed. "
                "Run: python -m pip install -r requirements.txt"
            ) from exc
        return msal

    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    @property
    def connected(self) -> bool:
        return self.token_path.is_file()

    def _cache_and_app(self):
        if not self.client_id:
            raise OAuthError("Microsoft Application (client) ID is not configured.")
        msal = self._import_msal()
        cache = msal.SerializableTokenCache()
        if self.token_path.is_file():
            try:
                cache.deserialize(self.token_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise OAuthError(f"{self.service_name} authorization cache could not be read.") from exc
        try:
            app = msal.PublicClientApplication(
                self.client_id,
                authority=f"https://login.microsoftonline.com/{self.tenant}",
                token_cache=cache,
            )
        except Exception as exc:
            raise OAuthError("Microsoft OAuth client configuration is invalid.") from exc
        return app, cache

    def _save(self, cache) -> None:
        if cache.has_state_changed:
            try:
                _write_secret(self.token_path, cache.serialize())
            except Exception as exc:
                raise OAuthError(f"{self.service_name} authorization could not be saved.") from exc

    def token(self, *, interactive: bool = False) -> str:
        app, cache = self._cache_and_app()
        try:
            accounts = app.get_accounts()
            result = (
                app.acquire_token_silent(list(self.request_scopes), account=accounts[0])
                if accounts else None
            )
            if not result and interactive:
                result = app.acquire_token_interactive(
                    scopes=list(self.request_scopes),
                    prompt="select_account",
                )
            self._save(cache)
        except (KeyboardInterrupt, EOFError) as exc:
            raise OAuthCancelledError(f"{self.service_name} authorization was cancelled.") from exc
        except OAuthError:
            raise
        except Exception as exc:
            raise OAuthError(f"{self.service_name} authorization failed.") from exc

        if not result or "access_token" not in result:
            error = str((result or {}).get("error", "")).lower()
            if error in {"user_cancelled", "authentication_canceled", "access_denied"}:
                raise OAuthCancelledError(f"{self.service_name} authorization was cancelled.")
            if not interactive:
                raise OAuthError(
                    f"{self.service_name} is not connected. Run '{self.connect_command}'."
                )
            raise OAuthScopeError(f"{self.service_name} read permission was not granted.")
        return str(result["access_token"])

    def connect(self) -> None:
        self.token(interactive=True)

    def disconnect(self) -> None:
        try:
            self.token_path.unlink(missing_ok=True)
        except OSError as exc:
            raise OAuthError(f"{self.service_name} authorization could not be removed.") from exc
