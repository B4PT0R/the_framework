from harness_core.agent.spec import AgentSpec, QueuePolicy
from harness_core.agent.extensions.instructions import Instruction
import asyncio
import json
from pathlib import Path

import pytest
from modict import modict

from harness_core.agent.runtime.agent import Agent
from harness_core.agent.extensions.specialists import AgentTriggers, agent_trigger
from harness_core.agent.runtime.agentic_loop import AgenticLoop
from harness_core.agent.extensions.commands import Commands, command
from harness_core.agent.models.config import Config, Configs
from harness_core.agent.context.builder import Context
from harness_core.agent.extensions.endpoints import Endpoints
from harness_core.agent.runtime.event_loop import EventLoop
from harness_core.agent.extensions.hooks import Hooks, hook
from harness_core.agent.extensions.instructions import Instructions
from harness_core.agent.extensions.plugin import Plugin, endpoint
from harness_core.agent.extensions.providers import Providers, provider
from harness_core.agent.models.responses import CommandOutput, FunctionCall, Image, ProviderOutput, ToolOutput
from harness_core.agent.context.session import Session
from harness_core.agent.models.state import States
from harness_core.agent.extensions.tools import NamespaceTool, ToolSearchTool, Tools, tool


class FakeAgent:
    def __init__(self):
        self.tools = Tools()
        self.agents = AgentTriggers()
        self.configs = Configs()
        self.states = States()
        self.commands = Commands()
        self.providers = Providers()
        self.hooks = Hooks()
        self.endpoints = Endpoints()
        self.instructions = Instructions()
        self.instructions.add(Instruction(
            name="core",
            content="Core instructions.",
        ))
        self.session = Session()
        self.plugins = []

    def add_tool(self, value):
        return self.tools.add(value)

    def add_response_item(self, value):
        self.session.append(value)
        return value

    def emit(self, event):
        return event

    def registry(self, name):
        return Agent.registry(self, name)

    def load_plugin(self, plugin):
        return Agent.load_plugin(self, plugin)

    def add_plugin(self, plugin, *, activate=None):
        return Agent.add_plugin(self, plugin, activate=activate)


class Lights(Plugin):
    name = "lights"
    description = "Lighting controls."

    @tool
    def status(self):
        """Return the light status."""
        return "on"

    @tool
    def dim(self, intensity: int):
        """Set the light intensity."""
        return intensity


class Haptics(Plugin):
    name = "haptics"

    @tool
    def status(self):
        """Return the haptic status."""
        return "idle"


class AsyncLights(Plugin):
    name = "async_lights"

    @tool
    async def dim(self, intensity: int):
        """Set the light intensity asynchronously."""
        await asyncio.sleep(0)
        return intensity


class FirstInstructions(Plugin):
    name = "first"
    instructions = "First plugin instructions."


class SecondInstructions(Plugin):
    name = "second"
    instructions = "Second plugin instructions."


class VocalInstructions(Plugin):
    name = "vocal_plugin"
    instructions = Instruction(
        name="plugin_prompt",
        content="Vocal plugin instructions.",
        scope="vocal",
    )


class FileInstructions(Plugin):
    name = "file_instruction"
    instruction_scope = "general"


class LimitsConfig(Config):
    maximum: int = 100
    ramp: int = 5


class DeviceConfig(Config):
    enabled: bool = True
    limits: LimitsConfig = modict.factory(LimitsConfig)


class Configured(Plugin):
    name = "configured"
    config = DeviceConfig


class Complete(Plugin):
    name = "complete"
    description = "A complete plugin."
    instructions = "Use the complete plugin carefully."

    @tool
    def ping(self):
        """Return pong."""
        return "pong"

    @command
    def reset_complete(self):
        """Reset the complete plugin."""

    @provider
    def complete_context(self):
        """Return complete context."""
        return "context"

    @hook
    def before_complete(self, payload):
        return payload

    @endpoint("get", "/complete")
    def complete_endpoint(self):
        return {"ok": True}


