"""Create an editable local agent application from the bundled starter."""

import argparse
import json
import shutil
from importlib.resources import files
from pathlib import Path


def bootstrap(code_dir: Path, data_dir: Path) -> None:
    code = code_dir.expanduser().resolve()
    data = data_dir.expanduser().resolve()
    if code == data or code in data.parents or data in code.parents:
        raise ValueError("code and data directories must be separate")
    for path in (code, data):
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise ValueError(f"directory must be absent or empty: {path}")

    template = files("harness_core").joinpath("_starter")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harness-core", description="Create a local agent application"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("bootstrap", help="copy the editable starter application")
    create.add_argument("--code-dir", type=Path, help="directory for application source")
    create.add_argument("--data-dir", type=Path, help="private directory for application data")
    args = parser.parse_args(argv)
    code = args.code_dir or Path(input("Code directory: ").strip())
    data = args.data_dir or Path(input("Application data directory: ").strip())
    if not str(code) or not str(data) or code == Path(".") or data == Path("."):
        parser.error("both directories must be provided")
    try:
        bootstrap(code, data)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Created application in {code.expanduser().resolve()}")
    print(f"Private data directory: {data.expanduser().resolve()}")
    print("Next: from the code directory, run:")
    print("  npm --prefix starter/ui ci")
    print("  npm --prefix starter/ui run build")
    print("  python -m starter")
    return 0
