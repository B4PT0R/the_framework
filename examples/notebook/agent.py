"""Worker-side tools, command, state and live context; no database import."""

from modict import modict

from the_framework import AgentExtension, command, provider, tool
from the_framework.agent import ApplicationError, Config, State

from .models import identifier


class NotebookConfig(Config):
    recent_limit: int = 5

    @modict.validator("recent_limit", mode="after")
    def bounded_limit(self, value):
        if not 1 <= value <= 20:
            raise ValueError("recent_limit must be between 1 and 20")
        return value


class NotebookState(State):
    project: str = "inbox"

    @modict.validator("project", mode="after")
    def valid_project(self, value):
        return identifier(value)


class NotebookExtension(AgentExtension):
    name = "notebook"
    config = NotebookConfig
    state_schema = NotebookState
    instructions = (
        "Use notebook.save_note to store durable notes under stable keys. "
        "Saving the same project/key replaces that note. The current project "
        "comes from /project; do not invent a successful save when storage fails. "
        "Recent note previews contain only titles, not complete note bodies."
    )

    @command
    def project(self, name: str):
        """Select the notebook project: /project research."""
        self.persist_state(project=name)
        return {"project": self.plugin_state.project}

    @tool
    async def save_note(self, key: str, title: str, body: str):
        """Save or replace one note in the current project under a stable key."""
        return await self.agent.bridge.call("notebook", "save", {
            "project": self.plugin_state.project,
            "key": key, "title": title, "body": body,
        })

    @tool
    async def recent_notes(self, limit: int = 5):
        """Read the most recently saved notes, including their complete bodies."""
        return await self.agent.bridge.call("notebook", "recent", {
            "project": self.plugin_state.project, "limit": limit,
        })

    @provider(channels=("text",))
    async def notebook_context(self):
        """Current project and recent note titles, refreshed before every step."""
        project = self.plugin_state.project
        try:
            result = await self.agent.bridge.call("notebook", "recent", {
                "project": project, "limit": self.config.recent_limit,
            })
        except ApplicationError as error:
            return {"project": project, "status": "unavailable", "error": str(error)}
        return {"project": project, "status": "ready", "notes": [
            {"key": note["key"], "title": note["title"]}
            for note in result["notes"]
        ]}
