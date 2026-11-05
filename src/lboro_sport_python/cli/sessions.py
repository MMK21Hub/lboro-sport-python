"""The ``sessions`` command group."""

from __future__ import annotations

import json as json_module
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from datetime import date as date_type
from typing import Annotated, Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import typer

from ..client import LegendClient
from ..models import ActivityConcept, Location, Order, OrderItem, Slot, SlotDetail
from .context import CliContext, handle_api_errors
from .render import (
    _format_price,
    _format_price_display,
    _print_activities,
    _print_slots,
    console,
    err_console,
    link_text,
    slot_line,
)

sessions_app = typer.Typer(
    name="sessions",
    help="Find and book facility-hire sessions.",
    no_args_is_help=True,
)


@dataclass
class SlotMatch:
    """A bookable slot together with the context needed to display and book it."""

    day: date_type
    location: Location | None
    slot: Slot
    activity_label: str

    def row(self) -> dict[str, Any]:
        return _slot_row(
            day=self.day,
            location=self.location,
            slot=self.slot,
            activity_label=self.activity_label,
        )


@sessions_app.command("search")
def search(
    ctx: typer.Context,
    activity: Annotated[
        str,
        typer.Option("--activity", "-a", help="Activity name or identifier to search for."),
    ] = "badminton",
    date: Annotated[
        str | None,
        typer.Option("--date", "-d", help="First day to search (YYYY-MM-DD). Defaults to today."),
    ] = None,
    days: Annotated[
        int,
        typer.Option("--days", "-n", min=1, help="Number of days to search."),
    ] = 5,
    activity_id: Annotated[
        str | None,
        typer.Option("--activity-id", help="Skip activity lookup and use this identifier."),
    ] = None,
    site_id: Annotated[
        str | None,
        typer.Option("--site-id", help="Only search this location identifier."),
    ] = None,
    site: Annotated[
        str | None,
        typer.Option("--site", help="Only search locations whose name contains this text."),
    ] = None,
    include_full: Annotated[
        bool,
        typer.Option("--include-full", help="Include slots with no spaces left."),
    ] = False,
    page_size: Annotated[
        int,
        typer.Option("--page-size", min=1, max=500, help="API page size."),
    ] = 100,
    wide: Annotated[
        bool,
        typer.Option("--wide", "-w", help="Show the site and slot ID under each slot."),
    ] = False,
    list_activities: Annotated[
        bool,
        typer.Option("--list-activities", help="List activities and locations, then exit."),
    ] = False,
) -> None:
    """Find available facility-hire slots (for example badminton courts)."""
    cli_ctx: CliContext = ctx.obj
    start_day = _parse_start_day(date)

    with handle_api_errors():
        client = cli_ctx.client
        concepts = client.list_activities()
        if list_activities:
            _print_activities(concepts)
            return
        matches, _ = _search_matches(
            client,
            concepts,
            activity=activity,
            activity_id=activity_id,
            site=site,
            site_id=site_id,
            start_day=start_day,
            days=days,
            page_size=page_size,
            include_full=include_full,
        )

    rows = [match.row() for match in matches]

    if cli_ctx.json_output:
        typer.echo(json_module.dumps(rows, indent=2))
        return

    if not rows:
        console.print(
            f"[yellow]No {activity} slots found for {start_day} + {days} days.[/yellow]",
            highlight=False,
        )
        console.print(
            "[dim]Try --include-full, a later --date, or more --days.[/dim]",
            highlight=False,
        )
        return

    _print_slots(rows, days, wide=wide)


