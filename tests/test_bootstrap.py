import base64
import json
import stat
from datetime import UTC, datetime

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from lboro_sport_python.bootstrap import (
    BootstrapClient,
    BootstrapError,
    LegendConfig,
    anon_hmac_key,
    app_theme_aes_key,
    decrypt_theme_value,
    derive_theme_key,
    find_legend_module,
    legend_config_from_module,
    render_env,
    sigv4_headers,
    write_env,
)

BASE = "https://front.example/api"


def _encrypt_ecb_pkcs5(key: bytes, data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _encode_direction_byte(value: int) -> str:
    text = str(value)
    return f"{text}{len(text)}"


def test_theme_key_derivation_roundtrip() -> None:
    expected = "0123456789abcdef"
    ciphertext = _encrypt_ecb_pkcs5(app_theme_aes_key(), expected.encode())
    directions = {
        "locations": {"1": [{"lang": _encode_direction_byte(byte)} for byte in ciphertext]}
    }
    assert derive_theme_key(1, directions) == expected
    with pytest.raises(BootstrapError):
        derive_theme_key(999, directions)


def test_decrypt_theme_value_roundtrip() -> None:
    theme_key = "0123456789abcdef"
    value = "client-secret-value"
    encrypted = _encrypt_ecb_pkcs5(theme_key.encode(), value.encode())
    encoded = base64.b64encode(encrypted).decode()
    assert decrypt_theme_value(theme_key, encoded) == value
    assert decrypt_theme_value(theme_key, "not-encrypted!") == "not-encrypted!"


def test_legend_config_from_module_decrypts_credentials() -> None:
    theme_key = "0123456789abcdef"

    def encrypt(value: str) -> str:
        data = _encrypt_ecb_pkcs5(theme_key.encode(), value.encode())
        return base64.b64encode(data).decode()

    module = {
        "id": 241094,
        "name": "Hire - Badminton Court",
        "apiUrl": "https://publicapi.example",
        "oAuthAccessTokenUrl": "https://identity.example/connect/token",
        "oAuthUrl": "https://identity.example/connect/authorize",
        "identityProviderId": 1055,
        "nativeClientId": encrypt("client-id"),
        "nativeSecretKey": encrypt("client-secret"),
        "filters": {"activityIds": ["badminton"]},
        "isWebPayment": True,
    }
    config = legend_config_from_module(module, theme_key)
    assert config.client_id == "client-id"
    assert config.client_secret == "client-secret"
    assert config.api_url == "https://publicapi.example"
    assert config.module_id == 241094
    assert config.filters == {"activityIds": ["badminton"]}


def test_find_legend_module_walks_screens() -> None:
    home = {
        "tiles": [
            {
                "linkedModule": {
                    "id": 1,
                    "type": 51,
                    "name": "Book - home",
                    "screenId": 1,
                }
            }
        ]
    }
    screens = {
        "1": {
            "tiles": [
                {
                    "linkedModule": {
                        "id": 2,
                        "type": 51,
                        "name": "Book - Facility Hire",
                        "screenId": 2,
                    }
                }
            ]
        },
        "2": {
            "tiles": [
                {
                    "linkedModule": {
                        "id": 99,
                        "type": 38,
                        "name": "Hire - Badminton Court",
                    }
                }
            ]
        },
    }
    module, visited = find_legend_module(home, screens.__getitem__, "badminton")
    assert module["id"] == 99
    assert visited == 2


def test_find_legend_module_emits_progress() -> None:
    home = {
        "tiles": [
            {
                "linkedModule": {
                    "id": 1,
                    "type": 51,
                    "name": "Book - home",
                    "screenId": 1,
                }
            }
        ]
    }
    screens = {
        "1": {
            "tiles": [
                {
                    "linkedModule": {
                        "id": 99,
                        "type": 38,
                        "name": "Hire - Badminton Court",
                    }
                }
            ]
        }
    }
    messages: list[str] = []
    module, _ = find_legend_module(home, screens.__getitem__, "badminton", progress=messages.append)
    assert module["id"] == 99
    assert any("fetching screen 1" in message for message in messages)
    assert any("found Legend module" in message for message in messages)


def test_find_legend_module_reports_missing_activity() -> None:
    home = {
        "tiles": [
            {
                "linkedModule": {
                    "id": 1,
                    "type": 38,
                    "name": "Hire - Tennis Court",
                }
            }
        ]
    }
    with pytest.raises(BootstrapError, match="Tennis"):
        find_legend_module(home, lambda _: {}, "badminton")


def test_anon_hmac_keys_differ_between_environments() -> None:
    production = anon_hmac_key()
    qa = anon_hmac_key(qa=True)
    assert len(production) == 32
    assert production != qa


def test_sigv4_signing_headers() -> None:
    headers = sigv4_headers(
        "POST",
        "https://api.example/v1/private/appInstallation",
        {"Content-Type": "application/json; charset=utf-8"},
        b"{}",
        access_key="AKIDEXAMPLE",
        secret_key="secret",
        session_token="session",
        region="eu-central-1",
        now=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
    )
    authorization = headers["authorization"]
    assert "Credential=AKIDEXAMPLE/20261019/eu-central-1/execute-api/aws4_request" in authorization
    assert "SignedHeaders=content-type;host;x-amz-date;x-amz-security-token" in authorization
    assert headers["x-amz-security-token"] == "session"
    assert headers["x-amz-date"] == "20261019T120000Z"


def test_render_and_write_env(tmp_path) -> None:
    config = LegendConfig(
        api_url="https://publicapi.example",
        token_url="https://identity.example/connect/token",
        client_id="client-id",
        client_secret="client-secret",
        module_id=241094,
        module_name="Hire - Badminton Court",
    )
    text = render_env(config)
    assert 'LBORO_API_BASE_URL="https://publicapi.example"' in text
    assert 'LBORO_CLIENT_ID="client-id"' in text

    path = tmp_path / ".env"
    write_env(config, path)
    assert path.read_text() == text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_env(config, path)
    write_env(config, path, force=True)


@respx.mock
def test_anonymous_token_request_payload() -> None:
    route = respx.post(f"{BASE}/auth/anonymous").mock(
        return_value=httpx.Response(200, json={"token": "front-token"})
    )
    with BootstrapClient(frontdesk_url=BASE) as client:
        token = client.anonymous_token(club_id=47425, installation_id="install-1")

    assert token == "front-token"
    request = route.calls[0].request
    payload = json.loads(request.content)
    assert payload["c"] == 47425
    assert payload["appid"] == "516"
    assert payload["appver"] == "705800"
    assert payload["appVersion"] == "110.60"
    assert payload["appInstallationId"] == "install-1"
    assert len(payload["appKeyHash"]) == 64
    assert request.headers["x-mfa-aid"] == "install-1"


@respx.mock
def test_app_config_and_club_search() -> None:
    respx.get(f"{BASE}/proxy/mob/appConfig/").mock(
        return_value=httpx.Response(200, json={"esid": 1, "api": {}})
    )
    search = respx.get(f"{BASE}/proxy/json/klubSearch/Loughborough").mock(
        return_value=httpx.Response(
            200,
            json={"klubJSONDTOList": [{"id": 47425, "klubName": "Loughborough University"}]},
        )
    )

    with BootstrapClient(frontdesk_url=BASE) as client:
        config = client.app_config("token", club_id=47425, installation_id="install-1")
        clubs = client.search_clubs("token", "Loughborough", 47425, "install-1")

    assert config["esid"] == 1
    assert clubs[0]["id"] == 47425
    assert search.calls[0].request.headers["Authorization"] == "Bearer token"
    params = search.calls[0].request.url.params
    assert params["appver"] == "705800"
    assert params["appInstallationId"] == "install-1"
