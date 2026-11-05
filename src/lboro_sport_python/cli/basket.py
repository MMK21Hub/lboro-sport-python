"""The ``basket`` command group."""

from __future__ import annotations

import typer

from ..models import OrderItem
from .context import CliContext, handle_api_errors
from .render import console, print_basket

basket_app = typer.Typer(
    name="basket",
    help="View or empty your basket.",
    no_args_is_help=True,
)


@basket_app.command("clear")
def clear(ctx: typer.Context) -> None:
    """Remove every item from your basket (requires --user)."""
    cli_ctx: CliContext = ctx.obj
    if not cli_ctx.use_user:
        raise typer.BadParameter(
            "basket clear needs a member token; run with --user "
            "(and set LBORO_USERNAME/LBORO_PASSWORD)."
        )

    with handle_api_errors():
        client = cli_ctx.client
        order = client.create_order()
        items: list[OrderItem] = order.order_items
        if not items:
            console.print("[yellow]Your basket is already empty.[/yellow]")
            return

        print_basket(items)
        count = len(items)
        plural = "" if count == 1 else "s"
        if not typer.confirm(f"Remove {count} item{plural} from your basket?", default=False):
            console.print("[yellow]Cancelled - your basket is unchanged.[/yellow]")
            raise typer.Exit()

        for item in items:
            client.remove_order_item(order.identifier, item.identifier)

    console.print(f"[green]Cleared {count} item{plural} from your basket.[/green]")
