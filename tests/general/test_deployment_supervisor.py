"""Durable image transitions with a simulated engine (no Docker dependency)."""

import json
import threading

import pytest

from the_framework.deployment import BackendRuntime, Deployment, DeploymentError
from the_framework.deployment.supervisor import _Supervisor

IMAGE = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


class Engine:
    def __init__(self):
        self.calls = []
        self.container = None
        self.failed_images = set()
        self.fail_drain = False

    def run(self, *args, **kwargs):
        self.calls.append(tuple(map(str, args)))
        operation = args[0]
        if operation == "ps":
            return "container-id" if self.container else ""
        if operation == "create":
            assert self.container is None
            self.container = {"image": args[-1], "Running": False, "ExitCode": 0}
        if operation == "start":
            self.container["Running"] = self.container["image"] not in self.failed_images
            self.container["ExitCode"] = 3 if not self.container["Running"] else 0
        if operation == "stop":
            self.container["Running"] = False
        if operation == "exec" and args[-1] == "drain" and self.fail_drain:
            raise DeploymentError("busy incumbent")
        return ""

    def inspect(self, name, *, timeout=60):
        return {"State": self.container.copy()}

    def remove(self, name):
        self.calls.append(("rm", name))
        self.container = None


def runtime():
    return BackendRuntime(token="backend-secret", browser_token="browser-secret", port=18765)


def ready(deployment, image):
    with deployment._state_lock():
        state = deployment.status()
        state["validated"][image] = {"version": 2, "options": {}}
        deployment._save(state)
    deployment.select(image)


def stopped():
    event = threading.Event()
    event.set()
    return event


def test_first_launch_and_promotion_commit_only_healthy_images(tmp_path):
    engine = Engine()
    deployment = Deployment(tmp_path, docker=engine)
    ready(deployment, IMAGE)
    deployment.serve(runtime(), stop=stopped())
    assert deployment.status()["current"] == IMAGE
    assert deployment.status()["previous"] is None
    assert deployment.status()["pending"] is None
    assert engine.container is None
    ready(deployment, OTHER)
    deployment.serve(runtime(), stop=stopped())
    assert deployment.status()["current"] == OTHER
    assert deployment.status()["previous"] == IMAGE
    assert deployment.status()["selected"] is None
    create = [call for call in engine.calls if call[0] == "create"][-1]
    assert create[create.index("--publish") + 1] == "127.0.0.1:18765:8080"
    assert "--privileged" not in create and "--env" not in create
    assert "backend-secret" not in str(engine.calls)
    settings = json.loads((tmp_path / "settings.json").read_text())
    assert settings["token"] == "backend-secret"


def test_failed_startup_restores_known_good_without_retry_loop(tmp_path):
    engine = Engine()
    deployment = Deployment(tmp_path, docker=engine)
    ready(deployment, IMAGE)
    deployment.serve(runtime(), stop=stopped())
    ready(deployment, OTHER)
    engine.failed_images.add(OTHER)
    deployment.serve(runtime(), stop=stopped())
    state = deployment.status()
    assert state["current"] == IMAGE
    assert state["selected"] is None and state["pending"] is None
    assert "previous image restored" in state["error"]
    assert [call[-1] for call in engine.calls if call[0] == "create"] == [IMAGE, OTHER, IMAGE]


def test_failed_first_launch_is_visible_and_never_committed(tmp_path):
    engine = Engine()
    engine.failed_images.add(IMAGE)
    deployment = Deployment(tmp_path, docker=engine)
    ready(deployment, IMAGE)
    with pytest.raises(DeploymentError, match="no healthy rollback"):
        deployment.serve(runtime(), stop=stopped())
    assert deployment.status()["current"] is None
    assert deployment.status()["selected"] is None
    assert engine.container is None


