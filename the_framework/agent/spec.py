"""Declarative construction of an agent and its worker runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from importlib import import_module
from types import MappingProxyType
from typing import Literal

from modict import modict

from .extensions.instructions import Instruction
from .context.projections import SessionProjections
from .models.worker import WorkerProfile


def _tuple(value):
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


class SessionPolicy(modict):
    """Persistence policy for one agent session."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", enforce_json=True)
    mode: Literal["ephemeral", "resident", "durable"]

    @classmethod
    def ephemeral(cls):
        return cls(mode="ephemeral")

    @classmethod
    def resident(cls):
        return cls(mode="resident")

    @classmethod
    def durable(cls):
        return cls(mode="durable")


class QueuePolicy(modict):
    _config = modict.config(frozen=True, strict=True, extra="forbid", enforce_json=True)
    mode: Literal["fifo", "latest"] = "fifo"
    limit: int = 8
    durable: bool = True
    backlog_limit: int = 512

    @modict.model_validator(mode="after")
    def validate_limits(self):
        if self.limit <= 0 or self.backlog_limit < self.limit:
            raise ValueError("invalid agent queue limits")

    @classmethod
    def fifo(cls, *, limit=8, durable=True, backlog_limit=512):
        return cls(mode="fifo", limit=limit, durable=durable, backlog_limit=backlog_limit)

    @classmethod
    def latest(cls, *, limit=1, durable=False, backlog_limit=None):
        return cls(mode="latest", limit=limit, durable=durable,
                   backlog_limit=limit if backlog_limit is None else backlog_limit)


