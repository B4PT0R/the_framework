from ...server.composition.declarations import Plugin
from .agent import BrowserConfig, ChromiumExtension

def _build_agent_extension(agent):
    return ChromiumExtension(agent, runtime_root=agent.application_root)


CHROMIUM = Plugin(name="chromium", agent_extension=_build_agent_extension)

__all__ = ["CHROMIUM", "BrowserConfig", "ChromiumExtension"]
