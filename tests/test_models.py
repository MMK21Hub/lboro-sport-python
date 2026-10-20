from datetime import UTC, datetime
from decimal import Decimal

from lboro_sport_python.models import (
    ActivityListResponse,
    AvailabilityResponse,
    Facility,
    Slot,
    parse_api_datetime,
    parse_iso_duration,
)


def test_parse_api_datetime_accepts_offsets_and_z() -> None:
    expected = datetime(2026, 10, 19, 18, tzinfo=UTC)
    assert parse_api_datetime("2026-10-19T18:00:00+0000") == expected
    assert parse_api_datetime("2026-10-19T18:00:00Z") == expected
    assert parse_api_datetime("2026-10-19T18:00:00") == expected
    assert parse_api_datetime("not-a-date") is None


def test_parse_iso_duration() -> None:
    assert parse_iso_duration("PT60M").total_seconds() == 3600  # type: ignore
    assert parse_iso_duration("PT1H30M").total_seconds() == 5400  # type: ignore
    assert parse_iso_duration("P1DT2H").total_seconds() == 93600  # type: ignore
    assert parse_iso_duration("nonsense") is None
    assert parse_iso_duration(None) is None


def test_slot_computes_end_time_and_price() -> None:
    slot = Slot.model_validate(
        {
            "identifier": "slot-1",
            "startDate": "2026-10-19T18:00:00+0000",
            "duration": "PT60M",
            "remainingUses": 3,
            "maximumUses": 6,
            "offer": [{"price": 8.5, "priceCurrency": "GBP"}],
        }
    )
    assert slot.end_time == datetime(2026, 10, 19, 19, tzinfo=UTC)
    assert slot.price == Decimal("8.5")
    assert slot.currency == "GBP"
    assert slot.spaces_left == 3


def test_activity_list_response() -> None:
    response = ActivityListResponse.model_validate(
        {
            "concepts": [
                {
                    "identifier": "badminton",
                    "prefLabel": "Badminton",
                    "locations": [
                        {
                            "identifier": "site-1",
                            "name": "Sports Centre",
                            "timeZone": "Europe/London",
                        }
                    ],
                    "broader": [{"identifier": "racket", "prefLabel": "Racket Sports"}],
                }
            ]
        }
    )
    concept = response.concepts[0]
    assert concept.label == "Badminton"
    assert concept.locations[0].label == "Sports Centre"
    assert concept.broader[0].label == "Racket Sports"


def test_availability_response_parses_slots() -> None:
    response = AvailabilityResponse.model_validate(
        {
            "currentPage": 1,
            "totalRecords": 1,
            "items": [
                {
                    "activity": [{"identifier": "badminton", "prefLabel": "Badminton"}],
                    "location": {
                        "identifier": "site-1",
                        "name": "Sports Centre",
                        "timeZone": "Europe/London",
                    },
                    "slots": [
                        {
                            "identifier": "s1",
                            "startDate": "2026-10-19T18:00:00+0000",
                            "duration": "PT60M",
                            "remainingUses": 2,
                        }
                    ],
                }
            ],
        }
    )
    assert response.total_records == 1
    slot = response.items[0].slots[0]
    assert slot.identifier == "s1"
    assert slot.end_time is not None
    assert slot.end_time.hour == 19


def test_facility_unwraps_nested_array() -> None:
    facility = Facility.model_validate({"facility": [{"identifier": "court-1", "name": "Court 1"}]})
    assert facility.identifier == "court-1"
    assert facility.label == "Court 1"