class MemoryCurator(Plugin):
    name = "memory"

    def __init__(self, agent):
        super().__init__(agent)
        self.results = []

    @agent_trigger(
        AgentSpec(
            name="jiminy",
            instructions="Curate durable relational memory.",
            configuration={"model": "gpt-5.6-luna"},
            queue=QueuePolicy(mode="latest", limit=4),
        ),
        observes=["agent.response_item.added"],
    )
    def jiminy(self, result):
        """Curate and recall relevant relational memories."""
        self.results.append(result)


def test_plugin_tools_are_installed_as_one_namespace():
    agent = FakeAgent()
    agent.add_plugin(Lights)

    namespace = agent.registry("tools")["lights"]
    assert isinstance(namespace, NamespaceTool)
    assert namespace.description == "Lighting controls."
    assert [value.name for value in namespace.tools] == ["dim", "status"]
    assert agent.registry("tools").resolve("status", "lights")() == "on"


def test_plugin_contributes_typed_agent_profiles_only_when_active():
    agent = FakeAgent()
    plugin = agent.add_plugin(MemoryCurator, activate=False)

    assert agent.registry("agents").profiles() == []

    plugin.activate()
    profiles = agent.registry("agents").profiles()

    assert len(profiles) == 1
    assert profiles[0] == {
        "name": "memory.jiminy",
        "description": "Curate and recall relevant relational memories.",
        "model": "gpt-5.6-luna",
        "instructions": "Curate durable relational memory.",
        "observes": ["agent.response_item.added"],
        "item_kinds": [],
        "queue_limit": 4,
        "queue_policy": "latest",
        "session_mode": "ephemeral",
        "plugins": [],
        "agent_config": {},
        "plugin_config": {},
        "conversation_roles": [],
        "backlog_limit": 512,
        "input_item_field": None,
        "durable_tasks": True,
        "idle_timeout_seconds": None,
        "completion_tool": None,
        "completion_retry_limit": 0,
    }

    result = {
        "profile": "memory.jiminy",
        "status": "completed",
        "output": "remember",
    }
    agent.registry("agents").dispatch(result)
    assert plugin.results == [result]


def test_instruction_scopes_filter_agentic_and_vocal_contexts():
    instructions = Instructions()
    instructions.add(Instruction(name="shared", content="Shared", scope="general"))
    instructions.add(Instruction(name="voice", content="Voice", scope="vocal"))
    instructions.add(Instruction(name="tools", content="Tools", scope="agentic"))

    assert instructions.render(scope="vocal") == "Shared\n\nVoice"
    assert instructions.render(scope="agentic") == "Shared\n\nTools"

    with pytest.raises(ValueError, match="instruction scope"):
        Instruction(name="bad", content="Bad", scope="renderer")


def test_expired_instruction_is_removed_from_every_rendered_scope(monkeypatch):
    instructions = Instructions()
    instructions.add(Instruction(name="temporary", content="Arrival", expires_at=100))
    instructions.add(Instruction(name="lasting", content="Always"))

    monkeypatch.setattr("harness_core.agent.extensions.instructions.time.time", lambda: 99)
    assert instructions.render(scope="agentic") == "Arrival\n\nAlways"
    monkeypatch.setattr("harness_core.agent.extensions.instructions.time.time", lambda: 100)
    assert instructions.render(scope="agentic") == "Always"


def test_plugin_namespace_description_has_a_compatible_default():
    agent = FakeAgent()
    agent.add_plugin(Haptics)

    assert agent.registry("tools")["haptics"].description == (
        "Tools exposed by the haptics plugin."
    )


def test_unnamespaced_resolution_is_allowed_only_when_unambiguous():
    agent = FakeAgent()
    agent.add_plugin(Lights)
    assert agent.registry("tools").resolve("dim")(3) == 3

    agent.add_plugin(Haptics)
    with pytest.raises(ValueError, match="ambiguous"):
        agent.registry("tools").resolve("status")


