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
    """Process-local client and initial defaults; resumable values live in the session."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)
    client: object | None = None
    configuration: Mapping[str, object] = modict.factory(dict)
    state: Mapping[str, object] | None = None


class AgentSpec(modict):
    """A reusable declaration of agent identity, plugins and worker behavior."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str
    description: str = "Agent worker."
    extensions: tuple[object, ...] = ()
    instructions: tuple[Instruction | dict, ...] = ()
    initializers: tuple[object, ...] = ()
    command_middleware: tuple[object, ...] = ()
    projections: Mapping[str, object] = modict.factory(dict)
    resources: Callable[[str | Path | None], AgentResources] | None = None
    configuration: Mapping[str, object] = modict.factory(dict)
    idle_timeout_seconds: float | int | None = None
    completion_tool: str | None = None
    completion_retry_limit: int = 0
    queue: QueuePolicy = modict.factory(QueuePolicy)

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
        if key in {"extensions", "instructions", "initializers", "command_middleware"}:
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
            extensions=tuple(profile.plugins),
            instructions=(Instruction(name="specialist_role", content=profile.instructions),),
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

    def with_extensions(self, *plugins):
        return type(self)({**self, "extensions": (*self.extensions, *plugins)})

    def fleet_profile(self, *, identity=None, observes=("agent.task.requested",),
                      item_kinds=(), conversation_roles=(), input_item_field=None,
                      session_mode="ephemeral"):
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
            session_mode=session_mode,
            durable_tasks=self.queue.durable,
            backlog_limit=self.queue.backlog_limit,
            model=model,
            agent_config=configuration,
            plugins=[plugin for plugin in self.extensions if isinstance(plugin, str)],
            idle_timeout_seconds=self.idle_timeout_seconds,
            completion_tool=self.completion_tool,
            completion_retry_limit=self.completion_retry_limit,
            observes=list(observes),
            item_kinds=list(item_kinds),
            conversation_roles=list(conversation_roles),
            input_item_field=input_item_field,
        )

    @staticmethod
    def _resolve_extension(contribution, agent):
        from .extensions.extension import AgentExtension
        binding_name = contribution.name if hasattr(contribution, "agent_extension") else None
        contribution = getattr(contribution, "agent_extension", contribution)
        if contribution is None:
            return None
        if isinstance(contribution, str):
            try:
                module_name, attribute = contribution.split(":", 1)
            except ValueError as error:
                raise ValueError(
                    "agent extension references must use module:attribute"
                ) from error
            contribution = getattr(import_module(module_name), attribute)
        if isinstance(contribution, type) and issubclass(contribution, AgentExtension):
            contribution = contribution(agent)
        elif not isinstance(contribution, AgentExtension) and callable(contribution):
            contribution = contribution(agent)
            if not isinstance(contribution, AgentExtension):
                raise TypeError("agent extension factory must return an AgentExtension instance")
        if binding_name is not None:
            if contribution.loaded and contribution.title != binding_name:
                raise ValueError("cannot rename an already loaded agent extension")
            contribution.name = binding_name
        return contribution

    def build_agent(
        self,
        session=None,
        *,
        session_root=None,
        resources: AgentResources | None = None,
        application_root=None,
        profile: WorkerProfile | None = None,
    ):
        """Construct with declaration < resources < launch profile < saved session.

        The Session owns storage and history retention; omission creates an
        in-memory session. session_root supplies a workspace without persistence.
        A profile is consumed only at the worker boundary, never retained as a
        competing declaration. Executable factories and initializers stay local.
        """
        from .runtime.agent import Agent
        from .context.session import Session
        session = Session() if session is None else session
        if not isinstance(session, Session):
            raise TypeError("build_agent requires a Session, not a storage path")
        session_path = session.path
        if session_path is None and session_root is not None:
            session_path = Path(session_root) / "session.json"
        resolved = type(self).from_profile(profile) if profile is not None else None
        if resolved is not None and resolved.name != self.name:
            raise ValueError("execution profile belongs to another agent declaration")
        # Explicit resources replace the factory as a whole; there is no hidden
        # merge or fallback between independently supplied construction inputs.
        if resources is None:
            resources = self.resources(session_path) if self.resources else AgentResources()
        if not isinstance(resources, AgentResources):
            raise TypeError("agent resources must be AgentResources")
        options = dict(self.configuration)
        options.update(resources.configuration)
        if resolved is not None:
            options.update(resolved.configuration)
        if resources.client is not None:
            options["client"] = resources.client
        agent = Agent(
            session=session,
            name=self.name,
            application_root=application_root,
            session_root=Path(session_path).parent if session_path is not None else None,
            description=self.description,
            state=resources.state,
            **options,
        )
        if agent.session.agent_name not in (None, self.name):
            raise ValueError("session belongs to another agent declaration")
        agent.session.agent_name = self.name
        for instruction in resolved.instructions if resolved is not None else self.instructions:
            agent.add_instruction(
                instruction
                if isinstance(instruction, Instruction)
                else Instruction(**instruction)
            )
        for contribution in self.extensions:
            plugin = self._resolve_extension(contribution, agent)
            if plugin is not None:
                agent.add_extension(
                    plugin,
                    activate=getattr(contribution, "agent_binding_enabled", None),
                    required=getattr(contribution, "agent_binding_required", False),
                )
        for initialize in self.initializers:
            result = initialize(agent)
            if result is not None and result is not agent:
                raise ValueError("agent initializer must return the agent or None")
        agent.session.update_agent(configuration=agent.configs, state=agent.states.snapshot())
        return agent

    def build_worker(self, agent, receive, send, *, specialists=()):
        from .runtime.worker import Worker
        return Worker(
            agent,
            receive,
            send,
            command_middleware=self.command_middleware,
            projections=SessionProjections(self.projections),
            specialists=specialists,
        )


__all__ = ["AgentResources", "AgentSpec", "QueuePolicy"]
