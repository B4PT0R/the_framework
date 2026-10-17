"""Create, prepare and run an editable local agent application."""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from importlib.resources import files
from importlib.metadata import version
from pathlib import Path

from .utils.persistence import atomic_text_writer


def _project_manifest():
    distribution = "b4pt0r-the-framework"
    manifest = (
        '[project]\nname = "local-agent-app"\nversion = "0.1.0"\n'
        'requires-python = ">=3.12"\n'
        f'dependencies = [{json.dumps(distribution + "==" + version(distribution))}]\n'
        '\n[tool.uv]\npackage = false\n'
    )
    # A development bootstrap uses the same checkout; installed wheels use PyPI.
    source = Path(__file__).resolve().parent.parent
    project = source / "pyproject.toml"
    if project.is_file() and tomllib.loads(project.read_text())["project"]["name"] == distribution:
        manifest += (
            '\n[tool.uv.sources]\n'
            f'{json.dumps(distribution)} = {{ path = {json.dumps(str(source))}, editable = true }}\n'
        )
    return manifest

def bootstrap(code_dir: Path, data_dir: Path) -> None:
    code = code_dir.expanduser().resolve()
    data = data_dir.expanduser().resolve()
    if code == data or code in data.parents or data in code.parents:
        raise ValueError("code and data directories must be separate")
    for path in (code, data):
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise ValueError(f"directory must be absent or empty: {path}")

    template = files("the_framework").joinpath("_starter")
    if not template.is_dir():
        # In an editable source checkout, the canonical template is at root.
        template = Path(__file__).resolve().parent.parent / "starter"
    code.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    data.chmod(0o700)
    target = code / "starter"
    target.mkdir()
    for source in template.iterdir():
        if source.name in {"__pycache__", "node_modules", "dist"}:
            continue
        destination = target / source.name
        if source.is_dir():
            shutil.copytree(
                source, destination,
                ignore=shutil.ignore_patterns("__pycache__", "node_modules", "dist", "*.pyc"),
            )
        else:
            shutil.copyfile(source, destination)
    (target / "instance.json").write_text(
        json.dumps({"data_dir": str(data)}, indent=2) + "\n", encoding="utf-8"
    )
    (code / "pyproject.toml").write_text(_project_manifest(), encoding="utf-8")
    (code / ".gitignore").write_text(
        ".venv/\n.framework/\n__pycache__/\n*.pyc\n"
        "starter/instance.json\nstarter/ui/node_modules/\nstarter/ui/dist/\n",
        encoding="utf-8",
    )


def _tools():
    missing = [name for name in ("uv", "node", "npm") if shutil.which(name) is None]
    if missing:
        raise ValueError("Missing prerequisites: " + ", ".join(missing) + ". Install uv and Node.js/npm first.")


def _application(code_dir):
    code = code_dir.expanduser().resolve()
    required = ("pyproject.toml", "starter/instance.json", "starter/application.py", "starter/ui/package-lock.json")
    if any(not (code / name).is_file() for name in required):
        raise ValueError(f"not a bootstrapped application: {code}")
    return code


def _run(command, code, *, check=True):
    environment = dict(os.environ)
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("PYTHONPATH", None)
    environment["UV_PROJECT_ENVIRONMENT"] = str(code / ".venv")
    return subprocess.run(command, cwd=code, env=environment, check=check)


def _fingerprint(root, *, packages_only=False):
    digest = hashlib.sha256()
    for folder, directories, names in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in {"node_modules", "dist", ".git"})
        for name in sorted(names):
            path = Path(folder) / name
            if packages_only and path.relative_to(root).as_posix() not in {"package.json", "package-lock.json"}:
                continue
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare(code_dir):
    """Prepare a private Python environment, Chromium and the editable web UI.

    Successful npm stages are checkpointed independently. Failed preparations
    leave source/data intact and can be resumed with the same command.
    """
    code = _application(code_dir)
    _tools()
    state_path = code / ".framework" / "prepare.json"
    try:
        state = json.loads(state_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict):
        state = {}

    def checkpoint(key, value):
        state[key] = value
        with atomic_text_writer(state_path) as stream:
            json.dump(state, stream)

    print("Preparing application Python environment…", flush=True)
    _run(["uv", "sync"], code)
    print("Preparing Chromium…", flush=True)
    _run(["uv", "run", "--no-sync", "python", "-m", "playwright", "install", "chromium"], code)
    ui = code / "starter" / "ui"
    packages = _fingerprint(ui, packages_only=True)
    if state.get("packages") != packages or not (ui / "node_modules").is_dir():
        print("Installing interface dependencies…", flush=True)
        _run(["npm", "--prefix", str(ui), "ci"], code)
        checkpoint("packages", packages)
    source = _fingerprint(ui)
    if state.get("build") != source or not (ui / "dist" / "index.html").is_file():
        print("Building interface…", flush=True)
        _run(["npm", "--prefix", str(ui), "run", "build"], code)
        checkpoint("build", source)
    print(f"Application ready: {code}", flush=True)


def run_application(code_dir, *, port=0, no_browser=False, headless=False):
    code = _application(code_dir)
    prepare(code)
    command = ["uv", "run", "--no-sync", "python", "-m", "starter", "--port", str(port)]
    if no_browser:
        command.append("--no-browser")
    if headless:
        command.append("--headless")
    return _run(command, code, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="the-framework", description="Create, prepare and run an editable local agent application"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("bootstrap", help="create and prepare an editable application")
    create.add_argument("--code-dir", type=Path, help="directory for application source")
    create.add_argument("--data-dir", type=Path, help="private directory for application data")
    mode = create.add_mutually_exclusive_group()
    mode.add_argument("--no-prepare", action="store_true", help="copy source only; prepare later")
    mode.add_argument("--run", action="store_true", help="start the application after creation")
    setup = sub.add_parser("prepare", help="prepare or resume application setup")
    setup.add_argument("--code-dir", type=Path, default=Path.cwd())
    run = sub.add_parser("run", help="prepare if needed and run the application")
    run.add_argument("--code-dir", type=Path, default=Path.cwd())
    run.add_argument("--port", type=int, default=0)
    run.add_argument("--no-browser", action="store_true")
    run.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare(args.code_dir)
            return 0
        if args.command == "run":
            return run_application(args.code_dir, port=args.port, no_browser=args.no_browser, headless=args.headless)
        code_value = str(args.code_dir) if args.code_dir is not None else input("Code directory: ").strip()
        data_value = str(args.data_dir) if args.data_dir is not None else input("Application data directory: ").strip()
        if not code_value or not data_value:
            raise ValueError("both directories must be provided")
        code, data = Path(code_value), Path(data_value)
        if not args.no_prepare:
            _tools()
        bootstrap(code, data)
        print(f"Created application in {code.expanduser().resolve()}", flush=True)
        print(f"Private data directory: {data.expanduser().resolve()}", flush=True)
        if args.run:
            return run_application(code)
        if not args.no_prepare:
            prepare(code)
        print("Next: " + shlex.join(["the-framework", "run", "--code-dir", str(code.expanduser().resolve())]))
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"Command failed ({exc.returncode}): {shlex.join(map(str, exc.cmd))}", file=sys.stderr)
        print("Source and data were kept. Resume with: " + shlex.join([
            "the-framework", "prepare", "--code-dir", str(args.code_dir or code),
        ]), file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        return 130
