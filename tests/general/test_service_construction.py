"""Construction inputs stay separate from ordered services and live state."""

import sys

import pytest
from fastapi.testclient import TestClient

from the_framework import Application, BuildContext, Plugin, ServerExtension, endpoint


def test_factory_receives_explicit_build_context_and_ordered_services(tmp_path):
    resource = object()
    seen = []
    context = BuildContext(data_root=tmp_path, plugins={"feature": {"label": "hello"}})

    class Feature:
        def __init__(self, resource, *, context):
            seen.append((resource, context))
            assert context.application is application
            assert not context.services  # Constructed does not mean started.
            self.label = context.plugin("feature")["label"]

        @endpoint("get", "/feature", authenticated=False)
        def read(self):
            return {"label": self.label}

    application = Application(name="Construction", version="1", plugins=(
        Plugin(name="feature", server_extension=ServerExtension(
            name="feature", service_factory=Feature,
        )),
    ), server_extensions=(ServerExtension(name="resource", service=resource),))
    server = application.build(context=context)
    assert seen == [(resource, context)]
    assert application.extensions["feature"].requires_services == ("resource",)
    with pytest.raises(TypeError):
        context.plugin("feature")["label"] = "changed"
    with TestClient(server) as client:
        assert context.services["resource"] is resource
        assert client.get("/feature").json() == {"label": "hello"}
    assert not context.services


@pytest.mark.parametrize("plugin_owned", [False, True])
def test_lazy_service_factory_is_inert_until_build_and_infers_dependencies(tmp_path, monkeypatch, plugin_owned):
    name = "lazy_service_fixture"
    marker = tmp_path / "imported"
    (tmp_path / f"{name}.py").write_text(f'''
from pathlib import Path
Path({str(marker)!r}).touch()
class Feature:
    def __init__(self, resource, context, optional=None):
        self.resource = resource
        self.context = context
        self.optional = optional
''')
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, name, raising=False)
    resource = object()
    feature_declaration = ServerExtension(name="feature", service_factory=f"{name}:Feature")
    application = Application(name="Lazy", version="1",
        plugins=(Plugin(name="feature", server_extension=feature_declaration),) if plugin_owned else (),
        server_extensions=(ServerExtension(name="resource", service=resource),
                           *((feature_declaration,) if not plugin_owned else ())),
    )
    application.compile()
    assert not marker.exists()
    context = BuildContext()
    application.build(context=context)
    feature = application.extensions["feature"].service
    assert marker.exists()
    assert feature.resource is resource
    assert feature.context is context
    assert feature.optional is None
    assert tuple(application.extensions) == ("resource", "feature")


@pytest.mark.parametrize("factory", [lambda **services: services, lambda **context: context])
def test_context_is_not_an_ordinary_service_or_an_implicit_kwargs_input(factory):
    with pytest.raises(ValueError, match="reserved"):
        ServerExtension(name="context", service=object())
    with pytest.raises(ValueError, match="not a service dependency"):
        ServerExtension(name="feature", requires_services=("context",))
    application = Application(name="Explicit inputs", version="1", server_extensions=(
        ServerExtension(name="feature", service_factory=factory),
    ))
    application.build(context=BuildContext(secret="not forwarded"))
    assert application.extensions["feature"].service == {}


def test_discovery_flags_are_removed_and_explicit_empty_surfaces_stay_private():
    with pytest.raises(TypeError, match="discovery flag"):
        ServerExtension(name="feature", endpoints="service")

    class Feature:
        @endpoint("get", "/private", authenticated=False)
        def read(self):
            return {"private": True}

        def websocket_declarations(self):
            raise AssertionError("explicitly hidden sockets must not be discovered")

    application = Application(name="Hidden", version="1", server_extensions=(
        ServerExtension(name="feature", service_factory=Feature, endpoints=(), websockets=()),
    ))
    with TestClient(application.build()) as client:
        assert client.get("/private").status_code == 404
