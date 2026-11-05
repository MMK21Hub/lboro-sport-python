"""Rich console output for the Lboro Sport CLI."""

from __future__ import annotations

from datetime import date as date_type
from datetime import timedelta
from typing import Any

from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from ..models import ActivityConcept, Offer, OrderItem, Slot

console = Console()
err_console = Console(stderr=True)


def _format_price(slot: Slot) -> str | None:
    price = slot.price
    if price is None:
        return None
    currency = slot.currency or ""
    return f"{price:.2f} {currency}".strip()


_CURRENCY_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$"}


def _format_price_display(slot: Slot) -> str | None:
    price = slot.price
    if price is None:
        return None
    currency = (slot.currency or "").upper()
    symbol = _CURRENCY_SYMBOLS.get(currency)
    if symbol is None:
        return f"{price:.2f} {currency}".strip()
    if price == price.to_integral_value():
        return f"{symbol}{int(price)}"
    return f"{symbol}{price:.2f}"


def _split_activity(label: str | None) -> tuple[str, str]:
    text = label or ""
    if " - " in text:
        kind, venue = text.split(" - ", 1)
        return kind.strip(), venue.strip()
    return "", text


def _display_activity(label: str | None, common_kind: str = "") -> str:
    kind, venue = _split_activity(label)
    if common_kind and kind.lower() == common_kind.lower():
        return venue
    return (label or "").replace(" - ", " · ")


def _date_label(value: str) -> str:
    day = date_type.fromisoformat(value)
    today = date_type.today()
    if day == today:
        prefix = "Today · "
    elif day == today + timedelta(days=1):
        prefix = "Tomorrow · "
    else:
        prefix = ""
    return prefix + day.strftime("%a %d %b")


def _spaces_plain(spaces: int | None) -> str:
    if spaces is None:
        return "?"
    if spaces <= 0:
        return "Full"
    if spaces == 1:
        return "1 left"
    return f"{spaces} left"


def _spaces_text(spaces: int | None) -> Text:
    if spaces is None:
        return Text(_spaces_plain(spaces), style="dim")
    if spaces <= 0:
        return Text(_spaces_plain(spaces), style="red")
    if spaces == 1:
        return Text(_spaces_plain(spaces), style="yellow")
    return Text(_spaces_plain(spaces))


def link_text(url: str) -> Text:
    """A clickable hyperlink (rendered as an OSC 8 link where supported)."""
    return Text(url, style=f"link {url}")


def slot_line(row: dict[str, Any], *, wide: bool = False) -> str:
    """A one-line, plain-text summary of a slot row, mirroring the table columns."""
    parts = [
        _date_label(row["date"]),
        f"{row.get('start_time') or '?'}-{row.get('end_time') or '?'}",
        _display_activity(row.get("activity")),
    ]
    if row.get("site"):
        parts.append(str(row["site"]))
    if row.get("price_display"):
        parts.append(str(row["price_display"]))
    parts.append(_spaces_plain(row.get("spaces")))
    if wide and row.get("slot_id"):
        parts.append(str(row["slot_id"]))
    return " · ".join(parts)


def _slot_table(activity_width: int) -> Table:
    table = Table.grid(padding=(0, 1), expand=False)
    table.add_column(no_wrap=True, width=11)
    table.add_column(no_wrap=True, overflow="ellipsis", width=activity_width)
    table.add_column(justify="right", no_wrap=True, width=6)
    table.add_column(justify="right", no_wrap=True, width=6)
    return table


def _slot_cells(row: dict[str, Any], common_kind: str = "") -> tuple[Text, Text, Text, Text]:
    start = row.get("start_time") or "?"
    end = row.get("end_time") or "?"
    return (
        Text(f"{start}-{end}", style="bold"),
        Text(_display_activity(row.get("activity"), common_kind)),
        _spaces_text(row.get("spaces")),
        Text(row.get("price_display") or "", style="dim"),
    )


