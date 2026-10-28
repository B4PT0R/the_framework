import asyncio

import pytest
from starlette.requests import Request

from starter.security import LocalSecurity


def request(*headers):
    return Request({"type": "http", "headers": [(key.encode(), value.encode()) for key, value in headers]})


def test_local_credentials_require_exact_host_and_origin():
    security = LocalSecurity("private-test-secret", origin="http://127.0.0.1:8123")

    def authenticated(host="127.0.0.1:8123", origin="http://127.0.0.1:8123", token="private-test-secret"):
        return asyncio.run(security.authenticate(request(
            ("host", host), ("origin", origin), ("authorization", "Bearer " + token),
        )))

    assert authenticated().id == "local"
    assert authenticated(host="attacker.example:8123") is None
    assert authenticated(origin="https://attacker.example") is None
    assert authenticated(token="incorrect") is None
    assert authenticated(token="é") is None


@pytest.mark.parametrize("header,value", [
    ("host", "attacker.example:8123"),
    ("origin", "https://attacker.example"),
    ("host", "127.0.0.1:8123"),
    ("origin", "http://127.0.0.1:8123"),
])
def test_ambiguous_authorities_reject_http_and_socket_credentials(header, value):
    security = LocalSecurity("private-test-secret", origin="http://127.0.0.1:8123")
    headers = (
        ("host", "127.0.0.1:8123"), ("origin", "http://127.0.0.1:8123"),
        ("cookie", "starter_session=private-test-secret"), (header, value),
    )
    assert asyncio.run(security.authenticate(request(*headers))) is None
    assert asyncio.run(security.handshake(request(*headers))) is None


def test_browser_cookie_and_websocket_origin():
    security = LocalSecurity("private-test-secret", origin="http://127.0.0.1:8123")
    headers = (("host", "127.0.0.1:8123"), ("cookie", "starter_session=private-test-secret"))
    assert asyncio.run(security.authenticate(request(*headers))).id == "local"
    assert asyncio.run(security.handshake(request(*headers))) is None
    assert asyncio.run(security.handshake(request(
        *headers, ("origin", "http://127.0.0.1:8123"),
    ))) == {"subprotocol": None, "principal_id": "local"}


def test_browser_cookie_survives_control_token_rotation_without_crossing_channels():
    headers = (("host", "127.0.0.1:8123"),)
    for token in ("first-process", "replacement-process"):
        security = LocalSecurity(token, origin="http://127.0.0.1:8123", browser_token="stable-browser")
        assert asyncio.run(security.authenticate(request(*headers, ("cookie", "starter_session=stable-browser"))))
        assert asyncio.run(security.authenticate(request(*headers, ("authorization", f"Bearer {token}"))))
        assert asyncio.run(security.authenticate(request(*headers, ("cookie", f"starter_session={token}")))) is None
        assert asyncio.run(security.authenticate(request(*headers, ("authorization", "Bearer stable-browser")))) is None