@pytest.mark.parametrize("phase", ["stopping", "starting"])
def test_host_restart_recovers_interrupted_promotion(tmp_path, phase):
    engine = Engine()
    deployment = Deployment(tmp_path, docker=engine)
    ready(deployment, IMAGE)
    deployment.serve(runtime(), stop=stopped())
    ready(deployment, OTHER)
    state = deployment.status()
    state["pending"] = {"image": OTHER, "rollback": IMAGE, "phase": phase}
    deployment._save(state)
    engine.container = {"image": IMAGE if phase == "stopping" else OTHER, "Running": True, "ExitCode": 0}
    engine.failed_images.add(OTHER)
    deployment.serve(runtime(), stop=stopped())
    state = deployment.status()
    assert state["current"] == IMAGE and state["pending"] is None
    assert state["selected"] is None and "interrupted" in state["error"]
    # Exactly one restore, not a second implicit promotion/restart.
    assert [call[-1] for call in engine.calls if call[0] == "create"] == [IMAGE, IMAGE]


def test_failed_drain_does_not_kill_incumbent_or_launch_rival(tmp_path):
    engine = Engine()
    deployment = Deployment(tmp_path, docker=engine)
    ready(deployment, IMAGE)
    deployment.serve(runtime(), stop=stopped())
    ready(deployment, OTHER)
    engine.container = {"image": IMAGE, "Running": True, "ExitCode": 0}
    engine.fail_drain = True
    supervisor = _Supervisor(deployment, runtime(), 5)
    with pytest.raises(DeploymentError, match="could not be drained"):
        supervisor.replace()
    assert engine.container["image"] == IMAGE and engine.container["Running"]
    assert deployment.status()["pending"] is None
    assert not any(call[0] == "stop" for call in engine.calls[-2:])


def test_supervisor_lease_blocks_second_owner(tmp_path):
    from filelock import FileLock

    deployment = Deployment(tmp_path, docker=Engine())
    with FileLock(tmp_path / "supervisor.lock"):
        with pytest.raises(DeploymentError, match="already owns"):
            deployment.serve(runtime(), stop=stopped())


def test_runtime_rejects_reserved_inputs_and_hides_credentials():
    assert "backend-secret" not in repr(runtime())
    assert "browser-secret" not in repr(runtime())
    for options in ({"validation": True}, {"restart": None}, {"data_root": "/etc"}):
        with pytest.raises(ValueError, match="core factory"):
            BackendRuntime(token="one", browser_token="two", options=options)
    with pytest.raises(ValueError, match="origin"):
        BackendRuntime(token="one", browser_token="two", origin="http://localhost/path")


@pytest.mark.parametrize("origin", [
    "http://localhost:wrong", "http://localhost:65536", "http://@localhost",
    "http://:secret@localhost", "http://local host", "http://localhost\n",
    " http://localhost", "http://localhost:",
])
def test_runtime_rejects_malformed_origins_before_launch(origin):
    with pytest.raises(ValueError, match="origin"):
        BackendRuntime(token="one", browser_token="two", origin=origin)


@pytest.mark.parametrize("origin", [
    "http://localhost", "http://127.0.0.1:8123", "https://private.example",
    "http://[::1]:8123",
])
def test_runtime_accepts_http_origins(origin):
    assert BackendRuntime(token="one", browser_token="two", origin=origin).settings()["origin"] == origin


def test_different_untested_options_refuse_before_stopping_incumbent(tmp_path):
    engine = Engine()
    deployment = Deployment(tmp_path, docker=engine)
    ready(deployment, IMAGE)
    engine.container = {"image": IMAGE, "Running": True, "ExitCode": 0}
    changed = BackendRuntime(token="one", browser_token="two", options={"feature": True})
    with pytest.raises(DeploymentError, match="application options"):
        deployment.serve(changed, stop=stopped())
    assert not any(call[0] in ("create", "stop", "exec") for call in engine.calls)
    assert engine.container["Running"]


