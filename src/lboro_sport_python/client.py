"""Synchronous client for the Legend facility-hire REST API."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from .models import (
    ActivityConcept,
    ActivityListResponse,
    AvailabilityResponse,
    Order,
    OrderItem,
    Reservation,
    ReservationListResponse,
    SlotDetail,
    format_api_datetime,
)


class LegendError(Exception):
    """Base class for errors raised by this package."""


class LegendAuthError(LegendError):
    """Raised when an endpoint needs credentials that were not supplied."""


class LegendAPIError(LegendError):
    """Raised for non-2xx responses from the Legend API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Mapping[str, Any] | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = dict(payload or {})
        self.body = body

    @classmethod
    def from_response(cls, response: httpx.Response) -> LegendAPIError:
        payload: dict[str, Any] = {}
        try:
            decoded = response.json()
            if isinstance(decoded, dict):
                payload = decoded
        except ValueError:
            pass

        title = (
            payload.get("title")
            or payload.get("errorTitle")
            or payload.get("error")
            or response.reason_phrase
        )
        detail = (
            payload.get("userMessage")
            or payload.get("errorDescription")
            or payload.get("developerMessage")
        )
        pieces = [f"HTTP {response.status_code}"]
        if title:
            pieces.append(str(title))
        if detail and str(detail) != str(title):
            pieces.append(str(detail))
        return cls(
            ": ".join(pieces),
            status_code=response.status_code,
            payload=payload,
            body=response.text,
        )


