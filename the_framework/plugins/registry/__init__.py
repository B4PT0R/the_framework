from ...server.composition.application import Plugin
from .agent import RegistryConfig, RegistryExtension, RegistryStore

REGISTRY = Plugin(
    name="registry", agent_extension=RegistryExtension,
)

__all__ = ["REGISTRY", "RegistryConfig", "RegistryExtension", "RegistryStore"]
