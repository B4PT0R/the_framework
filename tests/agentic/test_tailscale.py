import json
import subprocess
import sys

import pytest

from harness_core.server.clients.tailscale import (
    configure_serve,
    endpoint_from_status,
    main,
    published_endpoint,
    read_status,
    read_serve_status,
    validate_serve_endpoint,
    validate_tailnet_endpoint,
)


STATUS = {
    "BackendState": "Running",
    "Self": {
        "Online": True,
        "DNSName": "desktop.example-tailnet.ts.net.",
    },
}


def test_status_exposes_the_magicdns_https_origin():
    assert endpoint_from_status(STATUS) == "https://desktop.example-tailnet.ts.net"


def test_status_requires_an_online_magicdns_node():
    with pytest.raises(RuntimeError, match="not connected"):
        endpoint_from_status({"BackendState": "Stopped"})
    with pytest.raises(RuntimeError, match="not online"):
        endpoint_from_status({"BackendState": "Running", "Self": {"Online": False}})


def test_remote_endpoint_must_belong_to_the_same_tailnet():
    assert validate_tailnet_endpoint(
        "https://server.example-tailnet.ts.net/", STATUS,
    ) == "https://server.example-tailnet.ts.net"
    with pytest.raises(ValueError, match="tailnet"):
        validate_tailnet_endpoint("https://server.other-tailnet.ts.net", STATUS)
    with pytest.raises(ValueError, match="HTTPS"):
        validate_tailnet_endpoint("http://server.example-tailnet.ts.net", STATUS)


def test_read_status_and_serve_use_the_tailscale_cli():
    calls = []

    def run(command, **options):
        calls.append((command, options))
        if command[1:3] == ["status", "--json"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(STATUS), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    assert read_status(run) == STATUS
    assert configure_serve(48887, run) == "https://desktop.example-tailnet.ts.net"
    assert calls[-1][0] == ["tailscale", "serve", "--bg", "48887"]


@pytest.mark.parametrize("port", [8342, 48887])
def test_published_endpoint_requires_https_serve_to_target_the_application(port):
    serve = {
        "Web": {
            "desktop.example-tailnet.ts.net:443": {
                "Handlers": {"/": {"Proxy": f"http://127.0.0.1:{port}"}},
            },
        },
    }

    assert validate_serve_endpoint(
        "https://desktop.example-tailnet.ts.net", serve, port,
    ) == "https://desktop.example-tailnet.ts.net"
    with pytest.raises(RuntimeError, match=f"port {port}"):
        validate_serve_endpoint(
            "https://desktop.example-tailnet.ts.net",
            {"Web": {}},
            port,
        )

    def run(command, **_options):
        payload = serve if command[1:3] == ["serve", "status"] else STATUS
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    assert published_endpoint(port, command_runner=run) == (
        "https://desktop.example-tailnet.ts.net"
    )


@pytest.mark.parametrize("reader", [read_status, read_serve_status])
def test_stalled_tailscale_status_has_a_bounded_failure(reader):
    def run(command, **options):
        assert options["timeout"] == 10
        raise subprocess.TimeoutExpired(command, options["timeout"])

    with pytest.raises(RuntimeError, match="unable to read"):
        reader(run)


@pytest.mark.parametrize("action", ["serve", "published-endpoint"])
@pytest.mark.parametrize("port_args", [[], ["--port", "0"], ["--port", "65536"]])
def test_cli_requires_an_explicit_valid_application_port(monkeypatch, action, port_args):
    monkeypatch.setattr(sys, "argv", ["tailscale", action, *port_args])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