def test_tool_search_preserves_namespace_boundaries():
    agent = FakeAgent()
    agent.add_plugin(Lights)
    agent.tools.add(ToolSearchTool())

    results = agent.registry("tools").search({"query": "intensity"})
    assert len(results) == 1
    assert isinstance(results[0], NamespaceTool)
    assert results[0].name == "lights"
    assert [value.name for value in results[0].tools] == ["dim"]
    assert all(
        not isinstance(value, ToolSearchTool)
        for value in agent.registry("tools").search()
    )


def test_agentic_loop_executes_the_namespaced_function_call():
    agent = FakeAgent()
    agent.add_plugin(Lights)

    output = asyncio.run(AgenticLoop(agent).tool_output(FunctionCall(
        name="dim",
        namespace="lights",
        arguments='{"intensity": 4, "user_feedback": "Je tamise les lumières du salon"}',
        call_id="call-1",
    )))

    assert output.call_id == "call-1"
    assert json.loads(output.output) == {
        "status": "success",
        "message_ids": [output.output_items[0].id],
    }
    message = output.output_items[0]
    assert isinstance(message, ToolOutput)
    assert message.type == "message"
    assert message.role == "developer"
    assert message.kind == "tool_output"
    assert message.call_id == "call-1"
    assert message.content[0].text == (
        f'<tool_output name="dim" id="{message.id}" call_id="call-1">\n'
        "4\n"
        "</tool_output>"
    )


def test_tool_feedback_precedes_its_referenced_context_message():
    agent = FakeAgent()
    agent.add_plugin(Lights)
    call = FunctionCall(
        name="status",
        namespace="lights",
        arguments='{"user_feedback": "Je vérifie l’état des lumières"}',
        call_id="call-2",
    )

    asyncio.run(AgenticLoop(agent).run_tool_call(call))

    feedback, message = agent.session.history
    assert json.loads(feedback.output)["message_ids"] == [message.id]
    assert isinstance(message, ToolOutput)


def test_agentic_loop_awaits_async_function_tools():
    agent = FakeAgent()
    agent.add_plugin(AsyncLights)

    output = asyncio.run(AgenticLoop(agent).tool_output(FunctionCall(
        name="dim",
        namespace="async_lights",
        arguments='{"intensity": 7, "user_feedback": "J’ajuste doucement la lumière"}',
        call_id="call-async",
    )))

    assert json.loads(output.output)["status"] == "success"
    assert "\n7\n" in output.output_items[0].content[0].text


def test_tool_output_contract_preserves_typed_items_and_binds_call_id(tmp_path):
    agent = FakeAgent()
    image_path = tmp_path / "tool.png"
    image_path.write_bytes(b"image")

    @agent.add_tool
    def mixed():
        """Return every supported local output kind."""
        return (
            "plain",
            {"value": 42},
            ToolOutput(output=["explicit", "json"]),
            Image(path=str(image_path), description="Tool screenshot"),
        )

    output = asyncio.run(AgenticLoop(agent).tool_output(FunctionCall(
        name="mixed",
        arguments='{"user_feedback": "Je vérifie les sorties structurées"}',
        call_id="call-mixed",
    )))

    assert [type(item) for item in output.output_items] == [
        ToolOutput,
        ToolOutput,
        ToolOutput,
        Image,
    ]
    assert all(item.call_id == "call-mixed" for item in output.output_items)
    assert "\nplain\n" in output.output_items[0].content[0].text
    assert '\n{&quot;value&quot;:42}\n' in output.output_items[1].content[0].text
    assert '\n[&quot;explicit&quot;,&quot;json&quot;]\n' in output.output_items[2].content[0].text


