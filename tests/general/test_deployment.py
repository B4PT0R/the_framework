"""Image lifecycle guarantees that do not need an engine or a network."""

import asyncio
import json
from pathlib import Path

import pytest

from the_framework.deployment import BackendImage, Deployment, DeploymentError
from the_framework.deployment.image import image_id
from the_framework.server.runtime.supervisor import WorkerSupervisor
from the_framework.agent.runtime.protocol import PromptRequest, CompactRequest, ConfigUpdateRequest

IMAGE = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


class DockerStub:
    def __init__(self, *, fail_probe=False, fail_cleanup=False):
        self.calls = []
        self.fail_probe = fail_probe
        self.fail_cleanup = fail_cleanup
        self.settings = None

    def run(self, *args, timeout=60):
        self.calls.append(tuple(map(str, args)))
        if args[0] == "create":
            mount = args[args.index("--mount") + 1]
            source = mount.split("source=", 1)[1].split(",target=", 1)[0]
            self.settings = json.loads(Path(source).read_text())
        if args[0] == "exec" and self.fail_probe:
            raise DeploymentError("probe refused")
        if args[0] == "logs":
            return "backend diagnostics"
        return ""

    def inspect(self, name, *, timeout=60):
        return {"State": {"Running": not self.fail_probe, "ExitCode": 1}}

    def remove(self, name):
        self.calls.append(("rm", name))
        if self.fail_cleanup:
            raise DeploymentError("cleanup refused")


def test_image_recipe_only_stages_explicit_wheels(tmp_path):
    wheel = tmp_path / "app-1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel bytes")
    (tmp_path / ".env").write_text("private")

    class Builder:
        def run(self, *args, timeout):
            assert args[:2] == ("build", "--iidfile")
            root = Path(args[-1])
            assert {path.name for path in root.iterdir()} == {"wheels", "Dockerfile", "backend.json"}
            assert [path.name for path in (root / "wheels").iterdir()] == [wheel.name]
            assert (root / "wheels" / wheel.name).read_bytes() == b"wheel bytes"
            assert "apt-get" in (root / "Dockerfile").read_text()
            assert json.loads((root / "backend.json").read_text()) == {"factory": "demo.server:create_app"}
            Path(args[2]).write_text(IMAGE)

    recipe = BackendImage("demo.server:create_app", (wheel,), packages=("ffmpeg",))
    assert recipe.build(docker=Builder()) == IMAGE


@pytest.mark.parametrize("value", ["latest", "demo:current", "sha256:abc", "sha256:" + "G" * 64])
def test_releases_require_full_immutable_id(value):
    with pytest.raises(ValueError, match="immutable"):
        image_id(value)


def test_recipe_rejects_implicit_context_and_dockerfile_injection(tmp_path):
    wheel = tmp_path / "app.whl"
    wheel.touch()
    for changes in ({"base": "python\nRUN false"}, {"packages": ("curl;false",)}, {"factory": "demo:create;false"}):
        arguments = {"factory": "demo:create_app", "wheels": (wheel,), **changes}
        with pytest.raises(ValueError):
            BackendImage(**arguments)
    with pytest.raises(ValueError, match="wheel"):
        BackendImage("demo:create_app", (tmp_path,))


def test_validation_isolated_and_select_is_explicit(tmp_path):
    docker = DockerStub()
    deployment = Deployment(tmp_path / "release", docker=docker)
    assert not deployment.root.exists()
    session = tmp_path / "session.json"
    source = '{"id":"canonical-id","commands":{},"history":[]}'
    session.write_text(source)
    with pytest.raises(DeploymentError, match="test this exact"):
        deployment.select(IMAGE)
    assert deployment.test(IMAGE, session=session)["status"] == "ok"
    assert session.read_text() == source
    assert docker.settings["session"]["id"] == "canonical-id"
    assert docker.settings["validation"] is True
    assert docker.settings["token"] != docker.settings["browser_token"]
    create = next(call for call in docker.calls if call[0] == "create")
    assert create[create.index("--network") + 1] == "none"
    assert "--read-only" in create and "--cap-drop" in create
    assert "--publish" not in create
    assert IMAGE == create[-1]
    assert docker.calls[-1][0] == "rm"
    assert deployment.status()["selected"] is None
    assert deployment.select(IMAGE) == IMAGE
    assert deployment.status()["selected"] == IMAGE
    assert deployment.status()["current"] is None
    with pytest.raises(DeploymentError):
        deployment.select(OTHER)


