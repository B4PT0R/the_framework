"""Explicit conversation artifacts and durable, authenticated local assets."""

from ...server.composition.application import Plugin, ServerExtension
from .agent import ArtifactsExtension

ARTIFACTS = Plugin(
    name="artifacts", agent_extension=ArtifactsExtension,
    server_extensions=ServerExtension(
        name="artifacts", scope="session",
        service_factory="the_framework.plugins.artifacts.server:build_service",
    ),
)

__all__ = ["ARTIFACTS", "ArtifactsExtension"]
