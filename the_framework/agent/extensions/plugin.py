import inspect
from pathlib import Path

from .specialists import AgentTrigger
from .commands import Command
from ..models.config import Config
from .endpoints import Endpoint, endpoint  # noqa: F401 - public re-export
from .hooks import Hook
from .instructions import Instruction
from .providers import Provider
from ..models.state import State
from .tools import NamespaceTool


class AgentPlugin:
    config = Config
    state_schema = State
    name: str | None = None
    description: str | None = None
    # A string retains the historical agentic default. An Instruction lets a
    # plugin own an explicit scope (general, vocal, or agentic).
    instructions: str | Instruction | None = None
    # Relative paths resolve beside the module defining the plugin class.
    instructions_file: str | Path | None = None
    instruction_scope = "agentic"
    provider_channels = ("text",)
    restart_on_config_change = False

    def __init__(self, agent):
        self.agent = agent
        self.loaded = False
        self.activated = False
        self._contributions = []

    def state_projection(self):
        """Return this plugin's client-safe current state projection."""
        return dict(self.plugin_state)

    def persist_state(self, *, emit=True, **updates):
        """Atomically validate and persist mutable state through the agent owner."""
        if self.agent is None:
            raise RuntimeError("persistent plugin state requires a canonical agent")
        return self.agent.update_plugin_state(self.title, updates, emit=emit)

    @property
    def title(self):
        return self.name or self.__class__.__name__

    def instructions_section(self):
        source = self.instructions
        source_file = self.instructions_file
        if source is not None and source_file is not None:
            raise ValueError(
                "plugin instructions and instructions_file are mutually exclusive"
            )
        if source_file is not None:
            path = Path(source_file)
            if not path.is_absolute():
                path = Path(inspect.getfile(type(self))).parent / path
            source = path.read_text(encoding="utf-8").strip()
        if source is None:
            return None
        if isinstance(source, Instruction):
            content = source.content
            scope = source.scope
        elif isinstance(source, str):
            content = source
            scope = self.instruction_scope
        else:
            raise TypeError(
                "plugin instructions must be a string or an Instruction"
            )
        if not content:
            return None
        # The plugin owns the registry identity, while an Instruction supplies
        # the content and explicit scope. This prevents collisions between a
        # plugin's instruction and a separately installed core instruction.
        return Instruction(
            name=self.title,
            content=f"# Plugin: {self.title}\n{content}",
            scope=scope,
        )

    def load(self):
        if self.loaded:
            return self
        if self.agent is not None:
            self.config = self.agent.configs.add(self.title, type(self).config)
            self.plugin_state = self.agent.states.add(
                self.title, type(self).state_schema
            )
        methods = self.methods()
        section = self.instructions_section()
        if section:
            self._contributions.append(("instructions", section))
        tools = [
            method
            for method in methods
            if getattr(method, "agent_tool", False)
        ]
        if tools:
            namespace = NamespaceTool(
                name=self.title,
                description=(
                    self.description
                    or f"Tools exposed by the {self.title} plugin."
                ),
            )
            for method in tools:
                namespace.add(method)
            self._contributions.append(("tools", namespace))
        for method in methods:
            if getattr(method, "agent_trigger", None) is not None:
                trigger = AgentTrigger.from_function(method)
                trigger["name"] = f"{self.title}.{trigger.name}"
                self._contributions.append((
                    "agents",
                    trigger,
                ))
            if getattr(method, "agent_endpoint", None) is not None:
                self._contributions.append((
                    "endpoints",
                    Endpoint.from_function(method),
                ))
            if getattr(method, "agent_command", False):
                command = Command.from_function(method)
                self._contributions.append(("commands", command))
            if getattr(method, "agent_provider", False):
                provider = Provider.from_function(
                    method,
                    channels=list(
                        getattr(method, "agent_provider_channels", None)
                        or self.provider_channels
                    ),
                )
                self._contributions.append(("providers", provider))
            if getattr(method, "agent_hook", False):
                hook = Hook.from_function(method)
                self._contributions.append(("hooks", hook))
        self.loaded = True
        return self

    def activate(self):
        self.load()
        if self.activated:
            return self
        self.activated = True
        try:
            for name in {name for name, _ in self._contributions}:
                self.agent.registry(name)
            self.agent.session.set_plugin(self.title, True)
        except Exception:
            self.activated = False
            raise
        return self

    def deactivate(self):
        if not self.activated:
            return self
        self.activated = False
        try:
            self.agent.session.set_plugin(self.title, False)
        except Exception:
            self.activated = True
            raise
        return self

    def shutdown(self):
        """Release runtime resources owned by this plugin, if any."""
        return None

    def contributions(self, name):
        return [
            contribution
            for kind, contribution in self._contributions
            if kind == name
        ]

    def install(self):
        if self not in self.agent.plugins:
            self.agent.load_plugin(self)
        return self.activate()

    def methods(self):
        return [
            getattr(self, name)
            for name in dir(type(self))
            if inspect.isfunction(getattr(type(self), name, None))
        ]

    def endpoint_declarations(self):
        self.load()
        return self.contributions("endpoints")

    def endpoints(self):
        return self.endpoint_declarations() if self.activated else []