def test_tool_list_is_one_json_output_while_tuple_is_multiple():
    agent = FakeAgent()

    @agent.add_tool
    def values():
        """Return one JSON list."""
        return ["first", "second"]

    output = asyncio.run(AgenticLoop(agent).tool_output(FunctionCall(
        name="values",
        arguments='{"user_feedback": "Je lis la liste structurée"}',
        call_id="call-list",
    )))

    assert len(output.output_items) == 1
    assert '\n[&quot;first&quot;,&quot;second&quot;]\n' in output.output_items[0].content[0].text


def test_provider_output_contract_is_ephemeral_typed_and_tuple_aware(tmp_path):
    providers = Providers()
    image_path = tmp_path / "provider.png"
    image_path.write_bytes(b"image")

    @providers.add(description="Current browser state.")
    def browser_state():
        return (
            "ready",
            ["one", "json", "list"],
            ProviderOutput(output={"explicit": True}),
            Image(path=str(image_path), description="Browser screenshot"),
        )

    outputs = providers.outputs(channel="text")

    assert [type(item) for item in outputs] == [
        ProviderOutput,
        ProviderOutput,
        ProviderOutput,
        Image,
    ]
    assert outputs[0].name == "browser_state"
    assert outputs[0].description == "Current browser state."
    assert "\nready\n" in outputs[0].content[0].text
    assert '\n["one","json","list"]\n' in outputs[1].content[0].text
    assert '\n{"explicit":true}\n' in outputs[2].content[0].text


def test_async_provider_output_contract_uses_the_async_projection_path():
    providers = Providers()

    @providers.add(description="Fresh asynchronous state.")
    async def live_state():
        await asyncio.sleep(0)
        return {"status": "ready"}

    outputs = asyncio.run(providers.aoutputs(channel="text"))

    assert len(outputs) == 1
    assert isinstance(outputs[0], ProviderOutput)
    assert outputs[0].name == "live_state"
    assert '"status":"ready"' in outputs[0].content[0].text


def test_provider_images_bypass_session_image_limit_without_polluting_history(tmp_path):
    agent = FakeAgent()
    agent.configs.max_input_images = 0
    historical_path = tmp_path / "historical.png"
    historical_path.write_bytes(b"historical")
    image_path = tmp_path / "provider-context.png"
    image_path.write_bytes(b"image")
    historical = Image(path=str(historical_path), description="Historical session image")
    agent.session.append(historical)

    @agent.providers.add(channels=["text"])
    def screenshot():
        return Image(path=str(image_path), description="Current browser viewport")

    projected = Context(agent).input()

    assert agent.session.history == [historical]
    assert len(projected) == 1
    assert projected[0]["role"] == "user"
    assert projected[0]["content"][0]["type"] == "input_image"
    assert projected[0]["content"][0]["image_url"].startswith(
        "data:image/png;base64,"
    )
    assert str(historical_path) not in projected[0]["content"][1]["text"]
    assert str(image_path) in projected[0]["content"][1]["text"]
    assert "only because of API image-input constraints" in (
        projected[0]["content"][1]["text"]
    )
    assert "actual source may vary" in projected[0]["content"][1]["text"]


def test_command_output_contract_binds_one_command_id_to_all_outputs(tmp_path):
    agent = FakeAgent()
    image_path = tmp_path / "command.png"
    image_path.write_bytes(b"image")

    @agent.commands.add
    def inspect():
        """Return every supported command output kind."""
        return (
            "plain",
            {"value": 42},
            CommandOutput(output=["explicit", "json"]),
            Image(path=str(image_path), description="Command screenshot"),
        )

    outputs = AgenticLoop(agent).command_outputs("/inspect")

    assert [type(item) for item in outputs] == [
        CommandOutput,
        CommandOutput,
        CommandOutput,
        Image,
    ]
    command_id = outputs[0].command_id
    assert command_id
    assert all(item.command_id == command_id for item in outputs)
    assert f'command_id="{command_id}"' in outputs[0].content[0].text
    assert "\nplain\n" in outputs[0].content[0].text
    assert '\n{&quot;value&quot;:42}\n' in outputs[1].content[0].text
    assert '\n[&quot;explicit&quot;,&quot;json&quot;]\n' in outputs[2].content[0].text