@sessions_app.command("book")
def book(
    ctx: typer.Context,
    activity: Annotated[
        str,
        typer.Option("--activity", "-a", help="Activity name or identifier to search for."),
    ] = "badminton",
    date: Annotated[
        str | None,
        typer.Option("--date", "-d", help="First day to search (YYYY-MM-DD). Defaults to today."),
    ] = None,
    days: Annotated[
        int,
        typer.Option("--days", "-n", min=1, help="Number of days to search."),
    ] = 5,
    activity_id: Annotated[
        str | None,
        typer.Option("--activity-id", help="Skip activity lookup and use this identifier."),
    ] = None,
    site_id: Annotated[
        str | None,
        typer.Option("--site-id", help="Only search this location identifier."),
    ] = None,
    site: Annotated[
        str | None,
        typer.Option("--site", help="Only search locations whose name contains this text."),
    ] = None,
    page_size: Annotated[
        int,
        typer.Option("--page-size", min=1, max=500, help="API page size."),
    ] = 100,
    wide: Annotated[
        bool,
        typer.Option("--wide", "-w", help="Include the slot ID in the picker."),
    ] = False,
) -> None:
    """Pick an available slot and reserve it (requires --user; pay at the returned link)."""
    cli_ctx: CliContext = ctx.obj
    if not cli_ctx.use_user:
        raise typer.BadParameter(
            "sessions book needs a member token; run with --user "
            "(and set LBORO_USERNAME/LBORO_PASSWORD)."
        )
    start_day = _parse_start_day(date)

    with handle_api_errors():
        client = cli_ctx.client
        concepts = client.list_activities()
        matches, _ = _search_matches(
            client,
            concepts,
            activity=activity,
            activity_id=activity_id,
            site=site,
            site_id=site_id,
            start_day=start_day,
            days=days,
            page_size=page_size,
        )

        if not matches:
            console.print(
                f"[yellow]No bookable {activity} slots found for "
                f"{start_day} + {days} days.[/yellow]",
                highlight=False,
            )
            raise typer.Exit(1)

        rows = [match.row() for match in matches]
        count = len(rows)
        console.print(
            f"[bold]{count} bookable {'slot' if count == 1 else 'slots'}[/bold]  "
            "[dim]arrows to move · Enter to select[/dim]",
            highlight=False,
        )

        index = _select_slot(rows, wide=wide)
        if index is None:
            console.print("[yellow]No slot selected.[/yellow]")
            raise typer.Exit(1)

        match = matches[index]
        detail = client.get_slot(match.slot.identifier)
        facility_ids = _select_courts(detail)

        summary = slot_line(rows[index], wide=wide)
        if not typer.confirm(f"Book {summary}?", default=False):
            console.print("[yellow]Booking cancelled.[/yellow]")
            raise typer.Exit()

        order = client.create_order()
        item = client.add_facility_use(order.identifier, match.slot.identifier, facility_ids)
        checkout_url = _fill_checkout_url(order.checkout_url, client.access_token)

    _report_booking(order, item, checkout_url)


def _parse_start_day(value: str | None) -> date_type:
    if not value:
        return date_type.today()
    try:
        return date_type.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid --date {value!r}; expected YYYY-MM-DD.") from exc


def _search_matches(
    client: LegendClient,
    concepts: list[ActivityConcept],
    *,
    activity: str,
    activity_id: str | None,
    site: str | None,
    site_id: str | None,
    start_day: date_type,
    days: int,
    page_size: int,
    include_full: bool = False,
) -> tuple[list[SlotMatch], str]:
    activity_ids, concept_label, locations = _resolve_activity(
        concepts=concepts,
        activity=activity,
        activity_id=activity_id,
        site=site,
        site_id=site_id,
    )

    matches: list[SlotMatch] = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        date_from, date_end = _search_window(day)
        response = client.search_facility_uses(
            location_ids=[location.identifier for location in locations],
            activity_ids=activity_ids,
            date_from=date_from,
            date_end=date_end,
            page_size=page_size,
        )
        for item in response.items:
            for slot in item.slots:
                if (
                    not include_full
                    and slot.remaining_uses is not None
                    and slot.remaining_uses <= 0
                ):
                    continue
                item_label = item.activity[0].label if item.activity else concept_label
                matches.append(
                    SlotMatch(
                        day=day,
                        location=item.location,
                        slot=slot,
                        activity_label=item_label,
                    )
                )
    return matches, concept_label


def _select_slot(rows: list[dict[str, Any]], *, wide: bool = False) -> int | None:
    import questionary

    choices = [
        questionary.Choice(title=slot_line(row, wide=wide), value=index)
        for index, row in enumerate(rows)
    ]
    try:
        return questionary.select("Select a slot", choices=choices).ask()
    except KeyboardInterrupt:
        return None


def _select_courts(detail: SlotDetail) -> list[str] | None:
    if not detail.allow_facility_selection:
        return None
    available = [facility for facility in detail.facility if facility.is_available is not False]
    if not available:
        return None

    import questionary

    choices = [
        questionary.Choice(title=facility.label, value=facility.identifier)
        for facility in available
    ]
    try:
        picked = questionary.checkbox("Select court(s)", choices=choices).ask()
    except KeyboardInterrupt:
        return None
    return list(picked) if picked else None


def _fill_checkout_url(url: str | None, token: str | None) -> str | None:
    """Substitute the placeholders Legend leaves in the checkout URL."""
    if not url:
        return None
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}/"
    return (
        url.replace("{AuthToken}", token or "")
        .replace("{RedirectUrlAuthFailure}", origin)
        .replace("{CompleteRedirectUrl}", origin)
    )


