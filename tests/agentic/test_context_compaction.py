import asyncio
from uuid import UUID

from the_framework.agent.runtime.agentic_loop import AgenticLoop
from the_framework.agent.extensions.commands import Commands
from the_framework.agent.context.compaction import Compaction
from the_framework.agent.models.config import Configs
from the_framework.agent.context.builder import Context
from the_framework.agent.models.usage import ResponseUsage
from the_framework.agent.extensions.hooks import Hooks, hook
from the_framework.agent.extensions.instructions import Instructions
from the_framework.agent.extensions.providers import Providers
from the_framework.agent.models.responses import Compaction as CompactionItem
from the_framework.agent.models.responses import Image, Message, Portrait
from the_framework.agent.context.session import Session
from the_framework.agent.extensions.tools import Tools


class Responses:
    def __init__(self):
        self.compaction_requests = []
        self.compaction_inputs = []
        self.generation_requests = []
        self.generation_inputs = []

    def compact(self, **kwargs):
        self.compaction_requests.append(kwargs)
        self.compaction_inputs.append(kwargs["input"])
        return {
            "id": "compaction",
            "output": [{
                "type": "compaction_summary",
                "encrypted_content": "anchor",
            }],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 7,
                "total_tokens": 17,
            },
        }

    def create(self, **kwargs):
        self.generation_requests.append(kwargs)
        self.generation_inputs.append(kwargs["input"])
        return iter([{
            "type": "response.completed",
            "sequence_number": 1,
            "response": {
                "usage": {
                    "input_tokens": 321,
                    "output_tokens": 12,
                    "total_tokens": 333,
                },
            },
        }])


class Agent:
    def __init__(self, path):
        self.session = Session.open(path)
        self.configs = Configs()
        self.context = Context(self)
        self.commands = Commands()
        self.hooks = Hooks()
        self.instructions = Instructions()
        self.providers = Providers()
        self.tools = Tools()
        self.responses = Responses()
        self.client = type("Client", (), {"responses": self.responses})()
        self.compaction = Compaction(self)
        self.events = []

    def registry(self, name):
        return getattr(self, name)

    def emit(self, event):
        self.events.append(event)
        return event

    def add_message(self, role, text):
        message = Message(role=role, content=[])
        message.set_attr("test_text", text)
        self.session.append(message)
        return message

    def add_response_item(self, item):
        self.session.append(item)
        return item


def test_step_compacts_the_complete_session_including_the_active_turn(tmp_path):
    agent = Agent(tmp_path / "session.json")
    old = Message(role="user", content=[])
    agent.session.append(old)
    agent.session.set_context_usage(ResponseUsage(
        input_tokens=190_000,
        output_tokens=1_000,
        total_tokens=191_000,
    ))
    agent.context.local_input_tokens = lambda payload: 190_000
    loop = AgenticLoop(agent)

    asyncio.run(loop.turn("new turn"))

    compacted = agent.responses.compaction_inputs[0]
    assert compacted[0] == {"type": "message", "role": "user", "content": []}
    assert [item["type"] for item in compacted] == ["message", "message"]
    assert "context_token_limit" not in agent.responses.compaction_requests[0]
    assert "trigger_ratio" not in agent.responses.compaction_requests[0]
    assert "archive_anchor_limit" not in agent.responses.compaction_requests[0]
    assert agent.responses.compaction_requests[0]["model"] == "gpt-5.6-luna"
    assert agent.responses.compaction_requests[0]["reasoning"] == {
        "effort": "high",
        "summary": None,
        "generate_summary": None,
    }
    assert agent.responses.compaction_requests[0]["text"] == {
        "format": None,
        "verbosity": "medium",
    }
    cache_key = agent.responses.compaction_requests[0]["prompt_cache_key"]
    assert UUID(cache_key).version == 5
    assert agent.responses.generation_requests[0]["prompt_cache_key"] == cache_key
    assert [item["type"] for item in agent.responses.generation_inputs[0]] == [
        "compaction_summary",
    ]
    assert [item.type for item in agent.session.archive] == [
        "message",
        "message",
        "compaction_summary",
    ]
    assert agent.session.tail_start == 1
    assert agent.session.anchor_token_count == 7
    assert agent.session.context_usage.input_tokens == 321
    compaction_start = next(
        event for event in agent.events
        if event.type == "agent.compaction.start"
    )
    assert dict(compaction_start) == {"type": "agent.compaction.start"}
    assert [event.type for event in agent.events].index("agent.compaction.end") < (
        [event.type for event in agent.events].index("agent.generation.start")
    )


