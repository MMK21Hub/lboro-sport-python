# lboro-sport-python

A small, typed Python client for the facility-hire API used by the Lboro Sport
Android app (`com.innovatise.loughboroughuniversity`).

The app talks to the **Legend** booking platform over a hand-rolled OkHttp
client. Facility hire (for example badminton courts) is the `LEGEND_ACTIVITY`
module and uses these REST endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/facility-uses/activitylist` | Activities and their locations |
| `POST` | `/facility-uses` | Search available slots |
| `GET` | `/facility-uses/slots/{slotId}` | Slot detail and court availability |
| `POST` | `/orders` | Create/get the basket |
| `POST` | `/orders/{orderId}/orderitems/facility-use` | Add a court to the basket |
| `PATCH` | `/orders/{orderId}` | Confirm the booking (`orderStatus: Active`) |
| `GET` | `/contacts/current/reservations` | List bookings |
| `PATCH` | `/contacts/current/reservations/{id}` | Cancel a booking |

Write operations require a member token. Availability search works with the
public `client_credentials` token, which is what the sessions script uses.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
cd lboro_sport_python
uv sync
```

## Configure

### Automatic bootstrap (recommended)

```bash
uv run bootstrap_config.py --activity badminton
```

The script mirrors the Android app's startup handshake: it obtains a public
front-desk token, downloads `/proxy/mob/appConfig/`, finds the club, creates an
anonymous app installation/user, walks the app's `Book` screens until it finds
the Legend facility-hire module, decrypts `nativeClientId`/`nativeSecretKey`
with the theme key, and writes `.env`.

It prints the club/module it selected and verifies the credentials against the
Legend token endpoint and activity list. Useful options: `--club-id`,
`--module-id`, `--activity`, `--output`, `--json`, `--no-verify`, `--force`.
The same command is installed as `lboro-bootstrap`.

If the backend changes and the home-screen walk fails, save a screen response
and parse it offline:

```bash
uv run bootstrap_config.py --module-config screen.json --activity badminton
```

`.env` is written with mode 600 and is gitignored. Do not commit it.

The defaults target app id `516`, version name `110.60`, and build `705800`.
If the app updates, pass `--build` and `--version-name` from the new APK.

### Manual configuration

You can also copy `.env.example` to `.env` and fill it in yourself. The Android
app stores the downloaded values in `com.myfitapp.config.xml`; on a rooted
device or emulator:

```bash
adb shell su -c 'cat /data/data/com.innovatise.loughboroughuniversity/shared_prefs/com.myfitapp.config.xml'
```

The keys are `SHARED_PREFERENCE_LEGEND_API`,
`SHARED_PREFERENCE_LEGEND_OAUTH_ACCESSTOKEN_URL`,
`SHARED_PREFERENCE_LEGEND_CLIENT_KEY`, and
`SHARED_PREFERENCE_LEGEND_SECRET_KEY` (the client key/secret are decrypted by
the app before they are used).

## Get available sessions

```bash
# What activities does the API expose?
uv run get_available_sessions.py --list-activities

# Badminton for the next 7 days
uv run get_available_sessions.py --activity badminton --days 7

# A specific day and venue
uv run get_available_sessions.py --activity badminton --date 2026-10-24 --site "Sports Centre"

# Machine-readable output
uv run get_available_sessions.py --activity badminton --days 3 --json

# Full detail under each slot: site, slot ID, and courts
uv run get_available_sessions.py --activity badminton --days 1 --wide
```

The default view is grouped by day and shows only what matters for scanning:
time, venue, spaces left, and price. The repeated activity prefix and site move
into the heading; slot IDs stay behind `--wide`.

The same command is installed as the `lboro-sessions` console script:

```bash
uv run lboro-sessions --activity badminton --days 7
```

Useful options: `--activity-id`, `--site-id`, `--include-full`,
`--wide`, `--page-size`, `--json`, `--list-activities`.

By default the script uses the public OAuth token. If a member token is needed
for the club's configuration, pass `--user` after setting
`LBORO_USERNAME`/`LBORO_PASSWORD`.

## Python usage

```python
from datetime import datetime, time, timedelta, timezone

from lboro_sport_python import LegendClient

with LegendClient(
    base_url="https://legend.example.com/api",
    token_url="https://legend.example.com/oauth/token",
    client_id="...",
    client_secret="...",
) as client:
    concepts = client.list_activities()
    badminton = next(c for c in concepts if c.label.lower() == "badminton")
    site_ids = [location.identifier for location in badminton.locations]

    today = datetime.now(timezone.utc).date()
    response = client.search_facility_uses(
        location_ids=site_ids,
        activity_ids=[badminton.identifier],
        date_from=datetime.combine(today, time.min, tzinfo=timezone.utc),
        date_end=datetime.combine(today, time.max, tzinfo=timezone.utc),
    )

    for item in response.items:
        for slot in item.slots:
            print(slot.identifier, slot.start_date, slot.remaining_uses, slot.price)
```

Booking helpers (member token required):

```python
client.login("member@example.com", "password")

order = client.create_order()
item = client.add_facility_use(order.identifier, slot.identifier, ["court-id"])
client.confirm_order(order.identifier)
```

## Notes

- Dates are sent as `yyyy-MM-dd'T'HH:mm:ssZ` in UTC, matching the app.
- Slot durations are ISO-8601 (`PT60M`); `Slot.end_time` computes the end.
- `facilityAvailability` entries with `identifier == "0"` mean "Any court".
- Request bodies and paths mirror the decompiled app; see
  `decompiled/java/sources/gh/` in this repository for the originals.
- This client is unaffiliated with Loughborough University or Innovatise. Use
  your own account, follow the facility's terms, and do not hammer the API.

## Test

```bash
uv run pytest
uv run ruff check .
```
