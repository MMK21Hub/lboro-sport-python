"""Discover the Legend facility-hire configuration and write a .env file."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console

from .bootstrap import (
    APP_ID,
    BUILD,
    CLUB_SEARCH_TERM,
    FRONTDESK_URL,
    VERSION_NAME,
    BootstrapClient,
    BootstrapError,
    BootstrapHTTPError,
    BootstrapResult,
    iter_linked_modules,
    legend_config_from_module,
    render_env,
    run_bootstrap,
    theme_key_for,
    write_env,
)

console = Console()
err_console = Console(stderr=True)


def main() -> None:
    """Entry point for the ``lboro-bootstrap`` console script."""
    typer.run(bootstrap)


def bootstrap(
    club_id: Annotated[
        int | None,
        typer.Option("--club-id", help="Skip club search and use this club id."),
    ] = None,
    club_search_term: Annotated[
        str,
        typer.Option("--club-search-term", help="MyFitApp club search term."),
    ] = CLUB_SEARCH_TERM,
    activity: Annotated[
        str,
        typer.Option("--activity", "-a", help="Facility activity to select."),
    ] = "badminton",
    module_id: Annotated[
        int | None,
        typer.Option("--module-id", help="Skip screen discovery and load this module."),
    ] = None,
    module_config: Annotated[
        Path | None,
        typer.Option(
            "--module-config",
            help="Offline mode: parse a saved module/screen JSON file.",
        ),
    ] = None,
    app_config: Annotated[
        Path | None,
        typer.Option(
            "--app-config",
            help="Offline mode: appConfig JSON used to read the theme id.",
        ),
    ] = None,
    theme_id: Annotated[
        int | None,
        typer.Option("--theme-id", help="Override the appConfig theme id."),
    ] = None,
    directions: Annotated[
        Path | None,
        typer.Option("--directions", help="Override the bundled directions.geojson."),
    ] = None,
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Dotenv path, or '-' for stdout."),
    ] = ".env",
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an existing output file."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print the discovered configuration as JSON."),
    ] = False,
    verify: Annotated[
        bool,
        typer.Option("--verify/--no-verify", help="Verify the credentials with Legend."),
    ] = True,
    create_user: Annotated[
        bool,
        typer.Option(
            "--create-user/--no-create-user",
            help="Create the anonymous serverless user needed by the home screen.",
        ),
    ] = True,
    max_screens: Annotated[
        int,
        typer.Option("--max-screens", min=1, max=500, help="Maximum screens to walk."),
    ] = 100,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="HTTP timeout in seconds."),
    ] = 20.0,
    frontdesk_url: Annotated[
        str,
        typer.Option("--frontdesk-url", help="MyFitApp front desk base URL."),
    ] = FRONTDESK_URL,
    app_id: Annotated[
        str,
        typer.Option("--app-id", help="MyFitApp app id."),
    ] = APP_ID,
    version_name: Annotated[
        str,
        typer.Option("--version-name", help="App version name."),
    ] = VERSION_NAME,
    build: Annotated[
        str,
        typer.Option("--build", help="App version code."),
    ] = BUILD,
    device_model: Annotated[
        str,
        typer.Option("--device-model", help="Device model sent to the backend."),
    ] = "sdk_gphone64_x86_64",
    locale: Annotated[
        str,
        typer.Option("--locale", help="Locale sent to the backend."),
    ] = "en_GB",
    region: Annotated[
        str,
        typer.Option("--region", help="Region sent to the backend."),
    ] = "GB",
    qa: Annotated[
        bool,
        typer.Option("--qa", help="Use the QA app-key variant."),
    ] = False,
    save_raw: Annotated[
        Path | None,
        typer.Option("--save-raw", help="Save raw appConfig/home/module JSON here."),
    ] = None,
) -> None:
    """Download the Lboro Sport app config and extract the Legend credentials."""

    def emit(message: str) -> None:
        err_console.print(f"[dim]{message}[/dim]", highlight=False)

    started = time.perf_counter()
    try:
        if module_config is not None:
            emit(f"Reading module config from {module_config}...")
            result = _offline_result(module_config, app_config, theme_id, activity, directions)
            count = None
            if verify:
                emit("Verifying credentials with Legend...")
                with BootstrapClient(timeout=timeout) as client:
                    count = client.verify_legend(result.legend)
            _finish(result, count, output, force, as_json, save_raw)
            err_console.print(f"[dim]Done in {time.perf_counter() - started:.1f}s[/dim]")
            return

        with BootstrapClient(
            frontdesk_url=frontdesk_url,
            app_id=app_id,
            version_name=version_name,
            build=build,
            device_model=device_model,
            locale=locale,
            region=region,
            timeout=timeout,
        ) as client:
            result = run_bootstrap(
                client,
                club_id=club_id,
                club_search_term=club_search_term,
                activity=activity,
                module_id=module_id,
                create_user=create_user,
                max_screens=max_screens,
                directions_path=directions,
                qa=qa,
                progress=emit,
            )
            count = None
            if verify:
                emit("Verifying credentials with Legend...")
                count = client.verify_legend(result.legend)

        _finish(result, count, output, force, as_json, save_raw)
        err_console.print(f"[dim]Done in {time.perf_counter() - started:.1f}s[/dim]")

    except BootstrapHTTPError as exc:
        err_console.print(f"[red]Backend error:[/red] {exc}")
        if exc.status_code == 500 and module_id is None:
            err_console.print(
                "The home-screen endpoint rejected the bootstrap. Try again with "
                "--module-id, or use --module-config with a saved JSON file."
            )
        raise typer.Exit(1) from exc
    except BootstrapError as exc:
        err_console.print(f"[red]Bootstrap failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    except FileExistsError as exc:
        err_console.print(f"[red]Refusing to overwrite:[/red] {exc}")
        raise typer.Exit(1) from exc
    except httpx.HTTPError as exc:
        err_console.print(f"[red]Network error:[/red] {exc}")
        raise typer.Exit(1) from exc
    except (json.JSONDecodeError, OSError) as exc:
        err_console.print(f"[red]Could not read input:[/red] {exc}")
        raise typer.Exit(1) from exc


def _offline_result(
    module_path: Path,
    app_config_path: Path | None,
    theme_id: int | None,
    activity: str,
    directions: Path | None,
) -> BootstrapResult:
    raw = json.loads(module_path.read_text(encoding="utf-8"))
    module = _pick_module(raw, activity)

    resolved_theme = theme_id
    if resolved_theme is None and app_config_path is not None:
        app_config = json.loads(app_config_path.read_text(encoding="utf-8"))
        resolved_theme = int(app_config.get("esid", 1))
    if resolved_theme is None:
        resolved_theme = 1

    theme_key = theme_key_for(resolved_theme, directions)
    legend = legend_config_from_module(module, theme_key)
    return BootstrapResult(
        app_config={},
        theme_id=resolved_theme,
        theme_key=theme_key,
        club={},
        module=module,
        legend=legend,
    )


def _pick_module(raw: Any, activity: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, dict) and (data.get("type") == 38 or data.get("apiUrl")):
            return data
        if raw.get("type") == 38 or raw.get("apiUrl"):
            return raw

    modules = [module for module in iter_linked_modules(raw) if module.get("type") == 38]
    if not modules:
        raise BootstrapError(f"No Legend module found in {activity!r} JSON input")
    needle = activity.strip().lower()
    for module in modules:
        if needle in str(module.get("name") or "").lower():
            return module
    if len(modules) == 1:
        return modules[0]
    names = ", ".join(sorted(str(module.get("name")) for module in modules))
    raise BootstrapError(f"No module matched {activity!r}. Available: {names}")


def _finish(
    result: BootstrapResult,
    verified_count: int | None,
    output: str,
    force: bool,
    as_json: bool,
    save_raw: Path | None,
) -> None:
    if save_raw is not None:
        save_raw.mkdir(parents=True, exist_ok=True)
        (save_raw / "appconfig.json").write_text(
            json.dumps(result.app_config, indent=2), encoding="utf-8"
        )
        (save_raw / "module.json").write_text(json.dumps(result.module, indent=2), encoding="utf-8")
        if result.raw_home is not None:
            (save_raw / "home.json").write_text(
                json.dumps(result.raw_home, indent=2), encoding="utf-8"
            )

    if as_json:
        payload = {
            "legend": result.legend.model_dump(mode="json"),
            "club": result.club,
            "module": result.module,
            "theme_id": result.theme_id,
            "installation_id": result.installation_id,
            "user_id": result.user_id,
            "screens_visited": result.screens_visited,
            "verified_activities": verified_count,
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    if output == "-":
        typer.echo(render_env(result.legend), nl=False)
        return

    path = write_env(result.legend, Path(output), force=force)
    club_name = result.club.get("name") or result.club.get("klubName") or "unknown"
    module_name = result.legend.module_name or "unknown"
    console.print(f"[green]Club:[/green] {club_name} ({result.club.get('id', '?')})")
    console.print(f"[green]Module:[/green] {module_name} ({result.legend.module_id or '?'})")
    console.print(f"[green]Theme id:[/green] {result.theme_id}")
    if result.screens_visited:
        console.print(f"[green]Screens visited:[/green] {result.screens_visited}")
    if result.installation_id:
        console.print(f"[green]App installation:[/green] {result.installation_id}")
    if verified_count is not None:
        console.print(f"[green]Verified:[/green] {verified_count} activities available")
    console.print(f"[green]Wrote:[/green] {path}")
