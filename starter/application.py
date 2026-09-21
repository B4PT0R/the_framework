"""Start here: identity, plugins and persistence for your own application."""

from pathlib import Path

from core import (
    AgentApplication,
    AgentResources,
    AgentSpec,
    ClientSurface,
    PluginSpec,
    SessionPolicy,
)
from core.agent import Worker
from core.utils.persistence import MappingStore
from core.plugins.bash import BashPlugin
from core.plugins.browser import ChromiumPlugin
from core.plugins.memory.plugin import MemoryPlugin
from core.plugins.realtime import RealtimePlugin
from core.plugins.registry import RegistryPlugin
from core.plugins.scheduler import SchedulerPlugin
from core.plugins.system import SystemPlugin
from core.plugins.web_search import WebSearchPlugin

ROOT = Path(__file__).resolve().parent


class Memory(MemoryPlugin):
    curator_instructions = (ROOT / "memory.md").read_text(encoding="utf-8")


def browser(agent):
    return ChromiumPlugin(agent, runtime_root=Path(agent.session.path).parent)


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
        SchedulerPlugin, SystemPlugin, RealtimePlugin,
        PluginSpec(name="chromium", agent=browser),
    ),
    surfaces=(ClientSurface(name="main", source=ROOT / "ui",
                            build=("npm", "run", "build"), artifact="dist",
                            routes=("/ui",), shell="playwright"),),
    docs_url=None,
)
