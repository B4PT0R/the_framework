"""Real starter child with a test-only trigger for its supervised restart."""

import asyncio
import os
from pathlib import Path

from fastapi import Request, HTTPException

import starter.server
from core import AgentApplication, ClientSurface
from starter.desktop import main

build = starter.server.create_app
if source := os.environ.get("STARTER_TEST_UI"):
    declaration = starter.server.application
    surface = ClientSurface({**declaration.surfaces[0], "source": Path(source)})
    starter.server.application = AgentApplication({**declaration, "surfaces": (surface,)})


def create_app(*args, **kwargs):
    app = build(*args, **kwargs)

    @app.post("/test/restart")
    async def restart(request: Request):
        if request.cookies.get("starter_session") != kwargs["token"]:
            raise HTTPException(401)
        asyncio.get_running_loop().call_later(
            0.1, lambda: asyncio.create_task(kwargs["restart"](None)),
        )
        return {"pid": os.getpid()}

    @app.get("/test/process")
    async def process():
        return {"pid": os.getpid()}

    # Test routes precede the application's managed-surface catch-all mount.
    app.router.routes[:0] = app.router.routes[-2:]
    del app.router.routes[-2:]
    return app


starter.server.create_app = create_app
main()
