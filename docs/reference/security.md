# Authentication, authorization and client privileges

The Framework supplies policy seams, not a user database or a universal deployment
security policy. Authenticated HTTP/socket declarations require an explicit policy;
there is no implicit allow-all `Application` default. Network reachability, client
authentication, endpoint authorization, plugin binding and execution permissions
are separate controls.

## A policy and a tested protected endpoint

This service-only ASGI example demonstrates 401, 403, authorized access and socket
rejection. It has no worker/model/network dependency. Its **hardcoded test tokens
are not a provisioning design**. A real application must own credential issuance,
secret storage, revocation, expiry, TLS and browser origin protection.

```python
from hmac import compare_digest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from the_framework import Application, ServerExtension, endpoint, websocket
from the_framework.server.api.endpoints import Principal

class ExamplePolicy:
    async def authenticate(self, connection):
        supplied = connection.headers.get("authorization", "")
        for token, scopes in (("demo-reader", {"report:read"}), ("demo-guest", set())):
            if compare_digest(supplied.encode(), f"Bearer {token}".encode()):
                return Principal(id=token, scopes=frozenset(scopes))
        return None

    async def authorize(self, principal, requirement, connection):
        # The application interprets requirements; the registry does not grant scopes.
        return requirement is None or requirement["scope"] in principal.scopes

@endpoint("get", "/api/v1/report", authorization={"scope": "report:read"})
def report():
    return {"count": 3}

@websocket("/report-events", authorization={"scope": "report:read"})
async def report_events(socket):
    await socket.accept()
    await socket.send_json({"type": "report.ready"})
    await socket.close()

application = Application(
    name="Protected report", version="1", surfaces=(), security=ExamplePolicy(),
    server_extensions=(ServerExtension(
        name="report", endpoints=(report,), websockets=(report_events,),
    ),),
)
with TestClient(application.build()) as client:
    assert client.get("/api/v1/report").status_code == 401
    guest = {"Authorization": "Bearer demo-guest"}
    reader = {"Authorization": "Bearer demo-reader"}
    assert client.get("/api/v1/report", headers=guest).status_code == 403
    assert client.get("/api/v1/report", headers=reader).json() == {"count": 3}
    with client.websocket_connect("/report-events", headers=reader) as socket:
        assert socket.receive_json() == {"type": "report.ready"}
    try:
        with client.websocket_connect("/report-events"):
            raise AssertionError("unauthenticated socket was accepted")
    except WebSocketDisconnect as error:
        assert error.code == 1008
```

Browser `WebSocket` cannot set this example's arbitrary bearer header. The starter
uses provisioned HttpOnly cookies and an application-specific handshake instead;
never solve this by putting long-lived tokens in socket query strings.

## Endpoint boundary

`SecurityPolicy.authenticate(connection)` returns `Principal` or `None`;
`authorize(principal, requirement, connection)` decides the declared requirement.
Both may be sync or async in the registry implementation. `Principal` contains
`id`, `scopes` and optional `attributes`; a `"*"` scope has no magical effect unless
the application's policy interprets it. `PermitAllSecurity` is an explicitly
unsafe development/testing policy, never automatic.

