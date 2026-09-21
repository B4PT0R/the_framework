"""Declarative HTTP endpoint registration for server extensions.

The registry is deliberately independent from any concrete application.  It
turns the serializable :class:`agent.endpoints.Endpoint` declarations into a
small, uniform FastAPI surface while keeping authentication, authorization,
validation and public error translation at the server boundary.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Protocol
from uuid import uuid4

import httpx
from modict import modict
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

logger = logging.getLogger(__name__)


class Principal(modict):
    """Authenticated caller identity exposed to authorization policies."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)
    id: str
    scopes: frozenset[str] = frozenset()
    attributes: dict[str, Any] = modict.factory(dict)


class EndpointContext(modict):
    """Opt-in framework context injected into an endpoint handler."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    request: Request
    principal: Principal | None
    application: object | None = None


class SecurityPolicy(Protocol):
    """Application-owned authentication and authorization boundary."""

    async def authenticate(self, request: Request) -> Principal | None: ...

    async def authorize(
        self,
        principal: Principal,
        requirement: object | None,
        request: Request,
    ) -> bool: ...


class PermitAllSecurity:
    """Explicit development/compatibility policy, never an implicit default."""

    async def authenticate(self, _request):
        return Principal(id="local", scopes=frozenset({"*"}))

    async def authorize(self, _principal, _requirement, _request):
        return True


class HttpError(Exception):
    """A controlled error safe to expose over the application API."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: object | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.retryable = retryable

    def payload(self):
        error = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}


def _schema_dict(value):
    if value is None:
        return None
    if isinstance(value, type) and issubclass(value, modict):
        from .model_schema import model_schema
        return model_schema(value)
    if hasattr(value, "payload"):
        value = value.payload
    if isinstance(value, dict) or hasattr(value, "items"):
        return {key: _schema_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_schema_dict(item) for item in value]
    return value


def _validator(schema, *, endpoint, direction):
    schema = _schema_dict(schema)
    if schema is None:
        return None
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ValueError(
            f"invalid {direction} schema for endpoint {endpoint.name}: "
            f"{error.message}"
        ) from error
    return Draft202012Validator(schema)


def _validation_details(error: ValidationError):
    return {
        "path": [str(part) for part in error.absolute_path],
        "schema_path": [str(part) for part in error.absolute_schema_path],
        "message": error.message,
    }


def _coerce_query_value(value, schema):
    expected = schema.get("type") if isinstance(schema, dict) else None
    if isinstance(expected, list):
        expected = next((item for item in expected if item != "null"), None)
    if expected == "integer":
        return int(value)
    if expected == "number":
        return float(value)
    if expected == "boolean":
        normalized = value.lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        raise ValueError(f"invalid boolean value: {value}")
    if expected in {"object", "array"}:
        import json

        return json.loads(value)
    if expected == "null" and value.lower() in {"null", "none"}:
        return None
    return value


def _openapi_operation(endpoint, request_schema, response_schema):
    required = set((request_schema or {}).get("required", ()))
    properties = (request_schema or {}).get("properties", {})
    if endpoint.method in {"GET", "DELETE", "HEAD"}:
        parameters = []
        for name, schema in properties.items():
            path_parameter = "{" + name + "}" in endpoint.path
            parameters.append({
                "name": name,
                "in": "path" if path_parameter else "query",
                "required": path_parameter or name in required,
                "schema": schema,
            })
        request = {"parameters": parameters} if parameters else {}
    else:
        encoding = (
            endpoint.request_encoding
            if endpoint.has_attr("request_encoding")
            else "json"
        )
        media_type = (
            "multipart/form-data" if encoding == "multipart"
            else "application/json"
        )
        request = {
            "requestBody": {
                "required": bool(required),
                "content": {media_type: {"schema": request_schema or {}}},
            }
        }
    status_code = (
        endpoint.status_code if endpoint.has_attr("status_code") else 200
    )
    if status_code in {204, 205, 304}:
        response = {str(status_code): {"description": "Successful Response"}}
    else:
        response = {
            str(status_code): {
                "description": "Successful Response",
                "content": {
                    "application/json": {"schema": response_schema or {}}
                },
            }
        }
    return {**request, "responses": response}


