from ...server.composition.declarations import Plugin
from .agent import BashConfig, BashExtension

BASH = Plugin(
    name="bash", agent_extension=BashExtension,
)

__all__ = ["BASH", "BashConfig", "BashExtension"]
