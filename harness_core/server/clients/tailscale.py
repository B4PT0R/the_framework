"""Tailscale-first network discovery and installer checks."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Mapping
from urllib.parse import urlparse

def endpoint_from_status(payload):
    if not isinstance(payload, Mapping) or payload.get("BackendState") != "Running":
        raise RuntimeError("Tailscale is not connected")
    current = payload.get("Self")
    if not isinstance(current, Mapping) or current.get("Online") is not True:
        raise RuntimeError("this device is not online in its tailnet")
    dns_name = str(current.get("DNSName") or "").strip().rstrip(".").lower()
    if not dns_name.endswith(".ts.net") or "." not in dns_name:
        raise RuntimeError("Tailscale MagicDNS is unavailable")
    return f"https://{dns_name}"


def validate_tailnet_endpoint(endpoint, payload):
    parsed = urlparse(str(endpoint).strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/")
    ):
        raise ValueError("the remote endpoint must be a root-level HTTPS origin")
    local_endpoint = endpoint_from_status(payload)
    local_hostname = urlparse(local_endpoint).hostname
    tailnet_suffix = local_hostname.split(".", 1)[1]
    hostname = parsed.hostname.lower().rstrip(".")
    if not hostname.endswith(f".{tailnet_suffix}"):
        raise ValueError("the remote endpoint does not belong to this device's tailnet")
    return f"https://{parsed.netloc}"


def read_status(command_runner=subprocess.run):
    try:
        result = command_runner(
            ["tailscale", "status", "--json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return json.loads(result.stdout)
    except FileNotFoundError as error:
        raise RuntimeError("Tailscale is required but is not installed") from error
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise RuntimeError("unable to read Tailscale status") from error


def read_serve_status(command_runner=subprocess.run):
    try:
        result = command_runner(
            ["tailscale", "serve", "status", "--json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return json.loads(result.stdout)
    except FileNotFoundError as error:
        raise RuntimeError("Tailscale is required but is not installed") from error
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise RuntimeError("unable to read Tailscale Serve status") from error


def validate_serve_endpoint(endpoint, payload, port):
    hostname = urlparse(endpoint).hostname
    if not isinstance(payload, Mapping):
        raise RuntimeError("Tailscale Serve is not configured")
    web = payload.get("Web")
    site = web.get(f"{hostname}:443") if isinstance(web, Mapping) else None
    handlers = site.get("Handlers") if isinstance(site, Mapping) else None
    root = handlers.get("/") if isinstance(handlers, Mapping) else None
    proxy = root.get("Proxy") if isinstance(root, Mapping) else None
    parsed_proxy = urlparse(str(proxy or ""))
    if (
        parsed_proxy.scheme != "http"
        or parsed_proxy.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed_proxy.port != port
    ):
        raise RuntimeError(
            f"Tailscale Serve must proxy HTTPS / to the application on port {port}"
        )
    return endpoint


def published_endpoint(port, command_runner=subprocess.run):
    endpoint = endpoint_from_status(read_status(command_runner))
    return validate_serve_endpoint(
        endpoint,
        read_serve_status(command_runner),
        port,
    )


def configure_serve(port, command_runner=subprocess.run):
    endpoint = endpoint_from_status(read_status(command_runner))
    command_runner(
        ["tailscale", "serve", "--bg", str(port)],
        check=True,
        text=True,
        timeout=30,
    )
    return endpoint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("status", "published-endpoint", "check-endpoint", "serve"),
    )
    parser.add_argument("endpoint", nargs="?")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if args.action in {"serve", "published-endpoint"} and (
        args.port is None or not 1 <= args.port <= 65535
    ):
        parser.error(f"{args.action} requires --port in 1..65535")
    try:
        status = read_status()
        if args.action == "status":
            result = endpoint_from_status(status)
        elif args.action == "published-endpoint":
            result = validate_serve_endpoint(
                endpoint_from_status(status),
                read_serve_status(),
                args.port,
            )
        elif args.action == "check-endpoint":
            if args.endpoint is None:
                parser.error("check-endpoint requires an HTTPS endpoint")
            result = validate_tailnet_endpoint(args.endpoint, status)
        else:
            if args.endpoint is not None:
                parser.error("serve does not accept an endpoint")
            result = configure_serve(args.port)
    except (RuntimeError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Tailscale error: {error}\n")
    print(result)


if __name__ == "__main__":
    main()
