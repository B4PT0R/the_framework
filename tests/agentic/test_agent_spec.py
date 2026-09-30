import asyncio
import pytest
import json

from the_framework.agent import AgentResources, AgentSpec, Instruction, AgentExtension, QueuePolicy
from the_framework.agent.runtime.protocol import PluginBindingRequest, PromptRequest
from the_framework.agent.context.session import Session
from the_framework.server.composition.application import Plugin


class ExampleExtension(AgentExtension):
    name = "example"


def test_backend_client_is_lazy_noninteractive_and_retryable(monkeypatch):
    from the_framework.agent.runtime import agent as runtime

    calls = []
    available = False
    client = object()

    class Backend:
        def authenticate(self, *, interactive):
            calls.append(interactive)
            if not available:
                raise RuntimeError("login required")
            return client

    monkeypatch.setattr(runtime, "OpenAI", Backend)
    agent = AgentSpec(name="offline").build_agent()
    assert calls == []
    with pytest.raises(RuntimeError, match="login required"):
        _ = agent.client
    available = True
    assert agent.client is client
    assert agent.client is client
    assert calls == [False, False]


def test_specialist_spec_roundtrip_preserves_execution_configuration():
    spec = AgentSpec(
        name="observer", configuration={"model": "model", "reasoning": {"effort": "low"},
                                         "example": {"enabled": True}},
        extensions=("the_framework.plugins.registry:RegistryExtension",),
        idle_timeout_seconds=30, completion_tool="done", completion_retry_limit=2,
    )
    restored = AgentSpec.from_profile(json.loads(json.dumps(spec.fleet_profile())))
    assert restored.configuration == spec.configuration
    assert restored.extensions == spec.extensions
    assert restored.completion_tool == "done"
    assert restored.completion_retry_limit == 2
    assert restored.idle_timeout_seconds == 30


def test_plain_role_instructions_use_the_same_instruction_model():
    spec = AgentSpec(name="observer", instructions="Describe the evidence.")
    assert len(spec.instructions) == 1
    assert isinstance(spec.instructions[0], Instruction)
    assert spec.fleet_profile().instructions == "Describe the evidence."


def test_trigger_keeps_selection_separate_from_shared_agent_spec():
    from the_framework.agent import AgentTrigger, agent_trigger

    spec = AgentSpec(name="observer", configuration={"model": "custom"})

    @agent_trigger(spec, observes=("event",), input_item_field="items")
    def receive_result(result):
        return result

    trigger = AgentTrigger.from_function(receive_result)
    assert trigger.spec is spec
    assert trigger.name == "observer"
    profile = trigger.profile()
    assert profile.model == "custom"
    assert profile.observes == ["event"]
    assert profile.input_item_field == "items"
    assert trigger.handle("result") == "result"
    assert "observes" not in spec


def test_agent_resources_factory_is_resolved_once_at_construction(tmp_path):
    calls = []
    client = object()
    saves = []
    resources = AgentResources(
        client=client, configuration={"model": "resource-model"},
        persist_config=saves.append,
    )

    def load(path):
        calls.append(path)
        return resources

    spec = AgentSpec(name="example", resources=load)
    assert calls == []
    path = tmp_path / "session.json"
    agent = spec.build_agent(path)
    assert calls == [path]
    assert agent.client is client
    assert agent.configs.model == "resource-model"
    agent.update_config({"model": "updated"})
    assert saves[0]["model"] == "updated"


def test_explicit_resources_replace_factory_without_loading_it():
    def unexpected(_path):
        raise AssertionError("explicit resources must bypass the factory")

    client = object()
    resources = AgentResources(client=client)
    agent = AgentSpec(name="example", resources=unexpected).build_agent(resources=resources)
    assert agent.client is client
    assert agent.persist_config is None
    assert resources.client is client
    with pytest.raises(TypeError):
        resources.client = object()


@pytest.mark.parametrize("value", [None, {}, {"config": {}}])
def test_resources_factory_must_return_the_explicit_contract(value):
    with pytest.raises(TypeError, match="AgentResources"):
        AgentSpec(name="example", resources=lambda _path: value).build_agent()


def test_agent_resources_reject_unknown_fields_and_do_not_share_defaults():
    with pytest.raises(KeyError):
        AgentResources(configration={})
    first, second = AgentResources(), AgentResources()
    first.configuration["model"] = "local"
    assert second.configuration == {}


@pytest.mark.parametrize("mode", ["ephemeral", "resident", "durable"])
def test_serialized_profile_preserves_session_and_queue_policy(mode, tmp_path):
    spec = AgentSpec(name="specialist", session_mode=mode,
                     queue=QueuePolicy.latest(limit=2, backlog_limit=4))
    restored = AgentSpec.from_profile(json.loads(json.dumps(spec.fleet_profile())))
    assert restored.session_mode == spec.session_mode
    assert restored.queue == spec.queue
    agent = restored.build_agent(tmp_path / "session.json", resources=AgentResources(client=object()))
    assert agent.retain_history == (mode != "ephemeral")
    assert (agent.session.path is not None) == (mode == "durable")


