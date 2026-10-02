from ...server.composition.application import Plugin
from .agent import WebSearchConfig, WebSearchExtension

WEB_SEARCH = Plugin(
    name="web_search", agent_extension=WebSearchExtension,
)

__all__ = ["WEB_SEARCH", "WebSearchConfig", "WebSearchExtension"]
