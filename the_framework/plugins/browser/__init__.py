from pathlib import Path

from ...server.composition.application import Plugin
from .agent import BrowserConfig, ChromiumExtension

def _build_agent_extension(agent):
    if agent.session.path is None:
        raise ValueError("the browser plugin requires a session path or a custom runtime root")
    return ChromiumExtension(agent, runtime_root=Path(agent.session.path).parent)


CHROMIUM = Plugin(name="chromium", agent_extension=_build_agent_extension)

__all__ = ["CHROMIUM", "BrowserConfig", "ChromiumExtension"]