def test_command_list_is_one_json_output_and_is_committed_by_turn():
    agent = FakeAgent()

    @agent.commands.add
    def values():
        """Return one structured list."""
        return ["first", "second"]

    asyncio.run(AgenticLoop(agent).turn("/values"))

    assert len(agent.session.history) == 1
    output = agent.session.history[0]
    assert isinstance(output, CommandOutput)
    assert output.command_id
    assert '\n[&quot;first&quot;,&quot;second&quot;]\n' in output.content[0].text


def test_every_tool_output_is_truncated_with_the_global_token_budget():
    agent = FakeAgent()
    agent.configs.max_tool_output_tokens = 2

    @agent.add_tool
    def verbose():
        """Return several output lines."""
        return "alpha\nbeta\ngamma\n"

    output = asyncio.run(AgenticLoop(agent).tool_output(FunctionCall(
        name="verbose",
        arguments='{"user_feedback": "Je récupère le résultat détaillé"}',
        call_id="call-verbose",
    )))

    text = output.output_items[0].content[0].text
    assert "alpha" in text
    assert "gamma" in text
    assert "Middle truncated" in text
    assert "beta" not in text


def test_tool_output_token_budget_is_local_config_not_responses_payload():
    agent = FakeAgent()
    agent.context = Context(agent)
    agent.configs.max_tool_output_tokens = 321

    assert "max_tool_output_tokens" not in AgenticLoop(agent).generation_payload()
    with pytest.raises(ValueError, match="max_tool_output_tokens"):
        Configs(max_tool_output_tokens=0)


def test_turn_reasoning_override_replaces_only_the_generated_payload():
    agent = FakeAgent()
    agent.context = Context(agent)
    agent.configs.reasoning = {"effort": "high"}
    loop = AgenticLoop(agent)

    assert loop.generation_payload()["reasoning"]["effort"] == "high"
    loop.reasoning_effort_override = "none"
    assert loop.generation_payload()["reasoning"] == {"effort": "none"}
    assert agent.configs.reasoning.effort == "high"


def test_failed_tool_call_returns_feedback_and_a_detailed_message():
    agent = FakeAgent()
    call = FunctionCall(
        name="missing",
        arguments='{"user_feedback": "Je vérifie cet outil"}',
        call_id="call-3",
    )

    output = asyncio.run(AgenticLoop(agent).tool_output(call))

    assert json.loads(output.output) == {
        "status": "failure",
        "message_ids": [output.output_items[0].id],
        "error": "unknown tool: missing",
    }
    assert "unknown tool: missing" in output.output_items[0].content[0].text


def test_missing_user_feedback_rejects_the_call_without_invoking_the_tool():
    agent = FakeAgent()
    invoked = []

    @agent.add_tool
    def guarded():
        """Record an invocation."""
        invoked.append(True)
        return "called"

    output = asyncio.run(AgenticLoop(agent).tool_output(FunctionCall(
        name="guarded",
        arguments="{}",
        call_id="call-no-feedback",
    )))
    feedback = json.loads(output.output)

    assert feedback["status"] == "failure"
    assert "missing required user_feedback" in feedback["error"]
    assert "missing required user_feedback" in output.output_items[0].content[0].text
    assert invoked == []


def test_user_feedback_is_removed_before_invocation_and_added_to_tool_events():
    agent = FakeAgent()
    events = []
    received = []
    agent.emit = lambda event: events.append(event) or event

    @agent.add_tool
    def observe(value: int):
        """Observe only business arguments."""
        received.append(value)
        return value

    call = FunctionCall(
        name="observe",
        arguments='{"value": 3, "user_feedback": "Je vérifie la valeur calculée"}',
        call_id="call-events",
    )
    asyncio.run(AgenticLoop(agent).run_tool_call(call))

    assert received == [3]
    start = next(event for event in events if event.type == "agent.tool_call.start")
    end = next(event for event in events if event.type == "agent.tool_call.end")
    assert start.user_feedback == "Je vérifie la valeur calculée"
    assert end.user_feedback == "Je vérifie la valeur calculée"


