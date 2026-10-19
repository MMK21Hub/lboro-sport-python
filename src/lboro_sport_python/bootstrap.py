"""Bootstrap the Legend facility-hire configuration from the Lboro Sport backend.

This mirrors the Android app's startup handshake:

1. obtain a public front-desk token (``/auth/anonymous``),
2. download the app configuration (``/proxy/mob/appConfig/``),
3. discover the club (``/proxy/json/klubSearch/``),
4. create a serverless identity, app installation and anonymous user,
5. walk the app's home screens until a Legend facility-hire module is found,
6. decrypt the module's ``nativeClientId`` / ``nativeSecretKey``.

The module config is served by the MyFitApp front desk, not by Legend itself.
The script writes the decrypted Legend values to ``.env``.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import hmac
import json
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pydantic import BaseModel, ConfigDict, Field, field_validator

APP_ID = "516"
VERSION_NAME = "110.60"
BUILD = "705800"
APP_IDENTIFIER = "loughboroughuniversitysport"
FRONTDESK_URL = "https://frontdesk.myfitapp.de/api/v1"
CLUB_SEARCH_TERM = "Loughborough"
MODULE_TYPE_LEGEND_ACTIVITY = 38
MODULE_TYPE_SCREEN = 51

# Obfuscated app keys from com/innovatise/utils/{q0,o0}.java.
_Q0_PRODUCTION = ("JfVh", "xksE", "AqSb", "r9iW")
_O0_PRODUCTION = ("UCFH", "ycLV", "JXdD", "CahY")
_Q0_QA = ("uvFr", "tUje", "Qx51", "OCUm")
_O0_QA = ("yq2j", "GqSE", "uRY5", "NFgO")

# com/innovatise/views/{h,g}.java, used to derive the theme AES key.
_H_VALUES = ("7h", "dK", "cO", "Em")
_G_VALUES = ("RE", "BL", "YE", "CY")

_PRIMARY_SCREEN_WORDS = ("facility", "hire", "court")


Progress = Callable[[str], None]


class BootstrapError(Exception):
    """Base class for bootstrap failures."""


class BootstrapHTTPError(BootstrapError):
    """Raised for a non-2xx response from the MyFitApp backend."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.body = body

    @classmethod
    def from_response(cls, response: httpx.Response) -> BootstrapHTTPError:
        payload: dict[str, Any] = {}
        try:
            decoded = response.json()
            if isinstance(decoded, dict):
                payload = decoded
        except ValueError:
            pass
        detail = (
            payload.get("userMessage")
            or payload.get("message")
            or payload.get("error")
            or payload.get("retDesc")
            or payload.get("description")
            or payload.get("title")
            or response.reason_phrase
        )
        message = f"HTTP {response.status_code}"
        if detail:
            message = f"{message}: {detail}"
        return cls(
            message,
            status_code=response.status_code,
            url=str(response.url),
            body=response.text,
        )