class AgentResources(modict):
    """Process-local inputs and persistence callbacks for constructing an agent."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)
    client: object | None = None
    configuration: Mapping[str, object] = modict.factory(dict)
    state: Mapping[str, object] | None = None
    persist_config: Callable | None = None
    persist_state: Callable | None = None


class AgentSpec(modict):
    """A reusable declaration of agent identity, plugins and worker behavior."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str
    description: str = "Agent worker."
    plugins: tuple[object, ...] = ()
    instructions: tuple[Instruction | dict, ...] = ()
    initializers: tuple[object, ...] = ()
    command_middleware: tuple[object, ...] = ()
    projections: Mapping[str, object] = modict.factory(dict)
    session: SessionPolicy = modict.factory(SessionPolicy.ephemeral)
    resources: Callable[[str | Path | None], AgentResources] | None = None
    configuration: Mapping[str, object] = modict.factory(dict)
    idle_timeout_seconds: float | int | None = None
    completion_tool: str | None = None
    completion_retry_limit: int = 0
    queue: QueuePolicy = modict.factory(QueuePolicy)
    specialists: tuple[WorkerProfile, ...] = ()

    @modict.model_validator(mode="after")
    def validate_declaration(self):
        if not self.name.strip():
            raise ValueError("agent name is required")
        if not self.description.strip():
            raise ValueError("agent description is required")
        SessionProjections(self.projections)
        if self.idle_timeout_seconds is not None and self.idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be positive")
        if self.completion_retry_limit < 0 or (self.completion_tool and not self.completion_retry_limit):
            raise ValueError("invalid completion retry limit")

    @modict.any_validator(mode="before")
    def normalize_declaration(self, key, value):
        if key == "instructions" and isinstance(value, str):
            return (Instruction(name="role", content=value),)
        if key in {"plugins", "instructions", "initializers", "command_middleware", "specialists"}:
            return _tuple(value)
        if key in {"projections", "configuration"}:
            return MappingProxyType(dict(value))
        return value

    @classmethod
    def from_profile(cls, profile):
        profile = WorkerProfile(**profile)
        return cls(
            name=profile.name,
            description=profile.description or f"Private specialist {profile.name}.",
            plugins=tuple(profile.plugins),
            instructions=(Instruction(name="specialist_role", content=profile.instructions),),
            session=SessionPolicy(mode=profile.session_mode),
            queue=QueuePolicy(
                mode=profile.queue_policy,
                limit=profile.queue_limit,
                durable=profile.durable_tasks,
                backlog_limit=profile.backlog_limit,
            ),
            configuration={**profile.agent_config, **profile.plugin_config, "model": profile.model},
            idle_timeout_seconds=profile.idle_timeout_seconds,
            completion_tool=profile.completion_tool,
            completion_retry_limit=profile.completion_retry_limit,
        )

    def with_plugins(self, *plugins):
        return type(self)({**self, "plugins": (*self.plugins, *plugins)})

    def with_specialists(self, *profiles):
        return type(self)({**self, "specialists": tuple(profiles)})

    def fleet_profile(self, *, identity=None, observes=("agent.task.requested",),
                      item_kinds=(), conversation_roles=(), input_item_field=None):
        """Compile scheduling metadata for the server-owned specialist fleet."""
        instruction_text = "\n\n".join(
            value.content if isinstance(value, Instruction) else value["content"]
            for value in self.instructions
        ).strip()
        configuration = dict(self.configuration)
        model = configuration.pop("model", "gpt-5.6-luna")
        return WorkerProfile(
            name=identity or self.name,
            description=self.description,
            instructions=instruction_text or self.description,
            queue_limit=self.queue.limit,
            queue_policy=self.queue.mode,
            session_mode=self.session.mode,
            durable_tasks=self.queue.durable,
            backlog_limit=self.queue.backlog_limit,
            model=model,
            agent_config=configuration,
            plugins=[plugin for plugin in self.plugins if isinstance(plugin, str)],
            idle_timeout_seconds=self.idle_timeout_seconds,
            completion_tool=self.completion_tool,
            completion_retry_limit=self.completion_retry_limit,
            observes=list(observes),
            item_kinds=list(item_kinds),
            conversation_roles=list(conversation_roles),
            input_item_field=input_item_field,
        )

    @staticmethod
    def _plugin(contribution, agent):
        from .extensions.plugin import AgentPlugin
        resolver = getattr(contribution, "agent_plugin", None)
        if resolver is not None:
            contribution = resolver()
        else:
            contribution = getattr(contribution, "agent", contribution)
        if contribution is None:
            return None
        if isinstance(contribution, str):
            try:
                module_name, attribute = contribution.split(":", 1)
            except ValueError as error:
                raise ValueError(
                    "agent plugin references must use module:attribute"
                ) from error
            contribution = getattr(import_module(module_name), attribute)
        if isinstance(contribution, AgentPlugin):
            return contribution
        if isinstance(contribution, type) and issubclass(contribution, AgentPlugin):
            return contribution
        if callable(contribution):
            plugin = contribution(agent)
            if not isinstance(plugin, AgentPlugin):
                raise TypeError("agent plugin factory must return an AgentPlugin instance")
            return plugin
        return contribution

    def build_agent(
        self,
        session_path=None,
        *,
        resources: AgentResources | None = None,
    ):
        from .runtime.agent import Agent
        # Explicit resources replace the factory as a whole; there is no hidden
        # merge or fallback between independently supplied construction inputs.
        if resources is None:
            resources = self.resources(session_path) if self.resources else AgentResources()
        if not isinstance(resources, AgentResources):
            raise TypeError("agent resources must be AgentResources")
        options = dict(self.configuration)
        options.update(resources.configuration)
        if resources.client is not None:
            options["client"] = resources.client
        agent = Agent(
            session_path=session_path if self.session.mode == "durable" else None,
            name=self.name,
            description=self.description,
            retain_history=self.session.mode != "ephemeral",
            state=resources.state,
            **options,
        )
        agent.persist_config = resources.persist_config
        agent.persist_state = resources.persist_state
        for instruction in self.instructions:
            agent.add_instruction(
                instruction
                if isinstance(instruction, Instruction)
                else Instruction(**instruction)
            )
        for contribution in self.plugins:
            plugin = self._plugin(contribution, agent)
            if plugin is not None:
                agent.add_plugin(
                    plugin,
                    activate=getattr(contribution, "binding_enabled", None),
                )
        for initialize in self.initializers:
            result = initialize(agent)
            if result is not None and result is not agent:
                raise ValueError("agent initializer must return the agent or None")
        return agent

    def build_worker(self, agent, receive, send):
        from .runtime.worker import Worker
        return Worker(
            agent,
            receive,
            send,
            command_middleware=self.command_middleware,
            projections=SessionProjections(self.projections),
            specialists=self.specialists,
        )


__all__ = ["AgentResources", "AgentSpec", "QueuePolicy", "SessionPolicy"]