HTTP order is authenticate → authorize → parse/coerce → validate → handler →
validate response. Invalid credentials return 401; refused requirements return
403. `authenticated=False` bypasses **both** policy checks for that endpoint;
use it deliberately for public/bootstrap routes. `context="ctx"` injects an
`EndpointContext` with request, principal and owning application. See
[Services](services.md#http-request-and-response-contracts) for model/schema rules.

Controlled failures use:

```json
{"error":{"code":"forbidden","message":"The caller is not authorized","retryable":false}}
```

`HttpError(status, code, message, details=..., retryable=...)` provides safe public
errors. Request/schema/model errors return 422; malformed JSON 400, unsupported
body type 415. Invalid declared response models/schemas return 500. Default
upstream exception classes (`RuntimeError`, `TimeoutError`, `httpx.HTTPError`)
map to a sanitized retryable 502; unexpected exceptions to sanitized 500, with an
error id for logs. A returned native Starlette/FastAPI `Response` bypasses JSON
validation, supporting streaming and files. Handlers remain responsible for safe
contents/headers. Some deliberately custom responses, notably status timeout,
do not use the ordinary envelope.

`@websocket` uses the same authenticate/authorize policy and rejects before accept
with close code 1008. A custom `handshake` instead owns the **whole** authentication
and authorization step; returning `None` rejects, otherwise its connection object
is passed to the handler. It cannot be combined with a separate authorization
requirement. Protocol negotiation/replay/client roles belong there. Declared route
collisions are checked before installation; raw routes/middleware are outside
this registry contract. Server-push handlers must also receive disconnects; the
shared stream helper cancels idle send subscriptions promptly.

## Starter local policy

[`starter/security.py`](../../starter/security.py) is editable application code,
not an implicit Framework policy. It requires exactly one expected Host, at most
one Origin, and rejects an Origin differing from its configured exact origin.
Its socket handshake additionally requires that Origin. This helps reject DNS
rebinding/cross-site cookie requests; it does not make arbitrary remote hosting
safe by changing the bind address.

The desktop launcher provisions an HttpOnly browser cookie. Separate bearer and
browser tokens may be configured; no credential is put in URLs or browser storage.
The local principal is privileged by the policy. Read-only preview credentials
are checked separately: allowed HTTP/read operations and the event stream do not
grant prompt/config writes, voice startup or the application bridge. Custom apps
must maintain those boundaries if they replace this policy.

## Remote clients and capability leases

`the_framework.server.clients.remote.RemoteClientPolicy()` grants **nothing** by
default: no scopes, capabilities or platform privileges. Supply it explicitly to
`ClientRemoteService`, which owns pairing credentials, receipts, transport and
leases. Platform-specific scopes augment declared default scopes. A network
allowlist or Tailscale connectivity is not an API credential or a capability lease.
Tailscale helpers discover/verify Serve configuration; they do not turn every
installed application into an externally reachable service.

A testable least-privilege policy composition:

```python
from the_framework.server.clients.remote import RemoteClientPolicy

base = RemoteClientPolicy(default_scopes=("session:read",))
audio = RemoteClientPolicy(
    capabilities=frozenset({"audio.output"}),
    exclusive_capabilities=frozenset({"audio.output"}),
    routed_capabilities=frozenset({"audio.output"}),
    default_lease_capabilities=frozenset({"audio.output"}),
    event_capabilities={"audio.play": frozenset({"audio.output"})},
)
policy = RemoteClientPolicy.compose(base, audio)
assert policy.scopes_for("android") == ("session:read",)
assert policy.routed_capabilities == frozenset({"audio.output"})
assert RemoteClientPolicy().scopes_for("android") == ()
```

Routed/default-lease capabilities must be declared exclusive capabilities;
capability-bound events must name declared exclusive leases and cannot also be
unleased. Composition rejects duplicate capability/event ownership. Feature
policies can be contributed through BuildContext and composed by the remote
service factory after declaration collection; do not depend on factory ordering
to discover contributions. Changing policy after startup is forbidden; composition
does not rewrite existing paired-client records. Review/re-pair/revoke credentials
as part of the application's explicit privilege-change procedure.

Agent binding removal hides tools/providers/instructions but does not remove
installed routes/services or revoke a client. Local/host process tools use real
account privileges, not this HTTP policy as an OS sandbox. Keep installation,
agent visibility, authorization and process isolation distinct.

## Source and verification

`server/api/{endpoints,websockets}.py`, `server/clients/{remote,previews,tailscale}.py`
and starter security own these boundaries. Tests: `test_endpoint_registry.py`,
`test_endpoint_models.py`, `test_websockets.py`, `test_starter_security.py`,
`test_remote_policy_composition.py`. Both Python sequences above execute in the
documentation tests; they do not validate a public deployment or a real device.
