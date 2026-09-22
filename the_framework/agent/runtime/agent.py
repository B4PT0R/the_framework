from collections.abc import Mapping
from pathlib import Path
from threading import Lock

from codex_backend_sdk import OpenAI

from .agentic_loop import AgenticLoop
from .application import Application
from ..extensions.specialists import AgentTriggers
from ..extensions.commands import Commands
from ..context.compaction import Compaction
from ..models.config import Configs
from ..models.content import ContentItem, InputText, OutputText
from ..context.builder import Context
from ..extensions.endpoints import Endpoints
from .event_loop import EventLoop
from ..models.events import Event
from ..extensions.hooks import Hooks
from ..extensions.instructions import Instruction, Instructions
from ..models.lifecycle import AgentResponseItemAdded
from ..extensions.plugin import AgentPlugin
from .protocol import AgentConfigUpdated, AgentStateUpdated
from ..extensions.providers import Providers
from ..models.responses import Message, ResponseItem
from ..context.session import Session
from ..models.state import States
from ..extensions.tools import Tools


class Agent:
    def __init__(
        self,
        session_path=None,
        *,
        client=None,
        name="agent",
        description="General-purpose agent worker.",
        retain_history=True,
        state=None,
        **kwargs,
    ):
        self._client = client
        self._client_lock = Lock()
        self.application = Application()
        self.agents = AgentTriggers(agent=self)
        self.session = Session.open(session_path) if session_path else Session()
        if not isinstance(name, str) or not name.strip():
            raise ValueError("agent name must not be empty")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("agent description must not be empty")
        self.name = name
        self.description = description
        self.retain_history = bool(retain_history)
        if not self.retain_history and (self.session.history or self.session.archive):
            self.session.clear_context()
        self.configs = Configs(**kwargs)
        self.states = States(state)
        self.commands = Commands()
        self.compaction = Compaction(self)
        self.context = Context(self)
        self.events = EventLoop(self)
        self.endpoints = Endpoints()
        self.hooks = Hooks()
        self.instructions = Instructions()
        if self.configs.instructions:
            self.instructions.add(Instruction(
                name="core",
                content=self.configs.instructions,
            ))
        self.instructions.add(Instruction.from_file(
            Path(__file__).with_name("prompts") / "tool_outputs.md",
            scope="agentic",
        ))
        self.end_of_turn_requested = False
        self.agentic_loop = AgenticLoop(self)
        self.providers = Providers()
        self.plugins = []
        self.required_plugins = set()
        self.tools = Tools()
        self.persist_config = None
        self.persist_state = None

    @property
    def client(self):
        """Authenticate on first backend use, never while assembling the app."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = OpenAI().authenticate(interactive=False)
        return self._client

    @client.setter
    def client(self, value):
        self._client = value

    @property
    def id(self):
        return self.session.id

    @property
    def config(self):
        return self.configs

    @config.setter
    def config(self, updates):
        if not isinstance(updates, Mapping):
            raise ValueError("agent config update must be a mapping")
        self.configs.merge(updates)

    def update_config(self, updates):
        if not isinstance(updates, Mapping):
            raise ValueError("agent config update must be a mapping")
        unknown = set(updates).difference(self.configs)
        if unknown:
            raise ValueError(f"unknown agent config fields: {', '.join(sorted(unknown))}")
        candidate = self.configs.deepcopy()
        candidate.merge(updates)
        if self.persist_config is not None:
            self.persist_config(candidate)
        self.configs.merge(updates)
        self.emit(AgentConfigUpdated(config=dict(self.configs)))
        return self.configs

    def update_plugin_state(self, name, updates, *, emit=True):
        if name not in self.states:
            raise ValueError(f"unknown plugin state: {name}")
        if not isinstance(updates, Mapping):
            raise ValueError("plugin state update must be a mapping")
        current = self.states[name]
        candidate = current.deepcopy()
        candidate.merge(updates)
        snapshot = self.states.snapshot()
        snapshot[name] = dict(candidate)
        if self.persist_state is not None:
            self.persist_state(snapshot)
        current.merge(updates)
        if emit:
            projected = self.state_snapshot()
            self.emit(AgentStateUpdated(state=projected))
        return current

    def state_snapshot(self):
        return {
            plugin.title: plugin.state_projection()
            for plugin in self.plugins
        }

    def config_update_requires_restart(self, updates):
        """Return whether an installed plugin requires restart after an update."""
        if not isinstance(updates, Mapping):
            raise ValueError("agent config update must be a mapping")
        updated_names = set(updates)
        return any(
            plugin.title in updated_names and plugin.restart_on_config_change
            for plugin in self.plugins
        )

    def add_message(
        self,
        role: str,
        text: str | None = None,
        content=None,
        *,
        kind="message",
    ):
        content_cls = OutputText if role == "assistant" else InputText
        items = []
        if text is not None:
            items.append(content_cls(text=text))
        items.extend(ContentItem.from_dict(item) for item in content or [])
        message = Message(role=role, kind=kind, content=items)
        self.add_response_item(message)
        return message

    def add_response_item(self, item: ResponseItem):
        if self.session.append(item) is None:
            return None
        self.emit(AgentResponseItemAdded(item=item))
        return item

    def add_tool(self, tool=None):
        if tool is None:
            def decorator(func):
                self.tools.add(func)
                return func
            return decorator
        self.tools.add(tool)
        return tool

    def add_command(self, command=None):
        if command is None:
            def decorator(func):
                self.commands.add(func)
                return func
            return decorator
        self.commands.add(command)
        return command

    def add_provider(self, provider=None, *, description=None, channels=None):
        if provider is None:
            def decorator(func):
                self.providers.add(
                    func,
                    description=description,
                    channels=channels,
                )
                return func
            return decorator
        self.providers.add(
            provider,
            description=description,
            channels=channels,
        )
        return provider

    def add_hook(self, hook=None):
        if hook is None:
            def decorator(func):
                self.hooks.add(func)
                return func
            return decorator
        self.hooks.add(hook)
        return hook

    def add_instruction(self, instruction=None, *, name=None):
        if isinstance(instruction, str):
            instruction = Instruction(name=name or "core", content=instruction)
        return self.instructions.add(instruction)

    def load_plugin(self, plugin):
        if isinstance(plugin, type):
            plugin = plugin(self)
        if not isinstance(plugin, AgentPlugin):
            raise ValueError("Must be an AgentPlugin class or instance")
        if any(value.title == plugin.title for value in self.plugins):
            raise ValueError(f"duplicate plugin: {plugin.title}")
        plugin.load()
        self.plugins.append(plugin)
        return plugin

    def add_plugin(self, plugin, *, activate=None, required=False):
        plugin = self.load_plugin(plugin)
        if required:
            self.required_plugins.add(plugin.title)
        activate = (
            True if plugin.title in self.required_plugins
            else self.session.plugins.get(
                plugin.title, True if activate is None else activate
            )
        )
        if not activate:
            return plugin
        try:
            return plugin.activate()
        except Exception:
            self.plugins.remove(plugin)
            if required:
                self.required_plugins.discard(plugin.title)
            raise

    def registry(self, name):
        core = getattr(self, name)
        registry = (
            core.clone_empty()
            if hasattr(core, "clone_empty")
            else type(core)()
        )
        for contribution in core.values():
            registry.add(contribution)
        for plugin in self.plugins:
            if plugin.activated:
                for contribution in plugin.contributions(name):
                    registry.add(contribution)
        return registry

    def plugin(self, plugin):
        if isinstance(plugin, AgentPlugin):
            return plugin
        return next(
            (value for value in self.plugins if value.title == plugin),
            None,
        )

    def activate_plugin(self, plugin):
        plugin = self.plugin(plugin)
        if plugin is None:
            raise ValueError("unknown plugin")
        return plugin.activate()

    def deactivate_plugin(self, plugin):
        plugin = self.plugin(plugin)
        if plugin is None:
            raise ValueError("unknown plugin")
        if plugin.title in self.required_plugins:
            raise RuntimeError(f"plugin binding is required: {plugin.title}")
        return plugin.deactivate()

    def on(self, event_type: str, callback=None):
        if callback is None:
            def decorator(func):
                self.events.subscribe(event_type, func)
                return func
            return decorator
        self.events.subscribe(event_type, callback)
        return callback

    def emit(self, event: Event):
        return self.events.emit(event)

    def stream(self, prompt=None):
        yield from self.events.stream(self.agentic_loop.turn_sync, prompt)

    async def astream(self, prompt=None, **kwargs):
        async for event in self.events.astream(self.agentic_loop.turn, prompt, **kwargs):
            yield event

    def interrupt(self, reason=None):
        return self.agentic_loop.interrupt(reason)

    def end_turn(self):
        """Request a graceful stop after the current tool-call batch completes."""
        self.end_of_turn_requested = True

    def compact(self, input=None):
        return self.compaction.run(input=input)

    def chat(self, **kwargs):
        from .chat import chat
        return chat(self, **kwargs)