def test_validation_invalidated_during_boot_cannot_commit(tmp_path):
    engine = Engine()
    deployment = Deployment(tmp_path, docker=engine)
    ready(deployment, IMAGE)
    deployment.serve(runtime(), stop=stopped())
    ready(deployment, OTHER)
    original_wait = deployment._wait_ready

    def invalidate(name, **kwargs):
        original_wait(name, **kwargs)
        if engine.container["image"] == OTHER:
            with deployment._state_lock():
                state = deployment.status()
                state["validated"].pop(OTHER)
                deployment._save(state)

    deployment._wait_ready = invalidate
    deployment.serve(runtime(), stop=stopped())
    assert deployment.status()["current"] == IMAGE
    assert "restored" in deployment.status()["error"]


def test_failed_rollback_retains_recovery_journal(tmp_path):
    engine = Engine()
    deployment = Deployment(tmp_path, docker=engine)
    ready(deployment, IMAGE)
    deployment.serve(runtime(), stop=stopped())
    ready(deployment, OTHER)
    engine.failed_images.update((IMAGE, OTHER))
    with pytest.raises(DeploymentError, match="exited before readiness"):
        deployment.serve(runtime(), stop=stopped())
    state = deployment.status()
    assert state["current"] == IMAGE
    assert state["pending"] == {"image": OTHER, "rollback": IMAGE, "phase": "starting"}
    engine.failed_images.clear()
    deployment.serve(runtime(), stop=stopped())
    assert deployment.status()["current"] == IMAGE
    assert deployment.status()["pending"] is None


def test_rollback_uses_old_options_not_failed_candidate_inputs(tmp_path):
    engine = Engine()
    deployment = Deployment(tmp_path, docker=engine)
    ready(deployment, IMAGE)
    deployment.serve(runtime(), stop=stopped())
    state = deployment.status()
    state["validated"][OTHER] = {"version": 2, "options": {"new_feature": True}}
    deployment._save(state)
    deployment.select(OTHER)
    engine.failed_images.add(OTHER)
    changed = BackendRuntime(token="one", browser_token="two", options={"new_feature": True})
    deployment.serve(changed, stop=stopped())
    assert deployment.status()["current_options"] == {}
    assert json.loads((tmp_path / "settings.json").read_text())["options"] == {}


def test_mutated_core_runtime_options_refuse_before_engine_calls(tmp_path):
    engine = Engine()
    inputs = runtime()
    inputs.options["validation"] = True
    with pytest.raises(ValueError, match="core factory"):
        Deployment(tmp_path, docker=engine).serve(inputs, stop=stopped())
    assert engine.calls == []


def test_editable_workspaces_do_not_replace_installed_code(tmp_path):
    from dataclasses import replace

    workspace = tmp_path / "source"
    workspace.mkdir()
    engine = Engine()
    deployment = Deployment(tmp_path / "deployment", docker=engine)
    ready(deployment, IMAGE)
    deployment.serve(replace(runtime(), workspaces={"application": workspace}), stop=stopped())
    create = next(call for call in engine.calls if call[0] == "create")
    mounts = [create[index + 1] for index, item in enumerate(create) if item == "--mount"]
    assert f"type=bind,source={workspace},target=/workspaces/application" in mounts
    assert all("site-packages" not in mount and "docker.sock" not in mount for mount in mounts)
    for name, path in (("../escape", workspace), ("ok", tmp_path / "missing")):
        with pytest.raises(ValueError, match="workspace"):
            replace(runtime(), workspaces={name: path})