def _offer_amount(offer: Offer | None) -> str | None:
    if offer is None:
        return None
    value = offer.value if offer.value is not None else offer.price
    if value is None:
        return None
    currency = (offer.price_currency or offer.currency or "").strip()
    return f"{value:.2f} {currency}".strip()


def print_basket(items: list[OrderItem]) -> None:
    table = Table(title="Basket")
    table.add_column("Item")
    table.add_column("When", style="dim")
    table.add_column("Price", justify="right", style="dim")
    for item in items:
        ordered = item.ordered_item
        name = (ordered.name if ordered and ordered.name else None) or item.identifier
        when = (
            ordered.start_date.strftime("%a %d %b %H:%M") if ordered and ordered.start_date else "-"
        )
        table.add_row(name, when, _offer_amount(item.amount) or "")
    console.print(table)


def _print_activities(concepts: list[ActivityConcept]) -> None:
    if not concepts:
        console.print("[yellow]The API returned no activities.[/yellow]")
        return
    table = Table(title="Activities")
    table.add_column("Activity")
    table.add_column("Activity ID")
    table.add_column("Locations")
    for concept in concepts:
        locations = "\n".join(
            f"{location.label} ({location.identifier})" for location in concept.locations
        )
        table.add_row(concept.label, concept.identifier, locations or "-")
    console.print(table)


def _print_slots(rows: list[dict[str, Any]], days: int, *, wide: bool = False) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(
        rows,
        key=lambda item: (
            item["date"],
            item.get("start") or "",
            item.get("activity") or "",
        ),
    ):
        grouped.setdefault(row["date"], []).append(row)

    sites = sorted({row["site"] for row in rows if row.get("site")})
    if len(sites) == 1:
        site_text = sites[0]
    elif sites:
        site_text = f"{len(sites)} venues"
    else:
        site_text = ""
    days_with_slots = len(grouped)
    day_text = f"{days_with_slots} {'day' if days_with_slots == 1 else 'days'}"
    if days > 1 and days_with_slots != days:
        day_text = f"{day_text} of {days}"
    slot_text = f"{len(rows)} {'slot' if len(rows) == 1 else 'slots'}"
    summary = f"{slot_text} · {day_text}"
    if site_text:
        summary = f"{summary} · {site_text}"

    kinds = {kind for kind, _ in (_split_activity(row.get("activity")) for row in rows) if kind}
    common_kind = next(iter(kinds)) if len(kinds) == 1 else ""
    console.print(
        f"[bold]{common_kind or 'Available slots'}[/bold]  [dim]{summary}[/dim]",
        highlight=False,
    )

    longest_activity = max(
        (len(_display_activity(row.get("activity"), common_kind)) for row in rows),
        default=24,
    )
    activity_width = min(longest_activity + 2, max(24, console.width - 32))

    for date_value, day_rows in grouped.items():
        console.print()
        count = len(day_rows)
        console.print(
            f"[bold]{_date_label(date_value)}[/bold]  "
            f"[dim]{count} {'slot' if count == 1 else 'slots'}[/dim]",
            highlight=False,
        )

        if wide:
            for row in day_rows:
                table = _slot_table(activity_width)
                table.add_row(*_slot_cells(row, common_kind))
                details = []
                if row.get("site"):
                    details.append(str(row["site"]))
                if row.get("slot_id"):
                    details.append(str(row["slot_id"]))
                if row.get("facilities"):
                    court_count = len(row["facilities"])
                    details.append(f"{court_count} {'court' if court_count == 1 else 'courts'}")
                if details:
                    console.print(
                        Group(
                            table,
                            Text(" " * 12 + " · ".join(details), style="dim"),
                        )
                    )
                else:
                    console.print(table)
        else:
            table = _slot_table(activity_width)
            for row in day_rows:
                table.add_row(*_slot_cells(row, common_kind))
            console.print(table)

    zones = sorted({row["timezone"] for row in rows if row.get("timezone")})
    if len(zones) == 1:
        console.print(f"\n[dim]Times in {zones[0]}[/dim]", highlight=False)