def test_namespace_and_nested_tool_names_must_be_unique():
    agent = FakeAgent()
    agent.tools.add(NamespaceTool(name="lights", description="Core lights"))
    with pytest.raises(ValueError, match="duplicate tool or namespace"):
        agent.add_plugin(Lights)

    namespace = NamespaceTool(name="example", description="Example tools")
    namespace.add(Lights(agent).status)
    with pytest.raises(ValueError, match="duplicate tool in namespace"):
        namespace.add(Haptics(agent).status)


def test_loaded_plugin_is_inert_until_activated_and_deactivation_is_complete():
    agent = FakeAgent()
    plugin = agent.load_plugin(Complete)

    assert plugin.loaded is True
    assert plugin.activated is False
    assert not agent.tools
    assert not agent.commands
    assert not agent.providers
    assert not agent.hooks
    assert not agent.endpoints
    assert list(agent.registry("instructions")) == ["core"]
    assert plugin.endpoints() == []
    assert len(plugin.endpoint_declarations()) == 1
    assert Context(agent).instructions() == "Core instructions."

    plugin.activate()

    assert plugin.activated is True
    assert "complete" in agent.registry("tools")
    assert "reset_complete" in agent.registry("commands")
    assert "complete_context" in agent.registry("providers")
    assert "before_complete" in agent.registry("hooks")
    assert "GET /complete" in agent.registry("endpoints")
    assert list(agent.registry("instructions")) == ["core", "complete"]
    assert len(plugin.endpoints()) == 1
    assert "Use the complete plugin carefully." in Context(agent).instructions()
    assert agent.session.plugins == {"complete": True}

    plugin.deactivate()

    assert plugin.loaded is True
    assert plugin.activated is False
    assert not agent.registry("tools")
    assert not agent.registry("commands")
    assert not agent.registry("providers")
    assert not agent.registry("hooks")
    assert not agent.registry("endpoints")
    assert list(agent.registry("instructions")) == ["core"]
    assert plugin.endpoints() == []
    assert Context(agent).instructions() == "Core instructions."
    assert agent.session.plugins == {"complete": False}


def test_add_plugin_honors_the_persisted_session_preference():
    agent = Agent.__new__(Agent)
    agent.tools = Tools()
    agent.commands = Commands()
    agent.providers = Providers()
    agent.hooks = Hooks()
    agent.endpoints = Endpoints()
    agent.instructions = Instructions()
    agent.configs = Configs()
    agent.states = States()
    agent.session = Session(plugins={"lights": False})
    agent.plugins = []

    plugin = agent.add_plugin(Lights)

    assert plugin.loaded is True
    assert plugin.activated is False
    assert not agent.tools


def test_inactive_plugin_config_remains_registered_and_live():
    agent = Agent.__new__(Agent)
    agent.events = EventLoop(agent)
    agent.tools = Tools()
    agent.commands = Commands()
    agent.providers = Providers()
    agent.hooks = Hooks()
    agent.endpoints = Endpoints()
    agent.instructions = Instructions()
    agent.configs = Configs(configured={"limits": {"maximum": 60}})
    agent.states = States()
    agent.persist_config = None
    agent.session = Session(plugins={"configured": False})
    agent.plugins = []

    plugin = agent.add_plugin(Configured)
    namespace = plugin.config

    assert plugin.activated is False
    assert agent.configs.configured is namespace
    assert dict(agent.configs)["configured"] == {
        "enabled": True,
        "limits": {"maximum": 60, "ramp": 5},
    }

    agent.update_config({"configured": {"limits": {"ramp": 18}}})
    assert plugin.config is namespace
    assert plugin.config.limits == {"maximum": 60, "ramp": 18}

    plugin.activate()
    assert plugin.config is namespace
    assert plugin.config.limits.ramp == 18