def test_local_images_are_projected_for_generation_and_compaction_then_retired(tmp_path):
    agent = Agent(tmp_path / "session.json")
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"local-image")
    agent.session.append(Message(role="user", content=[]))
    agent.session.append(Image(
        path=str(image_path),
        description="A retained image",
        parameters={"seed": 7},
    ))

    generation = agent.context.input()
    agent.compaction.run()
    after_compaction = agent.context.input()

    assert [item.get("type") for item in generation] == ["message", "message"]
    assert generation[-1]["content"][0]["type"] == "input_image"
    compacted = agent.responses.compaction_inputs[-1]
    assert [item["type"] for item in compacted] == ["message", "message"]
    assert compacted[-1]["content"][0]["type"] == "input_image"
    assert compacted[-1]["content"][0]["detail"] == "auto"
    assert [item["type"] for item in after_compaction] == ["compaction_summary"]


def test_portrait_prefix_survives_compaction_and_ignores_zero_image_limit(tmp_path):
    agent = Agent(tmp_path / "session.json")
    agent.configs.max_input_images = 0
    portrait_path = tmp_path / "portrait.png"
    portrait_path.write_bytes(b"portrait")

    @hook
    def context_prefix(items):
        return [Portrait(path=str(portrait_path)), *items]

    agent.hooks.add(context_prefix)
    agent.session.append(Message(role="user", content=[]))

    generation = agent.context.input()
    agent.compaction.run()
    compacted = agent.responses.compaction_inputs[-1]
    after_compaction = agent.context.input()

    for projected in (generation, compacted, after_compaction):
        assert projected[0]["content"][0]["type"] == "input_image"
        assert "current avatar" in projected[0]["content"][1]["text"]
    assert [item["type"] for item in compacted] == ["message", "message"]
    assert [item["type"] for item in after_compaction] == [
        "message",
        "compaction_summary",
    ]


def test_context_prefix_precedes_every_item_in_compacted_output(tmp_path):
    agent = Agent(tmp_path / "session.json")
    portrait_path = tmp_path / "portrait.png"
    portrait_path.write_bytes(b"portrait")

    @hook
    def context_prefix(items):
        return [Portrait(path=str(portrait_path)), *items]

    agent.hooks.add(context_prefix)
    preserved_recent = Message(role="assistant", content=[])
    anchor = CompactionItem(encrypted_content="anchor")
    agent.session.commit_compaction(
        [preserved_recent, anchor],
        anchor_id="compaction",
        archive_anchor_limit=10,
    )
    tail = Message(role="assistant", content=[])
    agent.session.append(tail)

    composed = agent.context.compose_session_items()
    assert isinstance(composed[0], Portrait)
    assert composed[1:] == [preserved_recent, anchor, tail]

    projected = agent.context.input()
    assert projected[0]["content"][0]["type"] == "input_image"
    assert [item["type"] for item in projected[1:]] == [
        "message",
        "compaction",
        "message",
    ]


def test_generation_uses_stable_session_cache_key_and_honors_override(tmp_path):
    agent = Agent(tmp_path / "session.json")
    agent.session.save()
    loop = AgenticLoop(agent)

    automatic = loop.generation_payload()["prompt_cache_key"]
    assert automatic == AgenticLoop(Agent(tmp_path / "session.json")).generation_payload()[
        "prompt_cache_key"
    ]

    agent.configs.prompt_cache_key = "explicit-cache-key"
    assert loop.generation_payload()["prompt_cache_key"] == "explicit-cache-key"

    agent.configs.compaction.prompt_cache_key = "explicit-compaction-key"
    agent.compaction.run(input=[])
    assert agent.responses.compaction_requests[-1]["prompt_cache_key"] == (
        "explicit-compaction-key"
    )


