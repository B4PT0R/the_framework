"""Opt-in generated-app deployment; `python -m starter.deployment --help`."""

from pathlib import Path

from the_framework.deployment.cli import main as deployment_main
from the_framework.server.clients.browser import BrowserService


def main():
    deployment_main(
        "starter.server:create_app",
        projects=(Path(__file__).resolve().parents[1],)
        if Path(__file__).with_name("instance.json").is_file() else (),
        browser_factory=lambda root: BrowserService(root, web_headless=True, ui_headless=True),
    )


if __name__ == "__main__":
    main()
