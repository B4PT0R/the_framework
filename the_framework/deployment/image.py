"""Build immutable backend artifacts from an explicit, private build context."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class DeploymentError(RuntimeError):
    """A build, validation or deployment failed without authorizing promotion."""


def image_id(value: str) -> str:
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise ValueError("an immutable sha256 image ID is required, not a tag")
    return value


def _command(executable, *args, timeout=60):
    # File capture bounds memory even when a build emits a very large log.
    with tempfile.TemporaryFile() as log:
        try:
            with subprocess.Popen(
                [executable, *map(str, args)], stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            ) as process:
                try:
                    code = process.wait(timeout=timeout)
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DeploymentError(f"{Path(executable).name} {args[0]} could not complete: {type(error).__name__}") from error
        log.seek(0, 2)
        size = log.tell()
        log.seek(max(0, size - 16000))
        text = log.read().decode("utf-8", errors="replace").strip()
    if code:
        raise DeploymentError(f"{Path(executable).name} {args[0]} failed ({code}):\n{text}")
    return text


class Docker:
    """The CLI boundary: argument arrays, bounded calls, no shell or SDK daemon."""

    def __init__(self, executable="docker"):
        self.executable = executable

    def run(self, *args, timeout=60):
        return _command(self.executable, *args, timeout=timeout)

    def inspect(self, container, *, timeout=60):
        return {"State": json.loads(self.run("inspect", "--format", "{{json .State}}", container, timeout=timeout))}

    def remove(self, container):
        try:
            self.run("rm", "--force", container, timeout=30)
        except DeploymentError as error:
            if "No such container" not in str(error):
                raise


@dataclass(frozen=True)
class BackendImage:
    """A factory and its wheels, not a repository or a mutable source mount.

    The factory receives keyword-only data_root, token, browser_token, origin,
    restart and validation. It must return an ASGI app without starting it.
    Wheels include the application, Framework and any local dependencies. Other
    dependencies are resolved by pip in the build. OS packages are explicit.
    """

    factory: str
    wheels: tuple[Path, ...] = ()
    projects: tuple[Path, ...] = ()
    base: str = "python:3.12-slim"
    packages: tuple[str, ...] = ()

    def __post_init__(self):
        if not re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*", self.factory):
            raise ValueError("factory must be a module:function reference")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9./:@_-]*", self.base):
            raise ValueError("invalid base image reference")
        if any(not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", item) for item in self.packages):
            raise ValueError("invalid OS package name")
        wheels = tuple(Path(path).expanduser().resolve() for path in self.wheels)
        projects = tuple(Path(path).expanduser().resolve() for path in self.projects)
        if not wheels and not projects:
            raise ValueError("explicit wheel files or Python projects are required")
        if any(not (path / "pyproject.toml").is_file() for path in projects):
            raise ValueError("projects must contain a pyproject.toml")
        if any(not path.is_file() or path.suffix != ".whl" for path in wheels):
            raise ValueError("explicit existing wheel files are required")
        if len({path.name for path in wheels}) != len(wheels):
            raise ValueError("wheel filenames must be unique")
        object.__setattr__(self, "wheels", wheels)
        object.__setattr__(self, "projects", projects)

    def build(self, *, docker=None, timeout=900):
        """Build only; return the exact local ID, never select or launch it."""
        docker = docker or Docker()
        with tempfile.TemporaryDirectory(prefix="backend-build-") as directory:
            root = Path(directory)
            (root / "wheels").mkdir()
            wheels = list(self.wheels)
            for index, project in enumerate(self.projects):
                # Explicit host-side wheel build; sources never enter Docker.
                output = root / f"project-{index}"
                _command("uv", "build", "--wheel", "--out-dir", output, project, timeout=timeout)
                built = list(output.glob("*.whl"))
                if len(built) != 1:
                    raise DeploymentError("project build must produce exactly one wheel")
                wheels.extend(built)
            for wheel in wheels:
                if (root / "wheels" / wheel.name).exists():
                    raise ValueError("wheel filenames must be unique across projects and inputs")
                shutil.copyfile(wheel, root / "wheels" / wheel.name)
            (root / "backend.json").write_text(json.dumps({"factory": self.factory}), encoding="utf-8")
            packages = (
                "RUN apt-get update && apt-get install -y --no-install-recommends "
                + " ".join(self.packages) + " && rm -rf /var/lib/apt/lists/*\n"
                if self.packages else ""
            )
            (root / "Dockerfile").write_text(
                f"FROM {self.base}\n" + packages
                + "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
                + "COPY wheels /wheels\n"
                + "RUN python -m pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels\n"
                + "COPY backend.json /opt/backend.json\n"
                + 'LABEL org.the-framework.backend="1"\n'
                + 'ENTRYPOINT ["python", "-m", "the_framework.deployment.runner"]\n',
                encoding="utf-8",
            )
            identifier = root / "image.id"
            docker.run("build", "--iidfile", identifier, root, timeout=timeout)
            return image_id(identifier.read_text(encoding="utf-8").strip())