class LegendClient:
    """Small synchronous client for facility-hire bookings."""

    def __init__(
        self,
        base_url: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 20.0,
        user_agent: str = "lboro_sport_python/0.1",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url or not token_url:
            raise ValueError("base_url and token_url are required")
        self.base_url = base_url.rstrip("/")
        self.token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": user_agent},
        )
        self._access_token: str | None = None
        self._token_kind: str | None = None
        self._expires_at: datetime | None = None
        self._username: str | None = None
        self._password: str | None = None

    def __enter__(self) -> LegendClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    @property
    def access_token(self) -> str | None:
        """The current bearer token, if one has been obtained."""
        return self._access_token

    @property
    def token_kind(self) -> str | None:
        """``"client"`` or ``"user"``, depending on the current token."""
        return self._token_kind

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate_client_credentials(self) -> None:
        """Obtain a public token via the OAuth2 client-credentials grant."""
        self._request_token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "api.public",
                "Grant_Type": "client_credentials",
            },
            kind="client",
        )

    def login(self, username: str, password: str) -> None:
        """Obtain a member token via the OAuth2 password grant.

        The credentials are kept in memory so the token can be refreshed
        automatically when it expires.
        """
        self._username = username
        self._password = password
        self.authenticate_user(username, password)

    def authenticate_user(self, username: str, password: str) -> None:
        """Obtain a member token without remembering the credentials."""
        self._request_token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "api.public",
                "Grant_Type": "password",
                "username": username,
                "password": password,
            },
            kind="user",
        )

    def logout(self) -> None:
        """Forget the current token and stored credentials."""
        self._access_token = None
        self._token_kind = None
        self._expires_at = None
        self._username = None
        self._password = None

    def _request_token(self, data: Mapping[str, str], *, kind: str) -> None:
        response = self._http.post(self.token_url, data=dict(data))
        if response.is_error:
            raise LegendAPIError.from_response(response)

        try:
            payload = response.json()
        except ValueError as exc:
            raise LegendAPIError(
                "OAuth token endpoint returned a non-JSON response",
                status_code=response.status_code,
                body=response.text,
            ) from exc

        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise LegendAPIError(
                "OAuth token response did not contain access_token",
                status_code=response.status_code,
                payload=payload if isinstance(payload, dict) else None,
                body=response.text,
            )

        self._access_token = str(payload["access_token"])
        self._token_kind = kind
        self._expires_at = _token_expiry(self._access_token, payload.get("expires_in"))

    def _ensure_token(self, *, require_user: bool) -> None:
        if self._access_token and not self._token_expired():
            if require_user and self._token_kind != "user":
                raise LegendAuthError("This endpoint requires a member token; call login() first.")
            return

        if require_user:
            if not (self._username and self._password):
                raise LegendAuthError("This endpoint requires a member token; call login() first.")
            self.authenticate_user(self._username, self._password)
            return

        if self._username and self._password:
            self.authenticate_user(self._username, self._password)
        else:
            self.authenticate_client_credentials()

    def _token_expired(self) -> bool:
        if self._expires_at is None:
            return False
        return datetime.now(UTC) >= self._expires_at - timedelta(seconds=30)

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    def _auth_headers(self) -> dict[str, str]:
        if self._access_token is None:
            return {}
        return {"Authorization": f"Bearer {self._access_token}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        require_user: bool = False,
    ) -> Any:
        self._ensure_token(require_user=require_user)
        url = self._url(path)
        response = self._http.request(
            method, url, params=params, json=json_body, headers=self._auth_headers()
        )

        if response.status_code == 401 and self._username and self._password:
            self.authenticate_user(self._username, self._password)
            response = self._http.request(
                method, url, params=params, json=json_body, headers=self._auth_headers()
            )

        if response.is_error:
            raise LegendAPIError.from_response(response)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise LegendAPIError(
                "API returned a non-JSON response",
                status_code=response.status_code,
                body=response.text,
            ) from exc

    # ------------------------------------------------------------------
    # Facility hire
    # ------------------------------------------------------------------

    def list_activities(self) -> list[ActivityConcept]:
        """Return the activities (for example Badminton) and their locations."""
        payload = self._request("GET", "/facility-uses/activitylist")
        return ActivityListResponse.model_validate(payload).concepts

    def search_facility_uses(
        self,
        *,
        location_ids: Sequence[str],
        activity_ids: Sequence[str],
        date_from: datetime,
        date_end: datetime,
        page: int = 1,
        page_size: int = 100,
    ) -> AvailabilityResponse:
        """Search available facility-use slots for a date range."""
        payload = self._request(
            "POST",
            "/facility-uses",
            json_body={
                "locationIds": list(location_ids),
                "activityIds": list(activity_ids),
                "dateFrom": format_api_datetime(date_from),
                "dateEnd": format_api_datetime(date_end),
                "pageSize": page_size,
                "pageNo": page,
            },
        )
        return AvailabilityResponse.model_validate(payload)

    def get_slot(self, slot_id: str) -> SlotDetail:
        """Return a slot's detail, including selectable courts."""
        payload = self._request("GET", f"/facility-uses/slots/{quote(slot_id, safe='')}")
        return SlotDetail.model_validate(payload)

    def create_order(self) -> Order:
        """Create (or return) the member's active basket."""
        payload = self._request("POST", "/orders", json_body={}, require_user=True)
        return Order.model_validate(payload)

    def add_facility_use(
        self,
        order_id: str,
        slot_id: str,
        facility_ids: Sequence[str] | None = None,
    ) -> OrderItem:
        """Add a facility slot to the basket, optionally choosing courts."""
        slot: dict[str, Any] = {"identifier": slot_id}
        if facility_ids:
            slot["facility"] = [{"identifier": facility_id} for facility_id in facility_ids]
        payload = self._request(
            "POST",
            f"/orders/{quote(order_id, safe='')}/orderitems/facility-use",
            json_body={"slot": slot},
            require_user=True,
        )
        return OrderItem.model_validate(payload)

    def confirm_order(self, order_id: str) -> Order:
        """Confirm the basket, turning it into a booking."""
        payload = self._request(
            "PATCH",
            f"/orders/{quote(order_id, safe='')}",
            json_body={"orderStatus": "Active"},
            require_user=True,
        )
        return Order.model_validate(payload)

    # ------------------------------------------------------------------
    # Reservations
    # ------------------------------------------------------------------

    def list_reservations(
        self,
        *,
        start: datetime,
        end: datetime,
        page_size: int = 100,
    ) -> list[Reservation]:
        """Return the member's reservations in a date range."""
        payload = self._request(
            "GET",
            "/contacts/current/reservations",
            params={
                "startDateTime": format_api_datetime(start),
                "endDateTime": format_api_datetime(end),
                "pageSize": page_size,
            },
            require_user=True,
        )
        return ReservationListResponse.model_validate(payload).data

    def cancel_reservation(self, reservation_id: str) -> Reservation:
        """Cancel a reservation."""
        payload = self._request(
            "PATCH",
            f"/contacts/current/reservations/{quote(reservation_id, safe='')}",
            json_body={"reservationStatus": "Cancelled"},
            require_user=True,
        )
        return Reservation.model_validate(payload)


def _token_expiry(token: str, expires_in: Any) -> datetime | None:
    try:
        seconds = float(expires_in)
    except (TypeError, ValueError):
        seconds = None
    if seconds and seconds > 0:
        return datetime.now(UTC) + timedelta(seconds=seconds)
    return _jwt_expiry(token)


def _jwt_expiry(token: str) -> datetime | None:
    try:
        payload = token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        exp = claims.get("exp")
        if exp is None:
            return None
        return datetime.fromtimestamp(int(exp), tz=UTC)
    except (IndexError, TypeError, ValueError, binascii.Error):
        return None