@pytest.mark.parametrize("reference", [
    "the_framework.plugins.registry:RegistryPlugin",
    "the_framework.plugins.registry.plugin:RegistryPlugin",
])
def test_saved_extension_references_migrate_without_changing_session_bindings(reference, tmp_path):
    path = tmp_path / "session.json"
    session = Session.open(path)
    session.set_plugin("registry", False)
    profile = AgentSpec(name="primary", session_mode="durable", extensions=(reference,)).fleet_profile()
    restored = AgentSpec.from_profile(json.loads(json.dumps(profile)))
    agent = restored.build_agent(path, resources=AgentResources(client=object()))
    assert agent.session.id == session.id
    assert agent.extension("registry").activated is False
    assert dict(agent.session.plugins) == {"registry": False}
    assert profile.plugins == [reference]


def test_declared_policies_override_the_source_profile():
    initial = AgentSpec(name="specialist").fleet_profile()
    spec = AgentSpec({
        **AgentSpec.from_profile(initial),
        "session_mode": "resident",
        "queue": QueuePolicy.latest(limit=2, backlog_limit=3),
    })
    restored = AgentSpec.from_profile(spec.fleet_profile())
    assert restored.session_mode == spec.session_mode
    assert restored.queue == spec.queue


def test_profile_instructions_have_one_owner_and_do_not_accumulate():
    original = AgentSpec(name="specialist", instructions=(
        Instruction(name="role", content="Original role"),
    ))
    imported = AgentSpec.from_profile(original.fleet_profile())
    updated = AgentSpec({
        **imported,
        "instructions": (Instruction(name="role", content="Revised role"),),
    })
    for _ in range(3):
        profile = updated.fleet_profile()
        assert profile.instructions == "Revised role"
        updated = AgentSpec.from_profile(profile)
        assert len(updated.instructions) == 1
        assert updated.instructions[0].content == "Revised role"


def test_policy_rejects_unknown_mode_at_declaration():
    with pytest.raises(TypeError, match="mode"):
        QueuePolicy(mode="typo")
    with pytest.raises(TypeError, match="session_mode"):
        AgentSpec(name="agent", session_mode="typo")


@pytest.mark.parametrize("policy", [QueuePolicy.latest(limit=3)])
def test_policy_is_an_immutable_json_model(policy):
    assert type(policy)(json.loads(json.dumps(policy))) == policy
    with pytest.raises(TypeError):
        policy.mode = "changed"
    with pytest.raises(TypeError):
        policy.pop("mode")


def test_latest_queue_does_not_replace_explicit_invalid_backlog():
    assert QueuePolicy.latest(limit=3).backlog_limit == 3
    with pytest.raises(ValueError, match="limits"):
        QueuePolicy.latest(backlog_limit=0)


def test_projection_declaration_is_an_immutable_snapshot():
    project = lambda items: items
    source = {"display": project}
    spec = AgentSpec(name="example", projections=source)
    source.clear()
    assert spec.projections["display"] is project
    with pytest.raises(TypeError):
        spec.projections["other"] = project


def test_agent_spec_builds_identity_instructions_plugins_and_config(tmp_path):
    persisted = []
    initialized = []

    def initialize(agent):
        initialized.append(agent.name)
        agent.marker = True
        return agent

    spec = AgentSpec(
        name="companion",
        description="A reusable companion.",
        extensions=(ExampleExtension,),
        instructions=(Instruction(name="identity", content="Be yourself."),),
        initializers=(initialize,),
    )
    agent = spec.build_agent(
        tmp_path / "session.json",
        resources=AgentResources(
            client=object(),
            configuration={"model": "example-model"},
            persist_config=persisted.append,
        ),
    )

    assert agent.name == "companion"
    assert agent.configs.model == "example-model"
    assert agent.instructions["identity"].content == "Be yourself."
    assert [plugin.title for plugin in agent.extensions] == ["example"]
    assert initialized == ["companion"]
    assert agent.marker is True