def test_legacy_install_registers_the_loaded_runtime():
    agent = FakeAgent()
    plugin = Lights(agent).install()

    assert plugin in agent.plugins
    assert "lights" in agent.registry("tools")


def test_instruction_registry_is_named_ordered_and_rejects_collisions():
    instructions = Instructions()
    instructions.add(Instruction(name="core", content="Core."))
    instructions.add(Instruction(name="memory", content="Remember."))

    assert instructions.render() == "Core.\n\nRemember."
    with pytest.raises(ValueError, match="duplicate instruction"):
        instructions.add(Instruction(name="core", content="Other."))


def test_effective_instructions_keep_core_then_plugin_load_order():
    agent = FakeAgent()
    agent.instructions.add(Instruction(name="second_core", content="Core two."))
    first = agent.add_plugin(FirstInstructions)
    second = agent.add_plugin(SecondInstructions)

    expected = [
        "Core instructions.",
        "Core two.",
        "# Plugin: first\nFirst plugin instructions.",
        "# Plugin: second\nSecond plugin instructions.",
    ]
    assert agent.registry("instructions").render() == "\n\n".join(expected)

    first.deactivate()
    first.activate()
    assert agent.registry("instructions").render() == "\n\n".join(expected)
    assert second.activated is True


def test_plugin_instruction_object_preserves_its_explicit_scope():
    agent = FakeAgent()
    agent.add_plugin(VocalInstructions)

    installed = agent.registry("instructions")["vocal_plugin"]
    assert installed.scope == "vocal"
    assert installed.content == "# Plugin: vocal_plugin\nVocal plugin instructions."
    assert "Vocal plugin instructions." not in agent.registry(
        "instructions"
    ).render(scope="agentic")
    assert "Vocal plugin instructions." in agent.registry(
        "instructions"
    ).render(scope="vocal")


def test_plugin_instruction_can_load_an_editable_markdown_file(tmp_path):
    prompt = tmp_path / "instructions.md"
    prompt.write_text("# Editable\n\nInstructions from Markdown.\n", encoding="utf-8")
    FileInstructions.instructions_file = prompt
    try:
        section = FileInstructions(None).instructions_section()
    finally:
        FileInstructions.instructions_file = None

    assert section.name == "file_instruction"
    assert section.scope == "general"
    assert section.content == (
        "# Plugin: file_instruction\n# Editable\n\nInstructions from Markdown."
    )


def test_plugin_rejects_ambiguous_inline_and_file_instructions(tmp_path):
    prompt = tmp_path / "instructions.md"
    prompt.write_text("File instructions.", encoding="utf-8")

    class AmbiguousInstructions(Plugin):
        instructions = "Inline instructions."
        instructions_file = prompt

    with pytest.raises(ValueError, match="mutually exclusive"):
        AmbiguousInstructions(None).instructions_section()


def test_builtin_tool_output_prompt_explains_message_references():
    instruction = Instruction.from_file(
        Path(__file__).parents[2] / "harness_core/agent/runtime/prompts/tool_outputs.md"
    )

    assert instruction.name == "tool_outputs"
    assert "message_ids" in instruction.content
    assert '<tool_output name="tool_name"' in instruction.content
    assert "call_id" in instruction.content


def test_file_instruction_accepts_a_custom_name():
    instruction = Instruction.from_file(
        Path(__file__).parents[2] / "harness_core/agent/runtime/prompts/tool_outputs.md",
        name="tool_results_protocol",
    )

    assert instruction.name == "tool_results_protocol"