def test_failed_retest_invalidates_proof_and_cleans_candidate(tmp_path):
    docker = DockerStub()
    deployment = Deployment(tmp_path, docker=docker)
    deployment.test(IMAGE)
    deployment.select(IMAGE)
    docker.fail_probe = True
    with pytest.raises(DeploymentError, match="backend diagnostics"):
        deployment.test(IMAGE)
    state = deployment.status()
    assert IMAGE not in state["validated"] and state["selected"] is None
    assert docker.calls[-1][0] == "rm"


def test_cleanup_failure_never_records_validation(tmp_path):
    deployment = Deployment(tmp_path, docker=DockerStub(fail_cleanup=True))
    with pytest.raises(DeploymentError, match="cleanup failed"):
        deployment.test(IMAGE)
    assert IMAGE not in deployment.status()["validated"]


def test_validation_rejects_invalid_session_before_touching_engine(tmp_path):
    source = tmp_path / "session.json"
    source.write_text("[]")
    docker = DockerStub()
    with pytest.raises(ValueError, match="canonical"):
        Deployment(tmp_path / "root", docker=docker).test(IMAGE, session=source)
    assert docker.calls == []


def test_state_lease_is_exclusive_and_released_after_failure(tmp_path):
    first, second = Deployment(tmp_path), Deployment(tmp_path)
    with first._state_lock():
        with pytest.raises(DeploymentError, match="busy"):
            with second._state_lock():
                pytest.fail("a second writer acquired the lease")
    with second._state_lock():
        pass


@pytest.mark.parametrize("payload", [
    PromptRequest(id="test", prompt="never run"), CompactRequest(id="test"),
    ConfigUpdateRequest(id="test", values={}),
])
def test_validation_worker_never_sends_queued_commands(payload):
    worker = WorkerSupervisor("unused")
    worker.validation = True
    with pytest.raises(RuntimeError, match="disabled during image validation"):
        asyncio.run(worker.send(payload))


def test_same_image_validation_cannot_overlap(tmp_path):
    from filelock import FileLock

    docker = DockerStub()
    deployment = Deployment(tmp_path, docker=docker)
    with FileLock(tmp_path / f"test-{IMAGE[7:]}.lock"):
        with pytest.raises(DeploymentError, match="already being tested"):
            deployment.test(IMAGE)
    assert docker.calls == []


def test_proof_binds_application_options_and_copies_inputs(tmp_path):
    docker = DockerStub()
    deployment = Deployment(tmp_path, docker=docker)
    options = {"feature": {"enabled": True}}
    deployment.test(IMAGE, options=options)
    assert docker.settings["options"] == options
    options["feature"]["enabled"] = False
    state = deployment.status()
    assert deployment._validated(state, IMAGE, {"feature": {"enabled": True}})
    assert not deployment._validated(state, IMAGE, options)
    with pytest.raises(ValueError, match="core factory"):
        deployment.test(OTHER, options={"validation": False})


def test_readiness_inspect_uses_remaining_budget(tmp_path):
    class TimedEngine(DockerStub):
        def inspect(self, name, *, timeout=60):
            assert 0 < timeout <= 1
            return super().inspect(name)

    Deployment(tmp_path, docker=TimedEngine())._wait_ready("candidate", timeout=1)


def test_project_wheels_are_built_outside_docker_context(tmp_path, monkeypatch):
    import the_framework.deployment.image as module

    project = tmp_path / "source"
    project.mkdir()
    (project / "pyproject.toml").touch()
    (project / ".env").write_text("private")

    def build(executable, *args, timeout):
        assert executable == "uv" and args[:2] == ("build", "--wheel")
        output = Path(args[3])
        output.mkdir()
        (output / "app.whl").write_bytes(b"wheel")

    class Builder:
        def run(self, *args, timeout):
            root = Path(args[-1])
            assert not (root / "source").exists()
            assert sorted(p.name for p in (root / "wheels").iterdir()) == ["app.whl"]
            assert not any(p.name == ".env" for p in root.rglob("*"))
            Path(args[2]).write_text(IMAGE)

    monkeypatch.setattr(module, "_command", build)
    recipe = BackendImage("app:create_app", projects=(project,))
    assert recipe.build(docker=Builder()) == IMAGE
    wheel = tmp_path / "app.whl"
    wheel.touch()
    with pytest.raises(ValueError, match="unique across"):
        BackendImage("app:create_app", wheels=(wheel,), projects=(project,)).build(docker=Builder())
