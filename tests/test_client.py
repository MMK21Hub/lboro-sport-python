import base64
import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from lboro_sport_python.client import LegendAPIError, LegendAuthError, LegendClient

BASE_URL = "https://legend.test/api"
TOKEN_URL = "https://legend.test/oauth/token"


def make_client(transport: httpx.BaseTransport | None = None) -> LegendClient:
    return LegendClient(
        base_url=BASE_URL,
        token_url=TOKEN_URL,
        client_id="client-id",
        client_secret="client-secret",
        transport=transport,
    )


@respx.mock
def test_list_activities_requests_a_token_and_sends_bearer() -> None:
    token_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "abc", "expires_in": 3600})
    )
    activities_route = respx.get(f"{BASE_URL}/facility-uses/activitylist").mock(
        return_value=httpx.Response(
            200,
            json={
                "concepts": [
                    {
                        "identifier": "badminton",
                        "prefLabel": "Badminton",
                        "locations": [{"identifier": "site-1"}],
                    }
                ]
            },
        )
    )

    with make_client() as client:
        concepts = client.list_activities()

    assert [concept.identifier for concept in concepts] == ["badminton"]
    assert token_route.call_count == 1
    request = activities_route.calls[0].request
    assert request.headers["Authorization"] == "Bearer abc"
    assert request.headers["Accept"] == "application/json"

    form = token_route.calls[0].request.content.decode()
    assert "Grant_Type=client_credentials" in form
    assert "scope=api.public" in form
    assert "client_id=client-id" in form
    assert "client_secret=client-secret" in form


@respx.mock
def test_search_facility_uses_posts_the_expected_body() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "abc", "expires_in": 3600})
    )
    search_route = respx.post(f"{BASE_URL}/facility-uses").mock(
        return_value=httpx.Response(200, json={"items": []})
    )

    with make_client() as client:
        response = client.search_facility_uses(
            location_ids=["site-1"],
            activity_ids=["badminton"],
            date_from=datetime(2026, 10, 19, tzinfo=UTC),
            date_end=datetime(2026, 10, 19, 23, 59, 59, tzinfo=UTC),
        )

    assert response.items == []
    body = json.loads(search_route.calls[0].request.content)
    assert body == {
        "locationIds": ["site-1"],
        "activityIds": ["badminton"],
        "dateFrom": "2026-10-19T00:00:00+0000",
        "dateEnd": "2026-10-19T23:59:59+0000",
        "pageSize": 100,
        "pageNo": 1,
    }


def test_user_endpoint_requires_login() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    with make_client(transport) as client, pytest.raises(LegendAuthError):
        client.create_order()


@respx.mock
def test_api_errors_are_mapped() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "abc", "expires_in": 3600})
    )
    respx.get(f"{BASE_URL}/facility-uses/activitylist").mock(
        return_value=httpx.Response(
            400,
            json={"code": "BadRequest", "title": "Invalid", "userMessage": "Nope"},
        )
    )

    with make_client() as client, pytest.raises(LegendAPIError) as exc_info:
        client.list_activities()

    assert exc_info.value.status_code == 400
    assert "Invalid" in str(exc_info.value)
    assert "Nope" in str(exc_info.value)


@respx.mock
def test_add_facility_use_sends_slot_and_facilities() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "user-token", "expires_in": 3600})
    )
    item_route = respx.post(f"{BASE_URL}/orders/order-1/orderitems/facility-use").mock(
        return_value=httpx.Response(
            200,
            json={
                "identifier": "item-1",
                "orderedItem": {"reservationIdentifier": "res-1"},
            },
        )
    )

    with make_client() as client:
        client.login("member@example.com", "password")
        item = client.add_facility_use("order-1", "slot-1", ["court-1", "court-2"])

    assert item.identifier == "item-1"
    assert item.ordered_item is not None
    assert item.ordered_item.reservation_identifier == "res-1"
    assert item_route.calls[0].request.headers["Authorization"] == "Bearer user-token"
    assert json.loads(item_route.calls[0].request.content) == {
        "slot": {
            "identifier": "slot-1",
            "facility": [{"identifier": "court-1"}, {"identifier": "court-2"}],
        }
    }


@respx.mock
def test_cancel_reservation_sends_patch_body() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "user-token", "expires_in": 3600})
    )
    cancel_route = respx.patch(f"{BASE_URL}/contacts/current/reservations/res-1").mock(
        return_value=httpx.Response(200, json={"identifier": "res-1"})
    )

    with make_client() as client:
        client.login("member@example.com", "password")
        reservation = client.cancel_reservation("res-1")

    assert reservation.identifier == "res-1"
    assert json.loads(cancel_route.calls[0].request.content) == {"reservationStatus": "Cancelled"}


@respx.mock
def test_jwt_expiry_is_used_when_expires_in_is_missing() -> None:
    token = _fake_jwt(exp=4_000_000_000)
    token_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": token})
    )
    respx.get(f"{BASE_URL}/facility-uses/activitylist").mock(
        return_value=httpx.Response(200, json={"concepts": []})
    )

    with make_client() as client:
        client.list_activities()
        client.list_activities()

    assert token_route.call_count == 1


def _fake_jwt(*, exp: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJub25lIn0.{payload}.signature"
