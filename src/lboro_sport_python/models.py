"""Pydantic models for the Legend facility-hire API.

The Android app parses these payloads by hand from ``org.json.JSONObject``;
the aliases below match the JSON keys it reads.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

_DATETIME_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z")

_DURATION_RE = re.compile(
    r"^P(?:(?P<weeks>\d+(?:\.\d+)?)W)?"
    r"(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def parse_api_datetime(value: Any) -> datetime | None:
    """Parse the API's ``yyyy-MM-dd'T'HH:mm:ssZ`` timestamps.

    Accepts ``Z`` as well as ``+0000`` and assumes UTC when no offset is given.
    Returns ``None`` for empty or unrecognised values.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+0000"
    for fmt in _DATETIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def format_api_datetime(value: datetime) -> str:
    """Format a datetime the way the Android app sends it (UTC, no colon)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S%z")


def parse_iso_duration(value: str | None) -> timedelta | None:
    """Parse an ISO-8601 duration such as ``PT60M`` or ``P1DT2H``."""
    if not value:
        return None
    match = _DURATION_RE.match(value.strip())
    if not match:
        return None
    parts = {key: float(raw) for key, raw in match.groupdict().items() if raw is not None}
    return timedelta(
        weeks=parts.get("weeks", 0.0),
        days=parts.get("days", 0.0),
        hours=parts.get("hours", 0.0),
        minutes=parts.get("minutes", 0.0),
        seconds=parts.get("seconds", 0.0),
    )


ApiDatetime = Annotated[datetime | None, BeforeValidator(parse_api_datetime)]


class LegendModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class Offer(LegendModel):
    name: str | None = None
    description: str | None = None
    price: Decimal | None = None
    value: Decimal | None = None
    original_value: Decimal | None = Field(default=None, alias="originalValue")
    price_currency: str | None = Field(default=None, alias="priceCurrency")
    currency: str | None = None
    spaces: int | None = None


class Location(LegendModel):
    identifier: str
    name: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    time_zone: str | None = Field(default=None, alias="timeZone")

    @property
    def label(self) -> str:
        return self.name or self.display_name or self.identifier


class Activity(LegendModel):
    identifier: str
    pref_label: str | None = Field(default=None, alias="prefLabel")
    name: str | None = None

    @property
    def label(self) -> str:
        return self.pref_label or self.name or self.identifier


class ActivityConcept(Activity):
    locations: list[Location] = Field(default_factory=list)
    broader: list[Activity] = Field(default_factory=list)


class Facility(LegendModel):
    identifier: str
    name: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    is_available: bool | None = Field(default=None, alias="isAvailable")

    @model_validator(mode="before")
    @classmethod
    def _unwrap_facility_array(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("identifier"):
            facilities = data.get("facility")
            if isinstance(facilities, list) and facilities and isinstance(facilities[0], dict):
                return {**data, **facilities[0]}
        return data

    @property
    def label(self) -> str:
        return self.name or self.display_name or self.identifier


class Slot(LegendModel):
    identifier: str
    start_date: ApiDatetime = Field(default=None, alias="startDate")
    end_date: ApiDatetime = Field(default=None, alias="endDate")
    duration: str | None = None
    remaining_uses: int | None = Field(default=None, alias="remainingUses")
    maximum_uses: int | None = Field(default=None, alias="maximumUses")
    offer: list[Offer] = Field(default_factory=list)
    facility_availability: list[Facility] = Field(
        default_factory=list, alias="facilityAvailability"
    )
    allow_facility_selection: bool | None = Field(default=None, alias="allowFacilitySelection")

    @property
    def end_time(self) -> datetime | None:
        if self.end_date is not None:
            return self.end_date
        if self.start_date is None:
            return None
        delta = parse_iso_duration(self.duration)
        return self.start_date + delta if delta is not None else None

    @property
    def spaces_left(self) -> int | None:
        return self.remaining_uses

    @property
    def price(self) -> Decimal | None:
        if not self.offer:
            return None
        offer = self.offer[0]
        return offer.price if offer.price is not None else offer.value

    @property
    def currency(self) -> str | None:
        if not self.offer:
            return None
        offer = self.offer[0]
        return offer.price_currency or offer.currency


class SlotDetail(Slot):
    title: str | None = Field(default=None, alias="name")
    activity: list[Activity] = Field(default_factory=list)
    location: Location | None = None
    facility: list[Facility] = Field(default_factory=list)


class AvailabilityItem(LegendModel):
    activity: list[Activity] = Field(default_factory=list)
    location: Location | None = None
    slots: list[Slot] = Field(default_factory=list)


class AvailabilityResponse(LegendModel):
    current_page: int | None = Field(default=None, alias="currentPage")
    total_records: int | None = Field(default=None, alias="totalRecords")
    items: list[AvailabilityItem] = Field(default_factory=list)


class ActivityListResponse(LegendModel):
    concepts: list[ActivityConcept] = Field(default_factory=list)


class OrderedItem(LegendModel):
    identifier: str | None = None
    name: str | None = None
    item_type: str | None = Field(default=None, alias="itemType")
    reservation_identifier: str | None = Field(default=None, alias="reservationIdentifier")
    start_date: ApiDatetime = Field(default=None, alias="startDate")


class OrderItem(LegendModel):
    identifier: str
    order_quantity: int | None = Field(default=None, alias="orderQuantity")
    amount: Offer | None = None
    ordered_item: OrderedItem | None = Field(default=None, alias="orderedItem")


class Order(LegendModel):
    identifier: str
    order_status: str | None = Field(default=None, alias="orderStatus")
    order_items: list[OrderItem] = Field(default_factory=list, alias="orderItem")


class Reservation(LegendModel):
    identifier: str
    reservation_status: str | None = Field(default=None, alias="reservationStatus")
    reservation_for: SlotDetail | None = Field(default=None, alias="reservationFor")
    active_order_identifier: str | None = Field(default=None, alias="activeOrderIdentifier")
    potential_action: list[str] = Field(default_factory=list, alias="potentialAction")


class ReservationListResponse(LegendModel):
    data: list[Reservation] = Field(default_factory=list)
