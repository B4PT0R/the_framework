import asyncio

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


def test_browser_cookie_and_websocket_origin():
    security = LocalSecurity("private-test-secret", origin="http://127.0.0.1:8123")
    headers = (("host", "127.0.0.1:8123"), ("cookie", "starter_session=private-test-secret"))
    assert asyncio.run(security.authenticate(request(*headers))).id == "local"
    assert asyncio.run(security.handshake(request(*headers))) is None
    assert asyncio.run(security.handshake(request(
        *headers, ("origin", "http://127.0.0.1:8123"),
    ))) == {"subprotocol": None}
