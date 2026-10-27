"""In-image server and read-only probe. No host engine or supervisor imports."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

SETTINGS = Path("/run/backend/settings.json")


def request(settings, path, *, authenticated=True, method="GET", timeout=5):
    headers = {"Host": settings["origin"].split("://", 1)[1]}
    if authenticated:
        headers["Authorization"] = "Bearer " + settings["token"]
    req = urllib.request.Request("http://127.0.0.1:8080" + path, headers=headers, method=method)
    # Never inherit proxy environment for a private local control request.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as response:
        return json.load(response)


def probe(settings, *, complete=False):
    try:
        request(settings, "/api/v1/health", authenticated=False)
    except urllib.error.HTTPError as error:
        if error.code not in (401, 403):
            raise RuntimeError(f"unexpected unauthenticated response: {error.code}") from error
    else:
        raise RuntimeError("health endpoint accepted an unauthenticated request")
    if request(settings, "/api/v1/health").get("status") != "ok":
        raise RuntimeError("backend is not healthy")
    if complete:
        for path in ("/api/v1/status", "/api/v1/config", "/api/v1/session"):
            result = request(settings, path)
            if not isinstance(result, dict):
                raise RuntimeError(f"invalid response: {path}")
        expected = settings.get("session_id")
        if expected is not None:
            saved = json.loads(Path("/data/session.json").read_text(encoding="utf-8"))
            if saved.get("id") != expected:
                raise RuntimeError("validation changed the saved session identity")
    return {"status": "ok"}


async def serve(settings):
    import uvicorn

    os.environ.update(settings.get("environment", {}))
    if hasattr(time, "tzset"):
        time.tzset()
    os.environ.pop("THE_FRAMEWORK_HOST_SETTINGS", None)
    if settings.get("host") and not settings.get("validation"):
        os.environ["THE_FRAMEWORK_HOST_SETTINGS"] = str(SETTINGS)

    if settings.get("validation") and settings.get("session") is not None:
        Path("/data/session.json").write_text(json.dumps(settings["session"]), encoding="utf-8")
    metadata = json.loads(Path("/opt/backend.json").read_text(encoding="utf-8"))
    module, name = metadata["factory"].split(":")
    factory = getattr(importlib.import_module(module), name)
    restarting = False
    server = None

    async def restart(_intent):
        nonlocal restarting
        restarting = True
        server.should_exit = True

    host = settings.get("host")
    if host is not None:
        if settings.get("validation"):
            raise RuntimeError("validation cannot connect to host services")
        from .host import HostEndpoint

        host = HostEndpoint(**host)
    app = factory(
        data_root=Path("/data"), token=settings["token"],
        browser_token=settings["browser_token"], origin=settings["origin"],
        restart=restart, validation=settings.get("validation", False),
        **({"host": host} if host is not None else {}),
        **settings.get("options", {}),
    )
    server = uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=8080, access_log=False,
        timeout_graceful_shutdown=15,
    ))
    await server.serve()
    return 75 if restarting else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("serve", "probe", "drain"), nargs="?", default="serve")
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    if args.operation == "probe":
        print(json.dumps(probe(settings, complete=args.complete)))
    elif args.operation == "drain":
        print(json.dumps(request(settings, "/api/v1/server/prepare-restart", method="POST", timeout=140)))
    else:
        raise SystemExit(asyncio.run(serve(settings)))


if __name__ == "__main__":
    main()
