from ...server.composition.application import Plugin
from .agent import RegistryConfig, RegistryExtension, RegistryStore

REGISTRY = Plugin(
    name="registry", agent_extension=RegistryExtension,
    server_extension="the_framework.plugins.registry.live:build_server_extensions",
)

__all__ = ["REGISTRY", "RegistryConfig", "RegistryExtension", "RegistryStore"]
