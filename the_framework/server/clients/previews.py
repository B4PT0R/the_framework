"""Private preview credentials never grant ownership of the principal desktop."""

import hashlib
import hmac
import secrets
import time

from ..api.endpoints import Principal
from ..runtime.discovery import PersistentSecretStore

READ_SCOPES = frozenset({'*:read', 'surfaces:preview'})


class PreviewAccess:
    def __init__(self, service, *, origin=None):
        self.service = service
        self.origin = origin.rstrip("/") if origin else None
        self.key = PersistentSecretStore(service.root / 'preview.key').load_or_create().encode()
        self.cookie = "framework_preview_" + hashlib.sha256(self.key).hexdigest()[:12]
        self.tickets = {}

    def issue(self, name, release=None):
        release = self.service.preview_path(name, release).name
        now = time.time()
        self.tickets = {key: value for key, value in self.tickets.items() if value[0] > now}
        ticket = secrets.token_urlsafe(32)
        self.tickets[ticket] = (now + 30, name, release)
        return f'{self.service.surfaces[name].preview_route}/_open/{ticket}'

    def exchange(self, name, ticket):
        record = self.tickets.pop(ticket, None)
        if record is None or record[0] <= time.time() or record[1] != name:
            raise LookupError('invalid or expired preview ticket')
        release = self.service.preview_path(name, record[2]).name
        payload = f'{int(time.time()) + 8 * 3600}.{secrets.token_urlsafe(16)}'
        signature = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
        return f'{payload}.{signature}', f'{self.service.surfaces[name].preview_route}/{release}/'

    def authenticate(self, request):
        value = request.cookies.get(self.cookie, '')
        try:
            payload, signature = value.rsplit('.', 1)
            expires, _nonce = payload.split('.', 1)
            expected = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
            if int(expires) <= time.time() or not hmac.compare_digest(signature, expected):
                return None
        except (ValueError, TypeError):
            return None
        return Principal(id='surface-preview', scopes=READ_SCOPES, attributes={'client_role': 'preview'})

    def allows_exchange(self, request):
        origin = str(request.base_url).rstrip('/')
        return (
            request.method == 'GET'
            and (self.origin is None or origin == self.origin)
            and request.headers.get('origin', origin) == origin
        )

    def is_exchange(self, path):
        return any(path.startswith(f'{surface.preview_route}/_open/') for surface in self.service.surfaces.values())

    def allows_http(self, request):
        path = request.url.path
        return request.method in {'GET', 'HEAD'} and (
            path.startswith('/api/v1/')
            or any(path.startswith(f'{surface.preview_route}/') for surface in self.service.surfaces.values())
        )


def preview_access(request):
    """Find the surface host without coupling application policies to its storage."""
    application = getattr(getattr(request.scope.get('app'), 'state', None), 'application', None)
    service = getattr(application, 'surface_service', None)
    return getattr(service, 'previews', None)


def is_preview(principal):
    return principal is not None and principal.attributes.get('client_role') == 'preview'


def authorize_preview(principal, requirement):
    def allowed(scope):
        return scope is None or scope in principal.scopes or ('*:read' in principal.scopes and scope.endswith(':read'))

    if requirement is None:
        return True
    if isinstance(requirement, str):
        return allowed(requirement)
    if isinstance(requirement, dict):
        return allowed(requirement.get('scope'))
    return all(allowed(scope) for scope in requirement)
