import io
from datetime import date, timedelta

from rich.console import Console

from lboro_sport_python import cli
from lboro_sport_python.models import Slot


def _row(**overrides):
    row = {
        "date": date.today().isoformat(),
        "start": f"{date.today().isoformat()}T16:00:00+01:00",
        "activity": "Badminton - Netball Centre",
        "site": "Loughborough Uni",
        "site_id": "site-1",
        "start_time": "16:00",
        "end_time": "17:00",
        "timezone": "Europe/London",
        "spaces": 4,
        "price": "14.00 GBP",
        "price_display": "£14",
        "slot_id": "slot-123",
        "facilities": [],
    }
    row.update(overrides)
    return row


def test_display_activity_strips_common_kind() -> None:
    assert cli._display_activity("Badminton - Netball Centre", "Badminton") == "Netball Centre"
    assert cli._display_activity("Badminton - Netball Centre") == "Badminton · Netball Centre"


def test_format_price_display() -> None:
    gbp = Slot.model_validate({"identifier": "s", "offer": [{"price": 14, "priceCurrency": "GBP"}]})
    usd = Slot.model_validate(
        {"identifier": "s", "offer": [{"price": 14.5, "priceCurrency": "USD"}]}
    )
    chf = Slot.model_validate({"identifier": "s", "offer": [{"price": 14, "priceCurrency": "CHF"}]})
    assert cli._format_price_display(gbp) == "£14"
    assert cli._format_price_display(usd) == "$14.50"
    assert cli._format_price_display(chf) == "14.00 CHF"


def _capture(monkeypatch, rows, *, wide=False, days=1) -> str:
    output = io.StringIO()
    test_console = Console(file=output, width=80, force_terminal=False, color_system=None)
    monkeypatch.setattr(cli, "console", test_console)
    cli._print_slots(rows, days, wide=wide)
    return output.getvalue()


def test_print_slots_groups_by_day_and_hides_details(monkeypatch) -> None:
    tomorrow = date.today() + timedelta(days=1)
    rows = [
        _row(),
        _row(
            date=tomorrow.isoformat(),
            start=f"{tomorrow.isoformat()}T09:00:00+01:00",
            start_time="09:00",
            end_time="10:00",
            activity="Badminton - Sir David Wallace",
            spaces=1,
        ),
    ]
    text = _capture(monkeypatch, rows, days=2)

    assert "Today ·" in text
    assert "Tomorrow ·" in text
    assert "Netball Centre" in text
    assert "Sir David Wallace" in text
    assert "16:00-17:00" in text
    assert "£14" in text
    assert "1 left" in text
    assert "slot-123" not in text
    assert "Courts" not in text
    assert "Date" not in text


def test_print_slots_wide_shows_site_and_slot_id(monkeypatch) -> None:
    text = _capture(monkeypatch, [_row()], wide=True)
    assert "Loughborough Uni" in text
    assert "slot-123" in text
