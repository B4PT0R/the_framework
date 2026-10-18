"""Deterministic agents for real multi-worker session contracts."""
from the_framework import Application, AgentSpec, AgentExtension, Plugin
from the_framework.agent import Config, State, Worker, hook


class Settings(Config):
    label: str = "default"


class Progress(State):
    count: int = 0


class Feature(AgentExtension):
    name = "feature"
    config = Settings
    state_schema = Progress

    @hook
    def on_external_record(self, payload):
        self.agent.add_message("assistant", payload["text"])
        self.persist_state(count=self.plugin_state.count + 1)


application = Application(
    name="Concurrent sessions", version="1",
    primary_agent=AgentSpec(name="first", session_mode="durable", projections={"display": Worker.conversation_projection}),
    agents=(AgentSpec(name="second", session_mode="durable", projections={"display": Worker.conversation_projection}),),
    plugins=(Plugin(name="feature", agent_extension=Feature),),
)
