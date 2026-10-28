from ...server.composition.declarations import Plugin
from .agent import RegistryConfig, RegistryExtension, RegistryStore

REGISTRY = Plugin(
    name="registry", agent_extension=RegistryExtension,
    configure="the_framework.plugins.registry.live:configure",
)

__all__ = ["REGISTRY", "RegistryConfig", "RegistryExtension", "RegistryStore"]
