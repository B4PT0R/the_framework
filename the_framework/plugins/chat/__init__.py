"""Canonical text and file ingress, optionally coordinated with live voice."""
from ...server.composition.application import Capability, CapabilityRequirement, Plugin

CHAT = Plugin(name="chat", server_extension="the_framework.plugins.chat.server:build_server_extensions",
              provides_capabilities=(Capability(name="conversation.ingress", version=1),),
              optional_capabilities=(CapabilityRequirement(name="realtime.session", min_version=1),))

__all__ = ["CHAT"]
