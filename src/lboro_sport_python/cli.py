"""List available facility-hire slots."""

from __future__ import annotations

import json as json_module
from datetime import UTC, datetime, time, timedelta
from datetime import date as date_type
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import typer
from pydantic import ValidationError
from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from .client import LegendAPIError, LegendAuthError, LegendClient, LegendError
from .config import Settings
from .models import ActivityConcept, Location, Slot

console = Console()
err_console = Console(stderr=True)

_ENV_NAMES = {
    "api_base_url": "LBORO_API_BASE_URL",
    "oauth_token_url": "LBORO_OAUTH_TOKEN_URL",
    "client_id": "LBORO_CLIENT_ID",
    "client_secret": "LBORO_CLIENT_SECRET",
    "username": "LBORO_USERNAME",
    "password": "LBORO_PASSWORD",
}


def main() -> None:
    """Entry point for the ``lboro-sessions`` console script."""
    typer.run(sessions)


def sessions(
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
        typer.Option("--days", "-n", min=1, max=31, help="Number of days to search."),
    ] = 1,
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
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
    list_activities: Annotated[
        bool,
        typer.Option("--list-activities", help="List activities and locations, then exit."),
    ] = False,
    use_user: Annotated[
        bool,
        typer.Option(
            "--user",
            help="Sign in with LBORO_USERNAME/LBORO_PASSWORD instead of using the public token.",
        ),
    ] = False,
) -> None:
    """Find available facility-hire slots (for example badminton courts)."""
    try:
        settings = _load_settings()
    except typer.Exit:
        raise

    if date:
        try:
            start_day = date_type.fromisoformat(date)
        except ValueError as exc:
            raise typer.BadParameter(f"Invalid --date {date!r}; expected YYYY-MM-DD.") from exc
    else:
        start_day = date_type.today()

    try:
        with LegendClient(
            base_url=settings.api_base_url,
            token_url=settings.oauth_token_url,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
        ) as client:
            if use_user:
                if not settings.has_user_credentials:
                    raise typer.BadParameter("--user requires LBORO_USERNAME and LBORO_PASSWORD.")
                client.login(settings.username or "", settings.password or "")

            concepts = client.list_activities()
            if list_activities:
                _print_activities(concepts)
                return

            activity_ids, concept_label, locations = _resolve_activity(
                concepts=concepts,
                activity=activity,
                activity_id=activity_id,
                site=site,
                site_id=site_id,
            )

            rows: list[dict[str, Any]] = []
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
                        rows.append(
                            _slot_row(
                                day=day,
                                location=item.location,
                                slot=slot,
                                activity_label=item_label,
                            )
                        )

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

    if as_json:
        typer.echo(json_module.dumps(rows, indent=2))
        return

    if not rows:
        console.print(f"[yellow]No {activity} slots found for {start_day} + {days} days.[/yellow]")
        console.print("[dim]Try --include-full, a later --date, or more --days.[/dim]")
        return

    _print_slots(rows, days, wide=wide)


def _load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        err_console.print("[red]Invalid or missing configuration.[/red]")
        for error in exc.errors():
            field = ".".join(str(part) for part in error["loc"])
            env_name = _ENV_NAMES.get(field, field)
            err_console.print(f"  - {env_name}: {error['msg']}")
        err_console.print(
            "Copy .env.example to .env and fill in the Legend API values, "
            "or export them as environment variables."
        )
        raise typer.Exit(1) from exc


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
            chosen = ActivityConcept(identifier=activity_id, pref_label=activity)
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


def _spaces_text(spaces: int | None) -> Text:
    if spaces is None:
        return Text("?", style="dim")
    if spaces <= 0:
        return Text("Full", style="red")
    if spaces == 1:
        return Text("1 left", style="yellow")
    return Text(f"{spaces} left")


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
    console.print(f"[bold]{common_kind or 'Available slots'}[/bold]  [dim]{summary}[/dim]")

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
            f"[dim]{count} {'slot' if count == 1 else 'slots'}[/dim]"
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
        console.print(f"\n[dim]Times in {zones[0]}[/dim]")


if __name__ == "__main__":
    main()