class LegendConfig(BaseModel):
    """The values needed by :class:`lboro_sport_python.LegendClient`."""

    model_config = ConfigDict(extra="ignore")

    api_url: str
    token_url: str
    client_id: str
    client_secret: str
    authorize_url: str | None = None
    identity_provider_id: int | None = None
    module_id: int | None = None
    module_name: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    is_web_payment: bool | None = None

    @field_validator("filters", mode="before")
    @classmethod
    def _parse_filters(cls, value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return value if isinstance(value, dict) else {}


@dataclass(slots=True)
class BootstrapResult:
    app_config: dict[str, Any]
    theme_id: int
    theme_key: str
    club: dict[str, Any]
    module: dict[str, Any]
    legend: LegendConfig
    installation_id: str | None = None
    user_id: str | None = None
    screens_visited: int = 0
    raw_home: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Key derivation and decryption
# ---------------------------------------------------------------------------


def anon_hmac_key(*, qa: bool = False) -> str:
    """Return the key used for the ``appKeyHash`` HMAC.

    The Android app builds this by interleaving two obfuscated enum value
    lists; QA and production use different lists.
    """
    q0 = _Q0_QA if qa else _Q0_PRODUCTION
    o0 = _O0_QA if qa else _O0_PRODUCTION
    return "".join(pair for group in zip(q0, o0, strict=True) for pair in group)


def app_theme_aes_key() -> bytes:
    """Return the AES key used to protect the theme blob."""
    value = "".join(item[::-1] for item in _H_VALUES) + "".join(_G_VALUES)
    return hashlib.md5(value.encode()).hexdigest().encode()


def _decrypt_ecb_pkcs5(key: bytes, data: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def derive_theme_key(theme_id: int, directions: Mapping[str, Any]) -> str:
    """Derive the per-theme AES key from ``directions.geojson``.

    ``com.innovatise.views.i.a(themeId)`` decodes the ``lang`` fields of the
    selected location into a ciphertext and AES-decrypts it with
    :func:`app_theme_aes_key`.
    """
    try:
        items = directions["locations"][str(theme_id)]
    except (KeyError, TypeError) as exc:
        raise BootstrapError(
            f"Theme id {theme_id} was not found in the bundled directions data"
        ) from exc

    ciphertext = bytearray()
    for item in items:
        segment = str(item["lang"]).split(".")[-1]
        length = int(segment[-1])
        ciphertext.append(int(segment[:length]) & 0xFF)
    try:
        return _decrypt_ecb_pkcs5(app_theme_aes_key(), bytes(ciphertext)).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise BootstrapError(f"Could not derive the theme key for theme id {theme_id}") from exc


def load_directions(path: Path | None = None) -> dict[str, Any]:
    """Load the bundled theme blob, or an explicit file for debugging."""
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    resource = files("lboro_sport_python").joinpath("data/directions.geojson")
    return json.loads(resource.read_text(encoding="utf-8"))


def theme_key_for(theme_id: int, directions_path: Path | None = None) -> str:
    return derive_theme_key(theme_id, load_directions(directions_path))


def decrypt_theme_value(theme_key: str, value: str) -> str:
    """Decrypt an app-config value; return it unchanged if it is not encrypted."""
    raw = "".join(str(value).split())
    try:
        decoded = base64.b64decode(raw, validate=True)
        plain = _decrypt_ecb_pkcs5(theme_key.encode("utf-8"), decoded)
        text = plain.decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return str(value)
    if text and all(character.isprintable() or character in "\r\n\t" for character in text):
        return text
    return str(value)


# ---------------------------------------------------------------------------
# AWS Signature Version 4
# ---------------------------------------------------------------------------


def sigv4_headers(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    access_key: str,
    secret_key: str,
    session_token: str,
    region: str,
    service: str = "execute-api",
    now: datetime | None = None,
) -> dict[str, str]:
    """Return headers for an AWS Signature Version 4 request."""
    parsed = urlsplit(url)
    now = now or datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = amz_date[:8]

    canonical_uri = quote(parsed.path or "/", safe="/-_.~")
    query_pairs = sorted(parse_qsl(parsed.query, keep_blank_values=True))
    canonical_query = "&".join(
        f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}" for key, value in query_pairs
    )

    signed_map = {
        key.lower(): str(value).strip()
        for key, value in headers.items()
        if key.lower() != "authorization"
    }
    signed_map["host"] = parsed.netloc
    signed_map["x-amz-date"] = amz_date
    if session_token:
        signed_map["x-amz-security-token"] = session_token

    signed_headers = ";".join(sorted(signed_map))
    canonical_headers = "".join(f"{key}:{signed_map[key]}\n" for key in sorted(signed_map))
    payload_hash = hashlib.sha256(body or b"").hexdigest()
    canonical_request = (
        f"{method}\n{canonical_uri}\n{canonical_query}\n"
        f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )
    signing_key = ("AWS4" + secret_key).encode()
    for part in (date_stamp, region, service, "aws4_request"):
        signing_key = hmac.new(signing_key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    result = dict(signed_map)
    result["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return result


# ---------------------------------------------------------------------------
# Module discovery
# ---------------------------------------------------------------------------


def iter_linked_modules(data: Any) -> Iterator[dict[str, Any]]:
    """Yield every ``linkedModule`` object in a home/screen response."""
    if isinstance(data, dict):
        linked = data.get("linkedModule")
        if isinstance(linked, dict):
            yield linked
        for value in data.values():
            yield from iter_linked_modules(value)
    elif isinstance(data, list):
        for value in data:
            yield from iter_linked_modules(value)


def _screen_priority(name: str) -> int:
    lowered = name.lower()
    if any(word in lowered for word in _PRIMARY_SCREEN_WORDS):
        return 0
    return 1 if "book" in lowered else 2


def find_legend_module(
    home: dict[str, Any],
    fetch_screen: Callable[[str], dict[str, Any]],
    activity: str = "badminton",
    *,
    max_screens: int = 100,
    progress: Progress | None = None,
) -> tuple[dict[str, Any], int]:
    """Walk type-51 screens until a type-38 Legend module matches ``activity``."""
    legend_modules: dict[Any, dict[str, Any]] = {}
    pending_screens: dict[str, str] = {}
    queue: deque[dict[str, Any]] = deque([home])
    screens_visited = 0
    needle = activity.strip().lower()

    while True:
        if queue:
            data = queue.popleft()
        elif pending_screens and screens_visited < max_screens:
            screen_id, screen_name = min(
                pending_screens.items(), key=lambda item: _screen_priority(item[1])
            )
            del pending_screens[screen_id]
            if progress is not None:
                progress(f"  fetching screen {screen_id} ({screen_name or 'unnamed'})")
            data = fetch_screen(screen_id)
            screens_visited += 1
        else:
            break

        for module in iter_linked_modules(data):
            module_type = module.get("type")
            if module_type == MODULE_TYPE_LEGEND_ACTIVITY:
                key = module.get("id") or (module.get("name"), module_type)
                legend_modules[key] = module
            elif module_type == MODULE_TYPE_SCREEN and module.get("screenId"):
                screen_id = str(module["screenId"])
                pending_screens.setdefault(screen_id, str(module.get("name") or ""))

        matches = [
            module
            for module in legend_modules.values()
            if needle in str(module.get("name") or "").lower()
        ]
        if matches:
            if progress is not None:
                progress(
                    f"  found Legend module: {matches[0].get('name')} ({matches[0].get('id')})"
                )
            return matches[0], screens_visited

    if not needle and legend_modules:
        return next(iter(legend_modules.values())), screens_visited

    available = ", ".join(sorted(str(module.get("name")) for module in legend_modules.values()))
    if available:
        raise BootstrapError(
            f"No facility-hire module matched {activity!r}. Available: {available}"
        )
    raise BootstrapError(
        "No Legend facility-hire module was found in the app's screens "
        f"(visited {screens_visited} screens)."
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class BootstrapClient:
    """Client for the MyFitApp front desk and serverless bootstrap APIs."""

    def __init__(
        self,
        *,
        frontdesk_url: str = FRONTDESK_URL,
        app_id: str = APP_ID,
        version_name: str = VERSION_NAME,
        build: str = BUILD,
        app_identifier: str = APP_IDENTIFIER,
        locale: str = "en_GB",
        region: str = "GB",
        device_model: str = "sdk_gphone64_x86_64",
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.frontdesk_url = frontdesk_url.rstrip("/")
        self.app_id = app_id
        self.version_name = version_name
        self.build = build
        self.app_identifier = app_identifier
        self.locale = locale
        self.region = region
        self.device_model = device_model
        self.device_id = str(uuid.uuid4())
        self._http = httpx.Client(
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    def __enter__(self) -> BootstrapClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- helpers ---------------------------------------------------------

    def _headers(
        self, token: str | None = None, installation_id: str | None = None
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": (f"{self.app_identifier}/{self.build} Android 16 {self.device_model}"),
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if installation_id and installation_id != "temp":
            headers["x-mfa-aid"] = installation_id
        return headers

    def _frontdesk_params(self, club_id: int, installation_id: str = "temp") -> dict[str, Any]:
        return {
            "c": club_id,
            "locale": self.locale,
            "uid": self.device_id,
            "app": self.app_id,
            "appId": self.app_id,
            "appid": self.app_id,
            "appInstallationId": installation_id,
            "appver": self.build,
        }

    def _serverless_headers(
        self, *, account_id: str | None = None, installation_id: str | None = None
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "locale": self.locale,
            "ostype": "ANDROID",
            "appid": self.app_id,
            "appver": self.build,
            "build": self.build,
            "region": self.region,
        }
        if account_id:
            headers["accountId"] = str(account_id)
        if installation_id:
            headers["x-mfa-aid"] = installation_id
        return headers

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise BootstrapHTTPError(
                "Response was not JSON",
                status_code=response.status_code,
                url=str(response.url),
                body=response.text,
            ) from exc

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.is_error:
            raise BootstrapHTTPError.from_response(response)

    # -- front desk ------------------------------------------------------

    def anonymous_token(
        self,
        club_id: int = 0,
        installation_id: str = "temp",
        *,
        qa: bool = False,
    ) -> str:
        timestamp = str(int(time.time() * 1000))
        request_id = str(uuid.uuid4())
        key = anon_hmac_key(qa=qa)
        app_key_hash = hmac.new(
            key.encode(), f"{timestamp}-{request_id}".encode(), hashlib.sha256
        ).hexdigest()
        payload = {
            "c": club_id,
            "locale": self.locale,
            "uid": self.device_id,
            "app": self.app_id,
            "appId": self.app_id,
            "appid": self.app_id,
            "appInstallationId": installation_id,
            "appver": self.build,
            "platform": "Android",
            "timeStamp": timestamp,
            "requestId": request_id,
            "appKeyHash": app_key_hash,
            "appVersion": self.version_name,
            "deviceInfo": "test",
            "deviceId": self.device_id,
        }
        response = self._http.post(
            f"{self.frontdesk_url}/auth/anonymous",
            json=payload,
            headers=self._headers(installation_id=installation_id),
        )
        self._check(response)
        data = self._json(response)
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise BootstrapError("Anonymous auth response did not contain a token")
        return str(token)

    def app_config(
        self, token: str, club_id: int = 0, installation_id: str = "temp"
    ) -> dict[str, Any]:
        response = self._http.get(
            f"{self.frontdesk_url}/proxy/mob/appConfig/",
            params=self._frontdesk_params(club_id, installation_id),
            headers=self._headers(token, installation_id),
        )
        self._check(response)
        data = self._json(response)
        if not isinstance(data, dict):
            raise BootstrapError("appConfig response was not an object")
        return data

    def search_clubs(
        self,
        token: str,
        term: str,
        club_id: int = 0,
        installation_id: str = "temp",
    ) -> list[dict[str, Any]]:
        response = self._http.get(
            f"{self.frontdesk_url}/proxy/json/klubSearch/{quote(term, safe='')}",
            params=self._frontdesk_params(club_id, installation_id),
            headers=self._headers(token, installation_id),
        )
        self._check(response)
        data = self._json(response)
        if not isinstance(data, dict):
            return []
        clubs = data.get("klubJSONDTOList") or data.get("data") or []
        return [club for club in clubs if isinstance(club, dict)]

    def club_info(self, token: str, club_id: int, installation_id: str = "temp") -> dict[str, Any]:
        response = self._http.get(
            f"{self.frontdesk_url}/proxy/club/info/{club_id}",
            params=self._frontdesk_params(club_id, installation_id),
            headers=self._headers(token, installation_id),
        )
        self._check(response)
        data = self._json(response)
        if isinstance(data, dict):
            club = data.get("data")
            return club if isinstance(club, dict) else data
        return {}

    def home_screen(self, token: str, club_id: int, installation_id: str) -> dict[str, Any]:
        params = self._frontdesk_params(club_id, installation_id)
        params.update({"w": "1080", "h": "1920", "topinset": "0"})
        response = self._http.get(
            f"{self.frontdesk_url}/proxy/mob/homeScreen/{club_id}",
            params=params,
            headers=self._headers(token, installation_id),
        )
        self._check(response)
        data = self._json(response)
        if not isinstance(data, dict):
            raise BootstrapError("homeScreen response was not an object")
        return data

    def screen(
        self,
        screen_id: str,
        token: str,
        club_id: int,
        installation_id: str,
    ) -> dict[str, Any]:
        params = self._frontdesk_params(club_id, installation_id)
        params.update({"w": "1080", "h": "1920", "topinset": "0"})
        response = self._http.get(
            f"{self.frontdesk_url}/proxy/mob/screen/{screen_id}",
            params=params,
            headers=self._headers(token, installation_id),
        )
        self._check(response)
        data = self._json(response)
        if not isinstance(data, dict):
            raise BootstrapError(f"screen {screen_id} response was not an object")
        return data

    def module_config(
        self,
        token: str,
        module_id: int | str,
        club_id: int,
        installation_id: str,
    ) -> dict[str, Any]:
        response = self._http.get(
            f"{self.frontdesk_url}/proxy/module/config/{module_id}",
            params=self._frontdesk_params(club_id, installation_id),
            headers=self._headers(token, installation_id),
        )
        self._check(response)
        data = self._json(response)
        if isinstance(data, dict):
            module = data.get("data")
            return module if isinstance(module, dict) else data
        raise BootstrapError(f"module {module_id} response was not an object")

    # -- serverless identity --------------------------------------------

    def serverless_oauth(self, api: Mapping[str, Any], theme_key: str) -> str:
        client_id = str(api.get("clientId") or "")
        client_secret = decrypt_theme_value(theme_key, str(api.get("clientSecret") or ""))
        auth_url = str(api.get("authUrl") or "")
        if not client_id or not client_secret or not auth_url:
            raise BootstrapError("appConfig did not contain serverless API credentials")
        response = self._http.post(
            auth_url,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
        )
        self._check(response)
        data = self._json(response)
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            raise BootstrapError("Serverless OAuth response did not contain access_token")
        return str(token)

    def dev_token(
        self,
        api: Mapping[str, Any],
        access_token: str,
        theme_key: str,
        *,
        identity: str = "unauthenticated",
    ) -> dict[str, Any]:
        api_url = str(api.get("url") or "").rstrip("/")
        if not api_url:
            raise BootstrapError("appConfig did not contain the serverless API URL")
        signature = hmac.new(theme_key.encode(), identity.encode(), hashlib.sha256).hexdigest()
        response = self._http.post(
            f"{api_url}/devToken",
            json={"signature": signature, "userIdentityId": identity},
            headers={
                "Authorization": access_token,
                "Content-Type": "application/json",
            },
        )
        self._check(response)
        data = self._json(response)
        if not isinstance(data, dict) or not data.get("token") or not data.get("identityId"):
            raise BootstrapError("devToken response was missing token/identityId")
        return data

    def cognito_credentials(
        self, region: str, identity_id: str, login_token: str
    ) -> dict[str, Any]:
        response = self._http.post(
            f"https://cognito-identity.{region}.amazonaws.com/",
            headers={
                "Content-Type": "application/x-amz-json-1.1",
                "X-Amz-Target": "AWSCognitoIdentityService.GetCredentialsForIdentity",
            },
            json={
                "IdentityId": identity_id,
                "Logins": {"cognito-identity.amazonaws.com": login_token},
            },
        )
        self._check(response)
        data = self._json(response)
        credentials = data.get("Credentials") if isinstance(data, dict) else None
        if not isinstance(credentials, dict):
            raise BootstrapError("Cognito did not return credentials")
        return credentials

    # -- serverless actions ---------------------------------------------

    def create_installation(
        self,
        api: Mapping[str, Any],
        credentials: Mapping[str, Any],
        club_id: int,
        *,
        account_id: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        private_url = str(api.get("privateUrl") or "").rstrip("/")
        if not private_url:
            raise BootstrapError("appConfig did not contain privateUrl")
        url = f"{private_url}/appInstallation"
        body = json.dumps(
            {
                "deviceName": self.device_model,
                "deviceModel": self.device_model,
                "os": "Android",
                "osVersion": "36",
                "locale": self.locale,
                "appId": int(self.app_id),
                "build": self.build,
                "appVersion": {"version": self.version_name, "build": self.build},
                "installed": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "pushEnabled": False,
                "clubs": [],
                "currentClub": {"id": str(club_id)},
            },
            separators=(",", ":"),
        ).encode()
        headers = self._serverless_headers(account_id=account_id)
        headers = sigv4_headers(
            "POST",
            url,
            headers,
            body,
            access_key=str(credentials["AccessKeyId"]),
            secret_key=str(credentials["SecretKey"]),
            session_token=str(credentials["SessionToken"]),
            region=region or self._identity_region(api),
        )
        response = self._http.post(url, headers=headers, content=body)
        self._check(response)
        data = self._json(response)
        if not isinstance(data, dict) or not data.get("id"):
            raise BootstrapError("appInstallation response did not contain an id")
        return data

    def create_user(
        self,
        api: Mapping[str, Any],
        credentials: Mapping[str, Any],
        installation_id: str,
        *,
        account_id: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        private_url = str(api.get("privateUrl") or "").rstrip("/")
        url = f"{private_url}/user"
        body = json.dumps(
            {
                "deviceName": self.device_model,
                "appInstallationId": installation_id,
                "appInstallations": [
                    {
                        "deviceName": self.device_model,
                        "appInstallationId": installation_id,
                    }
                ],
            },
            separators=(",", ":"),
        ).encode()
        headers = self._serverless_headers(account_id=account_id, installation_id=installation_id)
        headers = sigv4_headers(
            "POST",
            url,
            headers,
            body,
            access_key=str(credentials["AccessKeyId"]),
            secret_key=str(credentials["SecretKey"]),
            session_token=str(credentials["SessionToken"]),
            region=region or self._identity_region(api),
        )
        response = self._http.post(url, headers=headers, content=body)
        self._check(response)
        data = self._json(response)
        return data if isinstance(data, dict) else {}

    def user_clubs(
        self,
        api: Mapping[str, Any],
        credentials: Mapping[str, Any],
        user_id: str,
        installation_id: str,
        *,
        account_id: str | None = None,
        region: str | None = None,
    ) -> list[dict[str, Any]]:
        private_url = str(api.get("privateUrl") or "").rstrip("/")
        url = f"{private_url}/user/{quote(str(user_id), safe='')}/clubs"
        headers = self._serverless_headers(account_id=account_id, installation_id=installation_id)
        headers = sigv4_headers(
            "GET",
            url,
            headers,
            b"",
            access_key=str(credentials["AccessKeyId"]),
            secret_key=str(credentials["SecretKey"]),
            session_token=str(credentials["SessionToken"]),
            region=region or self._identity_region(api),
        )
        response = self._http.get(url, headers=headers)
        self._check(response)
        data = self._json(response)
        return [club for club in data if isinstance(club, dict)] if isinstance(data, list) else []

    @staticmethod
    def _identity_region(api: Mapping[str, Any]) -> str:
        region = str(api.get("identityRegion") or "")
        return region or "eu-central-1"

    # -- verification ----------------------------------------------------

    def verify_legend(self, config: LegendConfig) -> int:
        response = self._http.post(
            config.token_url,
            data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "scope": "api.public",
                "Grant_Type": "client_credentials",
            },
        )
        self._check(response)
        data = self._json(response)
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            raise BootstrapError("Legend token response did not contain access_token")
        response = self._http.get(
            f"{config.api_url.rstrip('/')}/facility-uses/activitylist",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        self._check(response)
        data = self._json(response)
        concepts = data.get("concepts") if isinstance(data, dict) else None
        return len(concepts) if isinstance(concepts, list) else 0


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def legend_config_from_module(module: Mapping[str, Any], theme_key: str) -> LegendConfig:
    api_url = module.get("apiUrl") or module.get("baseUrl")
    token_url = module.get("oAuthAccessTokenUrl")
    client_id = module.get("nativeClientId") or module.get("clientId")
    client_secret = module.get("nativeSecretKey") or module.get("secretKey")
    if not api_url or not token_url:
        raise BootstrapError("Legend module is missing apiUrl/oAuthAccessTokenUrl")
    if not client_id or not client_secret:
        raise BootstrapError("Legend module is missing nativeClientId/nativeSecretKey")
    return LegendConfig(
        api_url=str(api_url),
        token_url=str(token_url),
        authorize_url=module.get("oAuthUrl") or module.get("authUrl"),
        client_id=decrypt_theme_value(theme_key, str(client_id)),
        client_secret=decrypt_theme_value(theme_key, str(client_secret)),
        identity_provider_id=module.get("identityProviderId"),
        module_id=module.get("id"),
        module_name=module.get("name"),
        filters=module.get("filters") or {},
        is_web_payment=module.get("isWebPayment"),
    )


def run_bootstrap(
    client: BootstrapClient,
    *,
    club_id: int | None = None,
    club_search_term: str = CLUB_SEARCH_TERM,
    activity: str = "badminton",
    module_id: int | None = None,
    create_user: bool = True,
    max_screens: int = 100,
    directions_path: Path | None = None,
    qa: bool = False,
    progress: Progress | None = None,
) -> BootstrapResult:
    """Run the full bootstrap and return the Legend configuration."""

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

    emit("Requesting front-desk token...")
    token = client.anonymous_token(0, "temp", qa=qa)
    emit("Downloading app config...")
    app_config = client.app_config(token, 0, "temp")
    theme_id = int(app_config.get("esid", 1))
    emit(f"Downloaded app config (theme id {theme_id})")
    theme_key = theme_key_for(theme_id, directions_path)

    club: dict[str, Any]
    if club_id is None:
        term = club_search_term or CLUB_SEARCH_TERM
        emit(f"Searching clubs for {term!r}...")
        clubs = client.search_clubs(token, term, 0, "temp")
        if not clubs and term != CLUB_SEARCH_TERM:
            clubs = client.search_clubs(token, CLUB_SEARCH_TERM, 0, "temp")
        if not clubs:
            raise BootstrapError(f"No club matched {term!r}")
        club = clubs[0]
        club_id = int(club["id"])
    else:
        club = {"id": club_id}
    try:
        info = client.club_info(token, club_id, "temp")
        if info:
            club = {**info, **club}
    except BootstrapError:
        pass
    club_name = club.get("name") or club.get("klubName") or "unknown"
    emit(f"Using club {club_id} ({club_name})")
    account_id = str(club["accountId"]) if club.get("accountId") is not None else None

    api = app_config.get("api")
    identity = (app_config.get("aws") or {}).get("identity") or {}
    if not isinstance(api, dict) or not isinstance(identity, dict):
        raise BootstrapError("appConfig did not contain the expected api/aws sections")

    identity_region = str(identity.get("region") or "eu-central-1")
    emit("Creating serverless identity...")
    access_token = client.serverless_oauth(api, theme_key)
    dev = client.dev_token(api, access_token, theme_key)
    credentials = client.cognito_credentials(
        identity_region,
        str(dev["identityId"]),
        str(dev["token"]),
    )
    emit("Creating app installation...")
    installation = client.create_installation(
        api,
        credentials,
        club_id,
        account_id=account_id,
        region=identity_region,
    )
    installation_id = str(installation["id"])

    user_id: str | None = None
    if create_user:
        emit("Creating anonymous user...")
        user = client.create_user(
            api,
            credentials,
            installation_id,
            account_id=account_id,
            region=identity_region,
        )
        user_id = str(user["id"]) if user.get("id") else None
        if user_id:
            with contextlib.suppress(BootstrapError):
                client.user_clubs(
                    api,
                    credentials,
                    user_id,
                    installation_id,
                    account_id=account_id,
                    region=identity_region,
                )

    emit("Refreshing front-desk token...")
    token = client.anonymous_token(club_id, installation_id, qa=qa)

    raw_home: dict[str, Any] | None = None
    screens_visited = 0
    if module_id is not None:
        emit(f"Loading module {module_id}...")
        module = client.module_config(token, module_id, club_id, installation_id)
    else:
        emit("Fetching home screen...")
        raw_home = client.home_screen(token, club_id, installation_id)
        emit("Walking app screens for facility hire...")
        module, screens_visited = find_legend_module(
            raw_home,
            lambda screen_id: client.screen(screen_id, token, club_id, installation_id),
            activity,
            max_screens=max_screens,
            progress=progress,
        )

    emit(f"Found {module.get('name')} ({module.get('id')})")
    emit("Decrypting Legend credentials...")
    legend = legend_config_from_module(module, theme_key)
    emit("Bootstrap complete.")
    return BootstrapResult(
        app_config=app_config,
        theme_id=theme_id,
        theme_key=theme_key,
        club=club,
        module=module,
        legend=legend,
        installation_id=installation_id,
        user_id=user_id,
        screens_visited=screens_visited,
        raw_home=raw_home,
    )


def render_env(config: LegendConfig, extra: Mapping[str, str] | None = None) -> str:
    """Render the Legend configuration as dotenv text."""
    values: dict[str, str] = {
        "LBORO_API_BASE_URL": config.api_url,
        "LBORO_OAUTH_TOKEN_URL": config.token_url,
        "LBORO_CLIENT_ID": config.client_id,
        "LBORO_CLIENT_SECRET": config.client_secret,
    }
    if config.authorize_url:
        values["LBORO_OAUTH_AUTHORIZE_URL"] = config.authorize_url
    if config.identity_provider_id is not None:
        values["LBORO_IDENTITY_PROVIDER_ID"] = str(config.identity_provider_id)
    if config.module_id is not None:
        values["LBORO_MODULE_ID"] = str(config.module_id)
    if config.module_name:
        values["LBORO_MODULE_NAME"] = config.module_name
    values.update(extra or {})

    lines = ["# Generated by lboro-sport-python bootstrap"]
    lines.extend(f"{key}={json.dumps(value)}" for key, value in values.items())
    return "\n".join(lines) + "\n"


def write_env(
    config: LegendConfig,
    path: Path,
    *,
    force: bool = False,
    extra: Mapping[str, str] | None = None,
) -> Path:
    """Write a dotenv file with the Legend client configuration."""
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass --force to overwrite it")
    path.write_text(render_env(config, extra), encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path
