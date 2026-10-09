"""Canonical text and file ingress, optionally coordinated with live voice."""
from ...server.composition.application import CapabilityRequirement, Plugin

CHAT = Plugin(name="chat", server_extension="the_framework.plugins.chat.server:build_server_extensions",
              optional_capabilities=(CapabilityRequirement(name="realtime.session", min_version=1),))

__all__ = ["CHAT"]