def test_plugin_config_merges_defaults_with_namespaced_overrides():
    agent = FakeAgent()
    agent.configs["configured"] = {
        "limits": {"maximum": 60},
    }

    plugin = agent.load_plugin(Configured)

    assert Configured.config is DeviceConfig
    assert isinstance(plugin.config, DeviceConfig)
    assert plugin.config == {
        "enabled": True,
        "limits": {"maximum": 60, "ramp": 5},
    }
    assert "configured" not in agent.configs.root()


def test_configs_support_tree_diff_and_merge_for_plugin_namespaces():
    configs = Configs(configured={"enabled": True})
    configs.add("configured", DeviceConfig)
    updated = configs.deepcopy()
    updated.merge({"configured": {"limits": {"ramp": 12}}})

    patch = configs.diffed(updated)
    configs.merge(patch)

    assert configs.deep_equals(updated)
    assert configs.configured.limits.ramp == 12


def test_plugin_config_recasts_nested_overrides_to_declared_types():
    configs = Configs(configured={
        "limits": {"maximum": 45},
    })

    config = configs.add("configured", DeviceConfig)

    assert isinstance(config, DeviceConfig)
    assert isinstance(config.limits, LimitsConfig)
    assert config.limits == {"maximum": 45, "ramp": 5}


def test_plugin_config_defaults_are_isolated_between_registries():
    first = Configs()
    second = Configs()
    first.add("configured", DeviceConfig)
    second.add("configured", DeviceConfig)

    first.configured.limits.ramp = 20

    assert second.configured.limits.ramp == 5


def test_configs_reject_invalid_plugin_namespaces_and_schemas():
    with pytest.raises(ValueError, match="conflicts with root"):
        Configs().add("model", DeviceConfig)
    with pytest.raises(TypeError, match="must be a mapping"):
        Configs(configured=True).add("configured", DeviceConfig)
    with pytest.raises(TypeError, match="Config subclass"):
        Configs().add("configured", dict)


def test_root_config_contains_no_plugin_namespace():
    configs = Configs(
        model="gpt-test",
        reasoning={"effort": "high"},
        configured={"enabled": False},
    )
    configs.add("configured", DeviceConfig)

    root = configs.root()

    assert root.model == "gpt-test"
    assert root.reasoning.effort == "high"
    assert "configured" not in root


def test_global_configs_merge_root_and_live_plugin_namespace():
    agent = Agent.__new__(Agent)
    agent.configs = Configs(configured={"limits": {"maximum": 60}})
    agent.states = States()
    agent.plugins = []
    plugin = agent.load_plugin(Configured)
    namespace = plugin.config

    agent.configs.merge({
        "model": "gpt-updated",
        "configured": {
            "enabled": False,
            "limits": {"ramp": 18},
        },
    })

    assert plugin.config is namespace
    assert agent.configs.configured is namespace
    assert agent.configs.model == "gpt-updated"
    assert plugin.config.enabled is False
    assert plugin.config.limits == {"maximum": 60, "ramp": 18}

    agent.configs.configured.limits.ramp = 24
    assert plugin.config.limits.ramp == 24


def test_agent_config_property_merges_without_replacing_global_registry():
    agent = Agent.__new__(Agent)
    agent.configs = Configs(configured={"limits": {"maximum": 60}})
    agent.states = States()
    agent.plugins = []
    plugin = agent.load_plugin(Configured)
    registry = agent.configs
    namespace = plugin.config

    agent.config = Config(
        model="gpt-property-update",
        configured={"limits": {"ramp": 21}},
    )

    assert agent.config is registry
    assert agent.configs is registry
    assert agent.config.configured is namespace
    assert plugin.config is namespace
    assert agent.config.model == "gpt-property-update"
    assert plugin.config.limits == {"maximum": 60, "ramp": 21}


def test_agent_config_property_rejects_replacement_with_non_mapping():
    agent = Agent.__new__(Agent)
    agent.configs = Configs()

    with pytest.raises(ValueError, match="must be a mapping"):
        agent.config = True
