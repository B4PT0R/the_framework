"""Start here: identity, plugins and persistence for your own application."""

from pathlib import Path

from the_framework import (
    ApplicationSpec,
    AgentResources,
    AgentSpec,
    Capability,
    CapabilityRequirement,
    ClientSurface,
    ServerExtension,
    Plugin,
)
from the_framework.agent import Worker
from the_framework.utils.persistence import MappingStore
from the_framework.plugins.bash import BashExtension
from the_framework.plugins.browser import ChromiumExtension
from the_framework.plugins.memory.agent import MemoryExtension
from the_framework.plugins.realtime import RealtimeExtension
from the_framework.plugins.registry import RegistryExtension
from the_framework.plugins.scheduler import SchedulerExtension
from the_framework.plugins.scheduler.service import SchedulerService
from the_framework.plugins.system import SystemExtension
from the_framework.plugins.system.service import SystemControlService
from the_framework.plugins.web_search import WebSearchExtension
from the_framework.plugins.realtime.controller import RealtimeController

from .voice import VoiceApi

ROOT = Path(__file__).resolve().parent


class Memory(MemoryExtension):
    curator_instructions = (ROOT / "memory.md").read_text(encoding="utf-8")


def browser(agent):
    return ChromiumExtension(agent, runtime_root=Path(agent.session.path).parent)


def scheduler_runtime(context):
    root = Path(context.require("data_root"))

    def create(runtime):
        scheduler = SchedulerService(root / "scheduler.json", runtime.submit)
        runtime.application.register("scheduler", scheduler.handle)
        runtime.register_observer(scheduler.observe)
        return scheduler

    return ServerExtension(
        name="scheduler",
        service_factory=create,
        requires_services=("runtime",),
    )


def system_runtime(context):
    root = Path(context.require("data_root"))
    restart = context.get("restart")

    def create(runtime):
        system = SystemControlService(
            root / "system.json", runtime.submit, restart=restart,
        )
        runtime.application.register("system", system.handle, contextual=True)
        runtime.register_observer(system.observe)
        return system

    return ServerExtension(
        name="system",
        service_factory=create,
        requires_services=("runtime",),
    )


def realtime_runtime(_context):
    return ServerExtension(
        name="voice",
        service_factory=lambda runtime: VoiceApi(runtime, RealtimeController(runtime)),
        requires_services=("runtime",),
        stop=lambda service, _context: service.controller.stop(),
        endpoints="service",
    )


def resources(session_path):
    """Keep the user's configuration and plugin state beside their session."""
    if session_path is None:
        raise ValueError("the starter requires a persistent session path")
    root = Path(session_path).resolve().parent
    settings = MappingStore(root / "settings.json", field="settings")
    state = MappingStore(root / "state.json", field="state")
    return AgentResources(
        configuration={"model": "gpt-5.6-luna", "bash": {"default_cwd": str(ROOT)}, **settings.load()},
        state=state.load(),
        persist_config=settings.save,
        persist_state=state.save,
    )


application = ApplicationSpec(
    name="Pandora",
    version="0.1.0",
    primary_agent=AgentSpec(
        name="assistant",
        description="Pandora, a persistent local agent that can evolve her own application.",
        session_mode='durable',
        instructions=(ROOT / "instructions.md").read_text(encoding="utf-8"),
        resources=resources,
        projections={"display": Worker.conversation_projection},
    ),
    plugins=(
        BashExtension, RegistryExtension, WebSearchExtension, Memory,
        Plugin(
            name="chat", server_extension="starter.chat:chat_runtime",
            optional_capabilities=(CapabilityRequirement(name="realtime.session", min_version=1),),
        ),
        Plugin(name="scheduler", agent_extension=SchedulerExtension, server_extension=scheduler_runtime),
        Plugin(name="system", agent_extension=SystemExtension, server_extension=system_runtime),
        Plugin(
            name="realtime", agent_extension=RealtimeExtension, server_extension=realtime_runtime,
            provides_capabilities=(Capability(name="realtime.session", version=1),),
        ),
        Plugin(name="chromium", agent_extension=browser),
    ),
    surfaces=(ClientSurface(name="main", source=ROOT / "ui",
                            build=("npm", "run", "build"), artifact="dist",
                            routes=("/ui",), shell="playwright"),),
    docs_url=None,
)
