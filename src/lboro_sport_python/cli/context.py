"""Shared CLI state: settings, error handling and the API client."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import httpx
import typer
from pydantic import ValidationError

from ..client import LegendAPIError, LegendAuthError, LegendClient, LegendError
from ..config import Settings
from .render import err_console

_ENV_NAMES = {
    "api_base_url": "LBORO_API_BASE_URL",
    "oauth_token_url": "LBORO_OAUTH_TOKEN_URL",
    "client_id": "LBORO_CLIENT_ID",
    "client_secret": "LBORO_CLIENT_SECRET",
    "username": "LBORO_USERNAME",
    "password": "LBORO_PASSWORD",
}


def load_settings() -> Settings:
    try:
        return Settings()  # type: ignore - these get filled in from env
    except ValidationError as exc:
        err_console.print("[red]Invalid or missing configuration.[/red]")
        for error in exc.errors():
            name = ".".join(str(part) for part in error["loc"])
            err_console.print(f"  - {_ENV_NAMES.get(name, name)}: {error['msg']}")
        err_console.print(
            "Copy .env.example to .env and fill in the Legend API values, "
            "or export them as environment variables."
        )
        raise typer.Exit(1) from exc


@contextmanager
def handle_api_errors() -> Iterator[None]:
    """Turn Legend/network failures into friendly messages and a clean exit."""
    try:
        yield
    except LegendAuthError as exc:
        err_console.print(f"[red]Authentication error:[/red] {exc}")
        raise typer.Exit(1) from exc
    except LegendAPIError as exc:
        err_console.print(f"[red]API error:[/red] {exc}")
        if exc.status_code in (401, 403):
            err_console.print(
                "Check LBORO_CLIENT_ID/LBORO_CLIENT_SECRET, and try --user for endpoints "
                "that need a member token."
            )
        raise typer.Exit(1) from exc
    except LegendError as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    except httpx.HTTPError as exc:
        err_console.print(f"[red]Network error:[/red] {exc}")
        raise typer.Exit(1) from exc


@dataclass
class CliContext:
    """State shared by every subcommand.

    Settings and the API client are created on first use, so ``--help`` and other
    parsing-only paths never require a configured environment.
    """

    click_context: typer.Context
    json_output: bool = False
    use_user: bool = False
    _settings: Settings | None = field(default=None, init=False)
    _client: LegendClient | None = field(default=None, init=False)

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = load_settings()
        return self._settings

    @property
    def client(self) -> LegendClient:
        if self._client is None:
            settings = self.settings
            client = LegendClient(
                base_url=settings.api_base_url,
                token_url=settings.oauth_token_url,
                client_id=settings.client_id,
                client_secret=settings.client_secret,
            )
            self.click_context.with_resource(client)
            if self.use_user:
                if not settings.has_user_credentials:
                    raise typer.BadParameter(
                        "--user requires LBORO_USERNAME and LBORO_PASSWORD."
                    )
                with handle_api_errors():
                    client.login(settings.username or "", settings.password or "")
            self._client = client
        return self._client
