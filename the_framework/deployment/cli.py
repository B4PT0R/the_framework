"""Small opt-in application CLI; no service installation or automatic selection."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from pathlib import Path
from dataclasses import replace

from .deployment import Deployment
from .image import BackendImage
from .host import HostEndpoint, HostServices
from .supervisor import BackendRuntime


def main(image, *, projects=(), browser_factory=None, argv=None):
    """Expose a fixed BackendImage recipe as build/test/select/status/serve.

    A runtime JSON file supplies BackendRuntime fields (including secrets), never
    source/build commands. Optional host.address is the address reachable FROM
    the container; --host-bind explicitly chooses the private host interface.
    """
    parser = argparse.ArgumentParser(description="Immutable backend images (Docker Linux containers)")
    parser.add_argument("--root", type=Path, required=True, help="private deployment state, not the live development data")
    parser.add_argument("--runtime", type=Path, help="private BackendRuntime JSON settings")
    parser.add_argument("--host-bind", help="explicit private IP for optional host services; never wildcard")
    parser.add_argument("--session", type=Path, help="optional canonical session COPY for testing only")
    parser.add_argument("--wheel", type=Path, action="append", default=[], help="explicit additional wheel (repeatable)")
    parser.add_argument("--project", type=Path, action="append", default=[], help="explicit additional Python project (repeatable)")
    parser.add_argument("operation", choices=("build", "test", "select", "status", "serve"))
    parser.add_argument("image", nargs="?")
    args = parser.parse_args(argv)
    deployment = Deployment(args.root)

    def recipe():
        if isinstance(image, BackendImage):
            return replace(image, wheels=(*image.wheels, *args.wheel), projects=(*image.projects, *args.project))
        try:
            return BackendImage(image, wheels=tuple(args.wheel), projects=(*projects, *args.project))
        except ValueError as error:
            parser.error(str(error))
    if args.operation == "build":
        print(recipe().build())
        return
    if args.operation == "status":
        print(json.dumps(deployment.status(), indent=2))
        return
    if args.operation == "select":
        if args.image is None:
            parser.error("select requires an immutable image ID")
        print(deployment.select(args.image))
        return
    settings = json.loads(args.runtime.read_text(encoding="utf-8")) if args.runtime else {}
    if args.operation == "test":
        if args.image is None:
            parser.error("test requires an immutable image ID")
        print(json.dumps(deployment.test(args.image, session=args.session, options=settings.get("options"))))
        return
    if args.runtime is None:
        parser.error("serve requires --runtime with stable private credentials")
    if settings.get("host") is not None:
        settings["host"] = HostEndpoint(**settings["host"])
        if not args.host_bind:
            parser.error("host services require an explicit --host-bind private IP")
    runtime = BackendRuntime(**settings)
    asyncio.run(_serve(deployment, recipe() if runtime.host else None, runtime, args.host_bind, args.session, browser_factory))


async def _serve(deployment, image, runtime, bind, session, browser_factory):
    stop = threading.Event()
    services = None
    browser = None
    task = None
    try:
        if runtime.host is not None:
            browser = browser_factory(deployment.root / "host-browser") if browser_factory is not None else None
            services = HostServices(
                deployment, token=runtime.host.token, browser_token=runtime.host.browser_token,
                build=image.build, browser=browser,
                session=session, options=runtime.options,
            )
            await services.start((bind, runtime.host.address[1]))
        task = asyncio.create_task(asyncio.to_thread(deployment.serve, runtime, stop=stop))
        await asyncio.shield(task)
    finally:
        stop.set()
        try:
            if task is not None:
                await task  # Keep the loop alive for graceful drain on Ctrl-C.
        finally:
            try:
                if services is not None:
                    await services.close()
            finally:
                if browser is not None:
                    await browser.stop()