def test_explicit_application_mounts(tmp_path):
    from dataclasses import replace

    source = tmp_path / "resources"
    source.mkdir()
    inputs = replace(runtime(), mounts={"/resources": {"source": source}})
    engine = Engine()
    deployment = Deployment(tmp_path / "deployment", docker=engine)
    ready(deployment, IMAGE)
    deployment.serve(inputs, stop=stopped())
    create = next(call for call in engine.calls if call[0] == "create")
    assert f"type=bind,source={source},target=/resources,readonly" in create
    for target in ("/", "/usr/local", "/data/home", "/workspaces", "relative", "/a/../b", "/a/", "/a,b"):
        with pytest.raises(ValueError, match="mount"):
            replace(runtime(), mounts={target: {"source": source}})
    for spec in ({"source": source, "readonly": "false"}, {"source": source / "missing"}, {"src": source}):
        with pytest.raises(ValueError, match="mount"):
            replace(runtime(), mounts={"/resources": spec})
    with pytest.raises(ValueError, match="overlap"):
        replace(runtime(), mounts={"/resources": {"source": source}, "/resources/nested": {"source": source}})
    inputs.mounts["/resources"]["readonly"] = "false"
    with pytest.raises(ValueError, match="readonly"):
        deployment.serve(inputs, stop=stopped())


@pytest.mark.parametrize("options", [[], False, 0, "", "value", [1]])
def test_application_options_reject_non_mappings(options):
    with pytest.raises(TypeError, match="mapping"):
        BackendRuntime(token="one", browser_token="two", options=options)


@pytest.mark.parametrize("changes, error, message", [
    ({"port": True}, TypeError, "integers"),
    ({"port": "8765"}, TypeError, "integers"),
    ({"user": False}, TypeError, "integers"),
    ({"user": 1.5}, TypeError, "integers"),
    ({"port": 0}, ValueError, "port or user"),
    ({"user": -1}, ValueError, "port or user"),
    ({"token": 1}, ValueError, "nonempty strings"),
    ({"browser_token": []}, ValueError, "nonempty strings"),
    ({"environment": []}, TypeError, "environment must be a mapping"),
    ({"workspaces": []}, TypeError, "workspaces must be a mapping"),
    ({"mounts": None}, TypeError, "mounts must be a mapping"),
    ({"environment": {"": "x"}}, ValueError, "valid string"),
    ({"origin": True}, TypeError, "origin must be a string"),
    ({"options": {1: "x"}}, ValueError, "names must be strings"),
])
def test_runtime_rejects_invalid_inputs_at_construction(changes, error, message):
    with pytest.raises(error, match=message):
        BackendRuntime(**{"token": "one", "browser_token": "two", **changes})


def test_runtime_copies_mapping_inputs():
    from types import MappingProxyType

    environment = {"TZ": "Europe/Paris"}
    runtime = BackendRuntime(token="one", browser_token="two",
                             environment=MappingProxyType(environment),
                             options=MappingProxyType({"enabled": True}))
    environment["TZ"] = "UTC"
    assert runtime.environment == {"TZ": "Europe/Paris"}
    assert runtime.options == {"enabled": True}


@pytest.mark.parametrize("document", ["releases", "settings"])
@pytest.mark.parametrize("stage", ["file_sync", "replace", "directory_sync"])
def test_deployment_snapshots_have_one_private_commit_point(tmp_path, monkeypatch, document, stage, caplog):
    import os
    import stat
    from the_framework.utils import persistence

    deployment = Deployment(tmp_path, docker=Engine())
    inputs = runtime()
    supervisor = _Supervisor(deployment, inputs, 5)
    path = tmp_path / (document + ".json")
    path.write_text('{"previous":true}')
    sync = os.fsync

    def fail_sync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if (stage == "directory_sync") == directory:
            raise OSError("snapshot failure")
        return sync(fd)

    def fail_replace(*args):
        raise OSError("snapshot failure")

    monkeypatch.setattr(persistence.os, "replace" if stage == "replace" else "fsync",
                        fail_replace if stage == "replace" else fail_sync)
    save = supervisor.configure if document == "settings" else lambda: deployment._save({"next": True})
    if stage == "directory_sync":
        save()
        expected = inputs.settings() if document == "settings" else {"next": True}
        assert json.loads(path.read_text()) == expected
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "crash durability is uncertain" in caplog.text
    else:
        with pytest.raises(OSError, match="snapshot failure"):
            save()
        assert path.read_text() == '{"previous":true}'
    assert not list(tmp_path.glob(".*.tmp"))
