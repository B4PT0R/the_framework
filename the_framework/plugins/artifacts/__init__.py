"""Explicit conversation artifacts and durable, authenticated local assets."""

from ...server.composition.application import Plugin
from .agent import ArtifactsExtension

ARTIFACTS = Plugin(
    name="artifacts", agent_extension=ArtifactsExtension,
    server_extension="the_framework.plugins.artifacts.server:build_server_extensions",
)

__all__ = ["ARTIFACTS", "ArtifactsExtension"]