class EndpointRegistry:
    """Validate and register a complete declarative endpoint set atomically."""

    def __init__(
        self,
        app,
        *,
        security: SecurityPolicy | None = None,
        validate_responses: bool = True,
        error_types=(RuntimeError, TimeoutError, httpx.HTTPError),
        application_context=None,
        occupied=(),
        reserved_prefixes=(),
    ):
        self.app = app
        self.security = security
        self.validate_responses = validate_responses
        self.error_types = error_types
        self.application_context = application_context
        self._entries = []
        self._occupied = {
            (method.upper(), route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())
        }
        self._occupied.update(occupied)
        self._reserved_prefixes = tuple(
            value.rstrip("/") or "/" for value in reserved_prefixes
        )

    def add(self, endpoint, *, owner="application"):
        identity = (endpoint.method.upper(), endpoint.path)
        if identity in self._occupied:
            raise ValueError(f"duplicate application endpoint: {endpoint.name}")
        if any(
            endpoint.path == prefix
            or prefix != "/" and endpoint.path.startswith(f"{prefix}/")
            or prefix == "/"
            for prefix in self._reserved_prefixes
        ):
            raise ValueError(
                f"application endpoint is shadowed by a mount: {endpoint.path}"
            )
        if endpoint.authenticated and self.security is None:
            raise ValueError(
                f"authenticated endpoint requires a security policy: {endpoint.name}"
            )
        request_schema = (
            endpoint.request_schema
            if endpoint.has_attr("request_schema")
            else endpoint.request
        )
        response_schema = (
            endpoint.response_schema
            if endpoint.has_attr("response_schema")
            else endpoint.response
        )
        entry = {
            "endpoint": endpoint,
            "owner": owner,
            "request_model": request_schema if isinstance(request_schema, type) and issubclass(request_schema, modict) else None,
            "response_model": response_schema if isinstance(response_schema, type) and issubclass(response_schema, modict) else None,
            "request_validator": _validator(
                request_schema, endpoint=endpoint, direction="request"
            ),
            "response_validator": _validator(
                response_schema, endpoint=endpoint, direction="response"
            ),
            "request_schema": _schema_dict(request_schema),
            "response_schema": _schema_dict(response_schema),
        }
        self._occupied.add(identity)
        self._entries.append(entry)
        return endpoint

    def add_plugin(self, plugin):
        for endpoint in plugin.endpoint_declarations():
            self.add(endpoint, owner=plugin.title)
        return plugin

    async def _authenticate(self, endpoint, request):
        if not endpoint.authenticated:
            return None
        principal = self.security.authenticate(request)
        if inspect.isawaitable(principal):
            principal = await principal
        if principal is None:
            raise HttpError(401, "unauthorized", "Authentication is required")
        allowed = self.security.authorize(
            principal, endpoint.authorization, request
        )
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if not allowed:
            raise HttpError(403, "forbidden", "The caller is not authorized")
        return principal

    async def _payload(self, endpoint, request, schema):
        if endpoint.method in {"GET", "DELETE", "HEAD"}:
            properties = (schema or {}).get("properties", {})
            payload = {}
            try:
                for key, value in request.query_params.multi_items():
                    item_schema = properties.get(key, {})
                    if item_schema.get("type") == "array":
                        converted = _coerce_query_value(
                            value, item_schema.get("items", {})
                        )
                        payload.setdefault(key, []).append(converted)
                    else:
                        converted = _coerce_query_value(value, item_schema)
                        payload[key] = converted
            except (TypeError, ValueError) as error:
                raise HttpError(
                    422,
                    "invalid_request",
                    "Path or query parameter coercion failed",
                ) from error
        else:
            encoding = (
                endpoint.request_encoding
                if endpoint.has_attr("request_encoding")
                else "json"
            )
            if encoding == "multipart":
                form = await request.form()
                properties = (schema or {}).get("properties", {})
                payload = {}
                for key, value in form.multi_items():
                    if properties.get(key, {}).get("type") == "array":
                        payload.setdefault(key, []).append(value)
                    elif key in payload:
                        current = payload[key]
                        if not isinstance(current, list):
                            current = [current]
                            payload[key] = current
                        current.append(value)
                    else:
                        payload[key] = value
            else:
                body = await request.body()
                if not body:
                    payload = {}
                elif "application/json" in request.headers.get("content-type", ""):
                    try:
                        payload = await request.json()
                    except ValueError as error:
                        raise HttpError(400, "invalid_json", "Malformed JSON body") from error
                else:
                    raise HttpError(
                        415,
                        "unsupported_media_type",
                        "This endpoint expects an application/json body",
                    )
        if not isinstance(payload, dict):
            raise HttpError(422, "invalid_request", "Request payload must be an object")
        overlap = set(payload).intersection(request.path_params)
        if overlap:
            raise HttpError(
                422,
                "duplicate_path_parameter",
                "Path parameters cannot be repeated in the request payload",
                details={"parameters": sorted(overlap)},
            )
        properties = (schema or {}).get("properties", {})
        try:
            path_payload = {
                key: _coerce_query_value(value, properties.get(key, {}))
                for key, value in request.path_params.items()
            }
        except (TypeError, ValueError) as error:
            raise HttpError(
                422,
                "invalid_request",
                "Path or query parameter coercion failed",
            ) from error
        return {**payload, **path_payload}

    async def _dispatch(self, entry, request):
        endpoint = entry["endpoint"]
        try:
            principal = await self._authenticate(endpoint, request)
            payload = await self._payload(
                endpoint, request, entry["request_schema"]
            )
            validator = entry["request_validator"]
            if validator is not None:
                try:
                    validator.validate(payload)
                except ValidationError as error:
                    raise HttpError(
                        422,
                        "invalid_request",
                        "Request validation failed",
                        details=_validation_details(error),
                    ) from error
            context_parameter = (
                endpoint.context_parameter
                if endpoint.has_attr("context_parameter")
                else None
            )
            if entry["request_model"] is not None:
                try:
                    payload = dict(entry["request_model"](payload))
                except (ValueError, TypeError, KeyError) as error:
                    raise HttpError(422, "invalid_request", "Request model validation failed") from error
            if context_parameter:
                payload[context_parameter] = EndpointContext(
                    request=request,
                    principal=principal,
                    application=self.application_context,
                )
            result = endpoint(**payload)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, Response):
                return result
            status_code = (
                endpoint.status_code
                if endpoint.has_attr("status_code")
                else 200
            )
            if status_code in {204, 205, 304} and result is None:
                return Response(status_code=status_code)
            validator = entry["response_validator"]
            if self.validate_responses and entry["response_model"] is not None:
                try:
                    result = entry["response_model"](result)
                except (ValueError, TypeError, KeyError) as error:
                    raise HttpError(500, "invalid_endpoint_response", "Response model validation failed") from error
            if self.validate_responses and validator is not None:
                try:
                    validator.validate(result)
                except ValidationError as error:
                    raise HttpError(
                        500,
                        "invalid_endpoint_response",
                        "Endpoint returned an invalid response",
                        details=_validation_details(error),
                    ) from error
            return JSONResponse(
                result,
                status_code=status_code,
            )
        except HttpError as error:
            return JSONResponse(error.payload(), status_code=error.status_code)
        except self.error_types as error:
            error_id = uuid4().hex
            logger.exception("endpoint upstream failure [%s]", error_id)
            controlled = HttpError(
                502,
                "upstream_failure",
                "An upstream operation failed",
                details={"error_id": error_id},
                retryable=True,
            )
            return JSONResponse(controlled.payload(), status_code=502)
        except Exception:  # noqa: BLE001 - public boundary must not leak internals.
            error_id = uuid4().hex
            logger.exception("endpoint internal failure [%s]", error_id)
            controlled = HttpError(
                500,
                "internal_error",
                "The endpoint could not complete the request",
                details={"error_id": error_id},
            )
            return JSONResponse(controlled.payload(), status_code=500)

    def install(self):
        """Mutate FastAPI only after every declaration has validated."""
        for entry in self._entries:
            endpoint = entry["endpoint"]

            def make_dispatch(registered):
                async def dispatch(request: Request):
                    return await self._dispatch(registered, request)

                return dispatch

            self.app.add_api_route(
                endpoint.path,
                make_dispatch(entry),
                methods=[endpoint.method],
                description=endpoint.description,
                name=f"extension:{entry['owner']}:{endpoint.name}",
                status_code=(
                    endpoint.status_code
                    if endpoint.has_attr("status_code")
                    else 200
                ),
                openapi_extra=_openapi_operation(
                    endpoint,
                    entry["request_schema"],
                    entry["response_schema"],
                ),
            )
        return self


def register_plugin_endpoints(app, plugins, *, security=None):
    """Compatibility helper for direct plugin tests and legacy composition."""
    registry = EndpointRegistry(
        app,
        security=security or PermitAllSecurity(),
    )
    for plugin in plugins:
        registry.add_plugin(plugin)
    return registry.install()
