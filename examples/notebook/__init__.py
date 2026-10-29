"""A complete session-scoped feature for the editable starter."""

from the_framework import Capability, Plugin, ServerExtension

from .agent import NotebookExtension

NOTEBOOK = Plugin(
    name="notebook",
    agent_extension=NotebookExtension,
    provides_capabilities=(Capability(name="example.notebook"),),
    server_extensions=(ServerExtension(
        name="notebook_store",
        scope="session",
        service_factory=f"{__name__}.server:NotebookService",
    ),),
)