def test_step_uses_local_count_instead_of_server_usage_for_compaction(tmp_path):
    agent = Agent(tmp_path / "session.json")
    agent.session.append(Message(role="user", content=[]))
    agent.session.set_context_usage(ResponseUsage(
        input_tokens=999_999,
        output_tokens=1,
        total_tokens=1_000_000,
    ))
    agent.context.local_input_tokens = lambda payload: 189_999
    loop = AgenticLoop(agent)

    asyncio.run(loop.turn("new turn"))

    assert agent.responses.compaction_inputs == []


def test_step_can_compact_when_the_history_contains_only_the_active_turn(tmp_path):
    agent = Agent(tmp_path / "session.json")
    active_turn_item = Message(role="assistant", content=[])
    agent.session.append(active_turn_item)
    agent.session.set_context_usage(ResponseUsage(
        input_tokens=195_000,
        output_tokens=1_000,
        total_tokens=196_000,
    ))
    agent.context.local_input_tokens = lambda payload: 195_000
    loop = AgenticLoop(agent)

    asyncio.run(loop.step())

    assert agent.responses.compaction_inputs == [[{
        "type": "message",
        "role": "assistant",
        "content": [],
    }]]
    assert [item.type for item in agent.session.history] == ["compaction_summary"]
    assert [item.type for item in agent.session.archive] == [
        "message",
        "compaction_summary",
    ]


def test_stateful_async_provider_is_sampled_once_when_step_compacts(tmp_path):
    agent = Agent(tmp_path / "session.json")
    agent.session.append(Message(role="user", content=[]))
    calls = 0

    @agent.providers.add(channels=["text"])
    async def cursor_provider():
        nonlocal calls
        calls += 1
        return {"sample": calls}

    agent.context.local_input_tokens = lambda payload: 195_000
    loop = AgenticLoop(agent)

    asyncio.run(loop.step())

    assert calls == 1
    provider_item = agent.responses.generation_inputs[0][-1]
    assert '"sample":1' in provider_item["content"][0]["text"]


def test_compaction_end_retires_the_previous_anchor_not_the_new_one(tmp_path):
    agent = Agent(tmp_path / "session.json")
    agent.session.append(Message(role="user", content=[]))

    first = agent.compaction.run()
    first_event = agent.events[-1]
    agent.session.append(Message(role="assistant", content=[]))
    second = agent.compaction.run()
    second_event = agent.events[-1]

    assert first_event.type == "agent.compaction.end"
    assert first_event.retired_anchor_id is None
    assert first_event.retired_items == []
    assert second_event.retired_anchor_id == first.id
    assert second_event.retired_items == first.output
    assert second_event.retired_items != second.output
    assert agent.session.history == second.output


def test_local_count_projects_wire_messages_and_uses_observed_anchor_tokens(tmp_path):
    agent = Agent(tmp_path / "session.json")
    agent.session.anchor_token_count = 123
    payload = {
        "model": "gpt-5.4",
        "instructions": "Stay concise.",
        "input": [
            {
                "type": "compaction_summary",
                "encrypted_content": "x" * 100_000,
            },
            {
                "type": "message",
                "role": "developer",
                "kind": "skill",
                "call_id": "local-only",
                "content": [{"type": "input_text", "text": "Visible context."}],
            },
        ],
        "tools": [],
        "stream": True,
    }

    projected = agent.context.local_token_payload(payload)
    count = agent.context.local_input_tokens(payload)

    assert projected["input"][0]["encrypted_content"] == "<encrypted_compaction_summary>"
    assert projected["input"][1] == {
        "type": "message",
        "role": "developer",
        "content": [{"type": "input_text", "text": "Visible context."}],
    }
    assert count >= 123
    assert count < 1_000


def test_local_token_count_drives_the_configured_compaction_threshold(tmp_path):
    agent = Agent(tmp_path / "session.json")
    payload = {
        "instructions": "Preserve conversational continuity.",
        "input": [{
            "type": "message",
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": "un contexte mesuré localement " * 50,
            }],
        }],
        "tools": [],
    }
    measured = agent.context.local_input_tokens(payload)
    agent.configs.compaction.trigger_ratio = 1.0
    agent.configs.compaction.context_token_limit = measured

    assert agent.context.needs_compaction(payload)

    agent.configs.compaction.context_token_limit = measured + 1
    assert not agent.context.needs_compaction(payload)
