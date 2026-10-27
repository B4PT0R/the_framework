"""Opt-in disposable Docker proof: real Framework worker, data and rollback.

Run with FRAMEWORK_DOCKER_TEST=1. Requires an existing Docker daemon and `uv`;
may download public build dependencies. Never mounts user data or Docker socket.
"""

import hashlib
import json
import os
import socket
from pathlib import Path
import subprocess
import threading
import time
import urllib.request

import pytest

from the_framework.deployment import BackendImage, BackendRuntime, Deployment

pytestmark = pytest.mark.skipif(os.environ.get("FRAMEWORK_DOCKER_TEST") != "1", reason="explicit Docker opt-in required")


def test_real_build_validate_serve_replace_and_rollback(tmp_path):
    root = Path(__file__).resolve().parents[2]
    wheels = tmp_path / "wheels"
    for source in (root, root / "tests/fixtures/deployment_app"):
        subprocess.run(["uv", "build", "--wheel", "--out-dir", str(wheels), str(source)], check=True, timeout=120)
    files = tuple(wheels.glob("*.whl"))
    existing = set(subprocess.check_output(["docker", "image", "ls", "--quiet", "--no-trunc"], text=True).split())
    images = [BackendImage("deployment_fixture:" + name, files).build() for name in ("create_app", "updated_app", "production_failure")]
    deployment = Deployment(tmp_path / "deployment")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    runtime = BackendRuntime(token="disposable-backend-token", browser_token="disposable-browser-token", port=port)
    stop = threading.Event()
    failures = []

    def run():
        try:
            deployment.serve(runtime, stop=stop)
        except BaseException as error:
            failures.append(error)

    def once():
        done = threading.Event()
        done.set()
        deployment.serve(runtime, stop=done)

    try:
        deployment.test(images[0])
        deployment.select(images[0])
        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 100
        while deployment.status()["current"] is None and not failures and time.monotonic() < deadline:
            time.sleep(0.2)
        assert deployment.status()["current"] == images[0], failures
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/health", headers={"Authorization": "Bearer " + runtime.token})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
            assert json.load(response)["status"] == "ok"
        session = deployment.root / "data/session.json"
        original_id = json.loads(session.read_text())["id"]
        before = hashlib.sha256(session.read_bytes()).hexdigest()
        deployment.test(images[1], session=session)
        assert hashlib.sha256(session.read_bytes()).hexdigest() == before
        deployment.select(images[1])
        # Real runner callback + graceful ASGI exit75 + host handoff; no model turn.
        restart = urllib.request.Request(f"http://127.0.0.1:{port}/fixture/restart",
                                         headers={"Authorization": "Bearer " + runtime.token}, method="POST")
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(restart, timeout=5) as response:
            assert json.load(response)["status"] == "restarting"
        deadline = time.monotonic() + 100
        while deployment.status()["current"] != images[1] and not failures and time.monotonic() < deadline:
            time.sleep(0.2)
        assert deployment.status()["current"] == images[1], failures
        assert thread.is_alive() and not failures
        stop.set()
        thread.join(180)
        assert not thread.is_alive() and not failures
        assert deployment.status()["previous"] == images[0]
        assert json.loads(session.read_text())["id"] == original_id
        # This fixture deliberately passes isolated startup, but fails when
        # production validation=False: exercise real rollback, not tag changes.
        deployment.test(images[2], session=session)
        deployment.select(images[2])
        once()
        state = deployment.status()
        assert state["current"] == images[1] and state["selected"] is None
        assert state["pending"] is None and "previous image restored" in state["error"]
        assert json.loads(session.read_text())["id"] == original_id
    finally:
        stop.set()
        if "thread" in locals():
            thread.join(180)
        # Never remove an image that existed before this test (cache reuse).
        # No prune or unrelated engine operations.
        for image in set(images) - existing:
            subprocess.run(["docker", "image", "rm", image], capture_output=True, timeout=30)


def test_generated_starter_wheel_and_isolated_startup(tmp_path):
    import zipfile
    from the_framework.cli import bootstrap

    root = Path(__file__).resolve().parents[2]
    code, private = tmp_path / "code", tmp_path / "private"
    bootstrap(code, private)
    wheels = tmp_path / "wheels"
    for project in (root, code):
        subprocess.run(["uv", "build", "--wheel", "--out-dir", str(wheels), str(project)],
                       check=True, timeout=120)
    with zipfile.ZipFile(next(wheels.glob("local_agent_app-*.whl"))) as archive:
        assert "starter/memory.md" in archive.namelist()
        assert "starter/deployment.py" in archive.namelist()
        assert "starter/instance.json" not in archive.namelist()
    existing = set(subprocess.check_output(["docker", "image", "ls", "--quiet", "--no-trunc"], text=True).split())
    image = BackendImage("starter.server:create_app", tuple(wheels.glob("*.whl"))).build()
    try:
        deployment = Deployment(tmp_path / "releases")
        assert deployment.test(image)["status"] == "ok"
        assert deployment.status()["selected"] is None
        assert not any(private.iterdir())
    finally:
        if image not in existing:
            subprocess.run(["docker", "image", "rm", image], capture_output=True, timeout=30)