def test_agent_spec_composes_command_middleware_in_declaration_order():
    async def scenario():
        events = []

        async def outer(_agent, command, call_next):
            events.append("outer:before")
            async for event in call_next(command):
                yield event
            events.append("outer:after")

        async def inner(_agent, command, call_next):
            events.append("inner:before")
            async for event in call_next(command):
                yield event
            events.append("inner:after")

        spec = AgentSpec(
            name="agent",
            description="Test agent.",
            command_middleware=(outer, inner),
        )
        agent = spec.build_agent(resources=AgentResources(client=object()))

        async def stream(_prompt, **_options):
            yield "event"

        agent.astream = stream
        worker = spec.build_worker(agent, asyncio.Queue().get, lambda _value: None)
        output = [
            event async for event in worker.dispatch(
                PromptRequest(id="turn", prompt="hello")
            )
        ]

        assert output == ["event"]
        assert events == [
            "outer:before", "inner:before", "inner:after", "outer:after"
        ]

    asyncio.run(scenario())


def test_agent_spec_owns_named_session_projections():
    spec = AgentSpec(
        name="agent",
        description="Test agent.",
        projections={"ids": lambda items: [item for item in items if item == "keep"]},
    )
    agent = spec.build_agent(resources=AgentResources(client=object()))
    worker = spec.build_worker(agent, asyncio.Queue().get, lambda _value: None)

    assert worker.projections.project("ids", ["drop", "keep"]) == ["keep"]


def test_disabled_plugin_binding_is_loaded_but_can_be_activated_later():
    spec = AgentSpec(
        name="agent",
        description="Test agent.",
        extensions=(Plugin(
            name="example",
            agent_extension=ExampleExtension,
            agent_binding_enabled=False,
        ),),
    )

    agent = spec.build_agent(resources=AgentResources(client=object()))

    assert [plugin.title for plugin in agent.extensions] == ["example"]
    assert agent.extensions[0].activated is False
    agent.activate_extension("example")
    assert agent.extensions[0].activated is True


def test_required_plugin_binding_overrides_stale_session_state(tmp_path):
    path = tmp_path / "session.json"
    Session.open(path).set_plugin("example", False)
    spec = AgentSpec(
        name="agent",
        session_mode='durable',
        extensions=(Plugin(
            name="example", agent_extension=ExampleExtension, agent_binding_required=True,
        ),),
    )

    agent = spec.build_agent(path, resources=AgentResources(client=object()))

    assert agent.extension("example").activated is True
    assert Session.open(path).plugins["example"] is True
    with pytest.raises(RuntimeError, match="binding is required"):
        agent.deactivate_extension("example")


def test_server_binding_overrides_stale_worker_session(tmp_path):
    path = tmp_path / "session.json"
    Session.open(path).set_plugin("example", True)
    spec = AgentSpec(
        name="agent", session_mode='durable',
        extensions=(Plugin(name="example", agent_extension=ExampleExtension),),
    )

    agent = spec.build_agent(
        path, resources=AgentResources(client=object()),
        binding_overrides={"example": False},
    )

    assert not agent.extension("example").activated
    assert Session.open(path).plugins["example"] is False


def test_agent_spec_accepts_a_plugin_factory():
    created_for = []

    def factory(agent):
        created_for.append(agent.name)
        return ExampleExtension(agent)

    agent = AgentSpec(
        name="agent",
        description="Test agent.",
        extensions=(factory,),
    ).build_agent(resources=AgentResources(client=object()))

    assert created_for == ["agent"]
    assert [plugin.title for plugin in agent.extensions] == ["example"]


def test_worker_changes_plugin_binding_through_the_ordered_command_path():
    async def scenario():
        spec = AgentSpec(
            name="agent",
            description="Test agent.",
            extensions=(ExampleExtension,),
        )
        agent = spec.build_agent(resources=AgentResources(client=object()))
        worker = spec.build_worker(agent, asyncio.Queue().get, lambda _value: None)

        events = [event async for event in worker.execute(PluginBindingRequest(
            id="binding-1", plugin="example", enabled=False,
        ))]

        assert events[0].type == "agent.plugin.binding.updated"
        assert events[0].enabled is False
        assert agent.extension("example").activated is False
        assert agent.session.plugins["example"] is False

    asyncio.run(scenario())


def test_agent_spec_supports_ephemeral_resident_and_durable_sessions(tmp_path):
    ephemeral_spec = AgentSpec(name="ephemeral")
    ephemeral = ephemeral_spec.build_agent(
        tmp_path / "ephemeral.json", resources=AgentResources(client=object())
    )
    resident = AgentSpec(
        name="resident", description="Resident agent.", session_mode='resident'
    ).build_agent(tmp_path / "resident.json", resources=AgentResources(client=object()))
    durable = AgentSpec(
        name="durable", description="Durable agent.", session_mode='durable'
    ).build_agent(tmp_path / "durable.json", resources=AgentResources(client=object()))

    assert ephemeral.session.path is None
    assert ephemeral.description == "Agent worker."
    assert ephemeral.retain_history is False
    assert resident.session.path is None
    assert resident.retain_history is True
    assert durable.session.path == tmp_path / "durable.json"
    assert durable.retain_history is True
