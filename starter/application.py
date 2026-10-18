"""Start here: identity, plugins and persistence for your own application."""

from pathlib import Path

from the_framework import (
    Application,
    AgentSpec,
    Plugin,
)
from the_framework.plugins.bash import BASH
from the_framework.plugins.artifacts import ARTIFACTS
from the_framework.plugins.browser import CHROMIUM
from the_framework.plugins.memory import MEMORY, MemoryExtension
from the_framework.plugins.realtime import REALTIME
from the_framework.plugins.registry import REGISTRY
from the_framework.plugins.scheduler import SCHEDULER
from the_framework.plugins.system import SYSTEM
from the_framework.plugins.web_search import WEB_SEARCH

ROOT = Path(__file__).resolve().parent


class Memory(MemoryExtension):
    curator_instructions = (ROOT / "memory.md").read_text(encoding="utf-8")


application = Application(
    name="Pandora",
    version="0.1.0",
    primary_agent=AgentSpec(
        name="assistant",
        description="Pandora, a persistent local agent that can evolve her own application.",
        session_mode='durable',
        instructions=(ROOT / "instructions.md").read_text(encoding="utf-8"),
        configuration={"model": "gpt-5.6-luna", "bash": {"default_cwd": str(ROOT)}},
    ),
    plugins=(
        BASH, REGISTRY, WEB_SEARCH, Plugin({**MEMORY, "agent_extension": Memory}),
        ARTIFACTS,
        SCHEDULER, SYSTEM, REALTIME, CHROMIUM,
    ),
    docs_url=None,
)
