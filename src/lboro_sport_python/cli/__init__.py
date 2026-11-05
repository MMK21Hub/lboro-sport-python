"""Command-line interface for the Lboro Sport (Legend) facility-hire API."""

from __future__ import annotations

from typing import Annotated

import typer

from .. import __version__
from .context import CliContext
from .sessions import sessions_app

app = typer.Typer(
    name="lboro-sport",
    help="Unofficial CLI for the Loughborough Sport (Legend) facility-hire service.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(sessions_app, name="sessions")


def _show_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    user: Annotated[
        bool,
        typer.Option(
            "--user",
            help="Sign in with LBORO_USERNAME/LBORO_PASSWORD instead of the public token.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_show_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Browse Loughborough Sport facility-hire sessions."""
    if ctx.resilient_parsing:
        return
    ctx.obj = CliContext(click_context=ctx, json_output=json_output, use_user=user)


def main() -> None:
    """Entry point for the ``lboro-sport`` console script."""
    app()


if __name__ == "__main__":
    main()