def _report_booking(order: Order, item: OrderItem, checkout_url: str | None) -> None:
    console.print(
        "[green]Slot reserved.[/green] Your basket is held for [bold]20 minutes[/bold] - "
        "complete payment to confirm the booking."
    )
    console.print(f"  Order: {order.identifier} ({order.order_status or 'unknown'})")
    reservation_id = item.ordered_item.reservation_identifier if item.ordered_item else None
    if reservation_id:
        console.print(f"  Reservation: {reservation_id}")
    if checkout_url:
        console.print("  Pay here:")
        console.print("  ", link_text(checkout_url), soft_wrap=True)
    else:
        console.print(
            "  [dim]No checkout URL returned - pay via the Loughborough Sport app or "
            "website to confirm the booking.[/dim]"
        )
    console.print("[dim]Raw API responses saved under logs/.[/dim]", highlight=False)


def _resolve_activity(
    *,
    concepts: list[ActivityConcept],
    activity: str,
    activity_id: str | None,
    site: str | None,
    site_id: str | None,
) -> tuple[list[str], str, list[Location]]:
    needle = activity.strip().lower()
    if not needle and activity_id is None:
        raise typer.BadParameter("--activity cannot be empty")

    if activity_id:
        chosen = next((c for c in concepts if c.identifier == activity_id), None)
        if chosen is None:
            chosen = ActivityConcept(identifier=activity_id, prefLabel=activity)
        matches = [chosen]
    else:
        exact = [
            c
            for c in concepts
            if (c.pref_label or "").lower() == needle or c.identifier.lower() == needle
        ]
        prefix = [c for c in concepts if (c.pref_label or "").lower().startswith(needle)]
        partial = [
            c
            for c in concepts
            if needle in (c.pref_label or "").lower() or needle in c.identifier.lower()
        ]
        matches = exact or prefix or partial
        if not matches:
            available = ", ".join(c.label for c in concepts) or "(none)"
            raise typer.BadParameter(f"No activity matched {activity!r}. Available: {available}")
        if len(matches) > 1 and not exact:
            labels = ", ".join(c.label for c in matches[:4])
            suffix = "..." if len(matches) > 4 else ""
            err_console.print(
                f"[yellow]Searching {len(matches)} activities matching "
                f"{activity!r}: {labels}{suffix}[/yellow]"
            )

    activity_ids = list(dict.fromkeys(c.identifier for c in matches))
    locations: list[Location] = []
    seen_locations: set[str] = set()
    for concept in matches:
        for location in concept.locations:
            if location.identifier not in seen_locations:
                seen_locations.add(location.identifier)
                locations.append(location)

    label = matches[0].label
    if site_id:
        selected = [location for location in locations if location.identifier == site_id]
        locations = selected or [Location(identifier=site_id, name=site_id)]
    if site:
        site_needle = site.lower()
        filtered = [location for location in locations if site_needle in location.label.lower()]
        if not filtered:
            raise typer.BadParameter(f"No location matched {site!r} for activity {label!r}.")
        locations = filtered
    if not locations:
        raise typer.BadParameter(
            f"Activity {label!r} has no locations in the API response; pass --site-id."
        )

    return activity_ids, label, locations


def _search_window(day: date_type) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    start = datetime.combine(day, time.min, tzinfo=UTC)
    if day == now.date():
        start = now
    end = datetime.combine(day, time(23, 59, 59), tzinfo=UTC)
    return start, end


def _slot_row(
    *,
    day: date_type,
    location: Location | None,
    slot: Slot,
    activity_label: str,
) -> dict[str, Any]:
    zone = _zone(location.time_zone if location else None)
    start = slot.start_date.astimezone(zone) if slot.start_date else None
    end = slot.end_time.astimezone(zone) if slot.end_time else None
    return {
        "date": day.isoformat(),
        "activity": activity_label,
        "site": location.label if location else None,
        "site_id": location.identifier if location else None,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "start_local": start.strftime("%Y-%m-%d %H:%M") if start else None,
        "end_local": end.strftime("%H:%M") if end else None,
        "start_time": start.strftime("%H:%M") if start else None,
        "end_time": end.strftime("%H:%M") if end else None,
        "timezone": str(zone),
        "spaces": slot.remaining_uses,
        "price": _format_price(slot),
        "price_display": _format_price_display(slot),
        "slot_id": slot.identifier,
        "facilities": [facility.label for facility in slot.facility_availability],
    }


def _zone(name: str | None) -> ZoneInfo:
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo("UTC")
