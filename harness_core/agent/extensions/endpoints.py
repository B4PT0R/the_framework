from modict import modict

from .schema import Parameters
from ..models.base import Base
from .schema import parse_function_specs


class Endpoint(Base):
    handler = modict.attr(None)
    request_schema = modict.attr(None)
    response_schema = modict.attr(None)
    method: str = modict.field(required="always")
    path: str = modict.field(required="always")
    description: str = ""
    request: Parameters | None = None
    response: object | None = None
    authenticated: bool = True
    authorization: object | None = None

    @modict.validator("method")
    def normalize_method(self, value):
        return value.upper()

    @modict.validator("path", mode="after")
    def validate_path(self, value):
        if not value.startswith("/"):
            raise ValueError("endpoint path must start with /")
        return value

    @property
    def name(self):
        return f"{self.method} {self.path}"

    def __call__(self, *args, **kwargs):
        if not self.has_attr("handler") or self.handler is None:
            raise ValueError("No function associated with this endpoint")
        return self.handler(*args, **kwargs)

    @classmethod
    def from_function(cls, func):
        declaration = getattr(func, "agent_endpoint", None)
        if declaration is None:
            raise ValueError("function has no endpoint declaration")
        specs = parse_function_specs(func)
        context_parameter = (
            declaration.context_parameter
            if declaration.has_attr("context_parameter")
            else None
        )
        if context_parameter:
            parameters = specs["parameters"]
            parameters["properties"].pop(context_parameter, None)
            parameters["required"] = [
                name for name in parameters.get("required", [])
                if name != context_parameter
            ]
        endpoint = cls(**{
            **declaration,
            "description": specs["description"],
            "request": Parameters.from_dict(specs["parameters"]),
            "response": specs.get("response"),
        })
        endpoint.set_attr("handler", func)
        endpoint.set_attr("request_schema", declaration.request_schema)
        endpoint.set_attr("response_schema", declaration.response_schema)
        endpoint.set_attr("context_parameter", context_parameter)
        endpoint.set_attr(
            "status_code",
            declaration.status_code if declaration.has_attr("status_code") else 200,
        )
        endpoint.set_attr(
            "request_encoding",
            declaration.request_encoding
            if declaration.has_attr("request_encoding")
            else "json",
        )
        return endpoint


class Endpoints(Base[str, Endpoint]):
    def add(self, endpoint_or_func):
        endpoint = (
            endpoint_or_func
            if isinstance(endpoint_or_func, Endpoint)
            else Endpoint.from_function(endpoint_or_func)
        )
        if endpoint.name in self:
            raise ValueError(f"duplicate endpoint: {endpoint.name}")
        self[endpoint.name] = endpoint
        return endpoint

    def list(self):
        return list(self.values())


def endpoint(
    method,
    path,
    *,
    request=None,
    response=None,
    authenticated=True,
    authorization=None,
    context=None,
    status_code=200,
    request_encoding="json",
):
    declaration = Endpoint(
        method=method,
        path=path,
        authenticated=authenticated,
        authorization=authorization,
    )
    declaration.set_attr("request_schema", request)
    declaration.set_attr("response_schema", response)
    if context is not None and (
        not isinstance(context, str) or not context.strip()
    ):
        raise ValueError("endpoint context parameter must be a non-empty string")
    declaration.set_attr("context_parameter", context)
    if not isinstance(status_code, int) or isinstance(status_code, bool) \
            or not 100 <= status_code <= 599:
        raise ValueError("endpoint status code must be an integer within 100..599")
    declaration.set_attr("status_code", status_code)
    if request_encoding not in {"json", "multipart"}:
        raise ValueError("endpoint request encoding must be json or multipart")
    declaration.set_attr("request_encoding", request_encoding)

    def decorator(func):
        func.agent_endpoint = declaration
        return func
    return decorator
