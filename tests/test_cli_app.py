import json

import httpx
import respx
import typer
from typer.testing import CliRunner

from lboro_sport_python import __version__
from lboro_sport_python.cli import app, sessions

runner = CliRunner()


def _configure_env(monkeypatch, *, with_user: bool = False) -> None:
    monkeypatch.setenv("LBORO_API_BASE_URL", "https://legend.test/api")
    monkeypatch.setenv("LBORO_OAUTH_TOKEN_URL", "https://legend.test/oauth/token")
    monkeypatch.setenv("LBORO_CLIENT_ID", "client-id")
    monkeypatch.setenv("LBORO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("LBORO_USERNAME", "member@example.com" if with_user else "")
    monkeypatch.setenv("LBORO_PASSWORD", "password" if with_user else "")


def _mock_slot_listing() -> None:
    respx.post("https://legend.test/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "abc", "expires_in": 3600})
    )
    respx.get("https://legend.test/api/facility-uses/activitylist").mock(
        return_value=httpx.Response(
            200,
            json={
                "concepts": [
                    {
                        "identifier": "badminton",
                        "prefLabel": "Badminton",
                        "locations": [{"identifier": "site-1", "name": "Netball Centre"}],
                    }
                ]
            },
        )
    )
    respx.post("https://legend.test/api/facility-uses").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "activity": [{"identifier": "badminton", "prefLabel": "Badminton"}],
                        "location": {"identifier": "site-1", "name": "Netball Centre"},
                        "slots": [
                            {
                                "identifier": "slot-1",
                                "startDate": "2026-10-19T16:00:00+0000",
                                "duration": "PT60M",
                                "remainingUses": 2,
                                "offer": [{"price": 8.5, "priceCurrency": "GBP"}],
                            }
                        ],
                    }
                ]
            },
        )
    )


def test_root_help_lists_sessions() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "sessions" in result.stdout


def test_bare_invocation_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.stdout
    assert "sessions" in result.stdout


def test_bare_sessions_shows_help() -> None:
    result = runner.invoke(app, ["sessions"])
    assert "Usage" in result.stdout
    assert "search" in result.stdout
    assert "book" in result.stdout


def test_sessions_search_help_lists_options() -> None:
    result = runner.invoke(app, ["sessions", "search", "--help"])
    assert result.exit_code == 0
    assert "--activity" in result.stdout
    assert "--days" in result.stdout


def test_sessions_book_help_drops_display_only_options() -> None:
    result = runner.invoke(app, ["sessions", "book", "--help"])
    assert result.exit_code == 0
    assert "--activity" in result.stdout
    assert "--wide" in result.stdout
    assert "--list-activities" not in result.stdout
    assert "--include-full" not in result.stdout


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_unknown_command_fails() -> None:
    result = runner.invoke(app, ["nope"])
    assert result.exit_code != 0


def test_user_flag_without_credentials_errors(monkeypatch) -> None:
    _configure_env(monkeypatch)

    result = runner.invoke(app, ["--user", "sessions", "search", "--days", "1"])

    assert result.exit_code != 0
    assert "LBORO_USERNAME" in result.output


def test_sessions_book_requires_user(monkeypatch) -> None:
    _configure_env(monkeypatch)

    result = runner.invoke(app, ["sessions", "book", "--days", "1"])

    assert result.exit_code != 0
    assert "--user" in result.output


@respx.mock
def test_sessions_search_prints_json(monkeypatch) -> None:
    _configure_env(monkeypatch)
    _mock_slot_listing()

    result = runner.invoke(
        app, ["--json", "sessions", "search", "--activity", "badminton", "--days", "1"]
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert len(rows) == 1
    assert rows[0]["slot_id"] == "slot-1"
    assert rows[0]["price_display"] == "£8.50"


@respx.mock
def test_sessions_book_books_the_selected_slot(monkeypatch) -> None:
    _configure_env(monkeypatch, with_user=True)
    _mock_slot_listing()
    respx.get("https://legend.test/api/facility-uses/slots/slot-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "identifier": "slot-1",
                "allowFacilitySelection": True,
                "facility": [
                    {"identifier": "court-1", "name": "Court 1", "isAvailable": True},
                ],
            },
        )
    )
    order_route = respx.post("https://legend.test/api/orders").mock(
        return_value=httpx.Response(200, json={"identifier": "order-1", "orderStatus": "Basket"})
    )
    add_route = respx.post("https://legend.test/api/orders/order-1/orderitems/facility-use").mock(
        return_value=httpx.Response(
            200,
            json={"identifier": "item-1", "orderedItem": {"reservationIdentifier": "res-1"}},
        )
    )
    confirm_route = respx.patch("https://legend.test/api/orders/order-1").mock(
        return_value=httpx.Response(200, json={"identifier": "order-1", "orderStatus": "Active"})
    )

    monkeypatch.setattr(sessions, "_select_slot", lambda rows, *, wide=False: 0)
    monkeypatch.setattr(sessions, "_select_courts", lambda detail: ["court-1"])
    monkeypatch.setattr(typer, "confirm", lambda *args, **kwargs: True)

    result = runner.invoke(app, ["--user", "sessions", "book", "--days", "1"])

    assert result.exit_code == 0, result.output
    assert "Booked" in result.output
    assert "res-1" in result.output
    assert order_route.called
    assert confirm_route.called
    assert json.loads(add_route.calls[0].request.content) == {
        "slot": {"identifier": "slot-1", "facility": [{"identifier": "court-1"}]}
    }


@respx.mock
def test_sessions_book_aborts_when_confirmation_declined(monkeypatch) -> None:
    _configure_env(monkeypatch, with_user=True)
    _mock_slot_listing()
    respx.get("https://legend.test/api/facility-uses/slots/slot-1").mock(
        return_value=httpx.Response(200, json={"identifier": "slot-1"})
    )
    order_route = respx.post("https://legend.test/api/orders").mock(
        return_value=httpx.Response(200, json={"identifier": "order-1"})
    )

    monkeypatch.setattr(sessions, "_select_slot", lambda rows, *, wide=False: 0)
    monkeypatch.setattr(sessions, "_select_courts", lambda detail: None)
    monkeypatch.setattr(typer, "confirm", lambda *args, **kwargs: False)

    result = runner.invoke(app, ["--user", "sessions", "book", "--days", "1"])

    assert result.exit_code == 0, result.output
    assert "cancelled" in result.output.lower()
    assert not order_route.called
