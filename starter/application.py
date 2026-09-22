"""Start here: identity, plugins and persistence for your own application."""

from pathlib import Path

from the_framework import (
    AgentApplication,
    AgentResources,
    AgentSpec,
    Capability,
    CapabilityRequirement,
    ClientSurface,
    Extension,
    Plugin,
    SessionPolicy,
)
from the_framework.agent import Worker
from the_framework.utils.persistence import MappingStore
from the_framework.plugins.bash import BashPlugin
from the_framework.plugins.browser import ChromiumPlugin
from the_framework.plugins.memory.plugin import MemoryPlugin
from the_framework.plugins.realtime import RealtimePlugin
from the_framework.plugins.registry import RegistryPlugin
from the_framework.plugins.scheduler import SchedulerPlugin
from the_framework.plugins.scheduler.service import SchedulerService
from the_framework.plugins.system import SystemPlugin
from the_framework.plugins.system.service import SystemControlService
from the_framework.plugins.web_search import WebSearchPlugin
from the_framework.server.runtime.realtime import RealtimeController

from .voice import VoiceApi

ROOT = Path(__file__).resolve().parent


class Memory(MemoryPlugin):
    curator_instructions = (ROOT / "memory.md").read_text(encoding="utf-8")


def browser(agent):
    return ChromiumPlugin(agent, runtime_root=Path(agent.session.path).parent)


def scheduler_runtime(context):
    root = Path(context.require("data_root"))

    def create(runtime):
        scheduler = SchedulerService(root / "scheduler.json", runtime.submit)
        runtime.application.register("scheduler", scheduler.handle)
        runtime.register_observer(scheduler.observe)
        return scheduler

    return Extension(
        name="scheduler",
        service_factory=create,
        requires=("runtime",),
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

    return Extension(
        name="system",
        service_factory=create,
        requires=("runtime",),
    )


def realtime_runtime(_context):
    return Extension(
        name="voice",
        service_factory=lambda runtime: VoiceApi(runtime, RealtimeController(runtime)),
        requires=("runtime",),
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


application = AgentApplication(
    name="Pandora",
    version="0.1.0",
    primary_agent=AgentSpec(
        name="assistant",
        description="Pandora, a persistent local agent that can evolve her own application.",
        session=SessionPolicy.durable(),
        instructions=(ROOT / "instructions.md").read_text(encoding="utf-8"),
        resources=resources,
        projections={"display": Worker.conversation_projection},
    ),
    plugins=(
        BashPlugin, RegistryPlugin, WebSearchPlugin, Memory,
        Plugin(
            name="chat", runtime="starter.chat:chat_runtime",
            optional_requires=(CapabilityRequirement(name="realtime.session", min_version=1),),
        ),
        Plugin(name="scheduler", agent=SchedulerPlugin, runtime=scheduler_runtime),
        Plugin(name="system", agent=SystemPlugin, runtime=system_runtime),
        Plugin(
            name="realtime", agent=RealtimePlugin, runtime=realtime_runtime,
            capabilities=(Capability(name="realtime.session", version=1),),
        ),
        Plugin(name="chromium", agent=browser),
    ),
    surfaces=(ClientSurface(name="main", source=ROOT / "ui",
                            build=("npm", "run", "build"), artifact="dist",
                            routes=("/ui",), shell="playwright"),),
    docs_url=None,
)
