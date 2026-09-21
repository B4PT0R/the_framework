import json

from the_framework.agent.context.compaction import Compaction
from the_framework.agent.models.usage import ResponseUsage
from the_framework.agent.models.config import Configs
from the_framework.agent.models.responses import Message
from the_framework.agent.context.session import Session


class Responses:
    def compact(self, **kwargs):
        return {
            "id": "compaction",
            "output": [
                {
                    "type": "compaction_summary",
                    "encrypted_content": "anchor",
                },
            ],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 25,
                "total_tokens": 125,
            },
        }


class Agent:
    def __init__(self, path):
        self.client = type("Client", (), {"responses": Responses()})()
        self.configs = Configs()
        self.session = Session.open(path)

    def emit(self, event):
        return event


def test_compaction_atomically_becomes_the_recovery_anchor(tmp_path):
    path = tmp_path / "session.json"
    agent = Agent(path)
    agent.session.append(Message(role="user", content=[]))

    Compaction(agent).run()
    agent.session.append(Message(role="user", content=[]))
    restored = Session.open(path)

    assert [item.type for item in restored.history] == [
        "compaction_summary",
        "message",
    ]
    assert restored.history[0].encrypted_content == "anchor"
    assert restored.anchor_token_count == 25
    assert restored.watermark() == {
        "session_id": restored.id,
        "revision": 3,
        "anchor_id": "compaction",
        "tail_start": 1,
    }


def test_compaction_replaces_all_history_and_invalidates_old_usage(tmp_path):
    path = tmp_path / "session.json"
    agent = Agent(path)
    old = Message(role="user", content=[])
    agent.session.append(old)
    agent.session.set_context_usage(ResponseUsage(
        input_tokens=190_000,
        output_tokens=100,
        total_tokens=190_100,
    ))

    Compaction(agent).run()
    restored = Session.open(path)

    assert [item.type for item in restored.history] == ["compaction_summary"]
    assert restored.archive[0].id == old.id
    assert restored.archive[1].type == "compaction_summary"
    assert restored.tail_start == 1
    assert restored.context_usage is None


def test_orphaned_temporary_write_is_not_loaded(tmp_path):
    path = tmp_path / "session.json"
    session = Session.open(path)
    session.append(Message(role="user", content=[]))
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text('{"id":"broken"')

    restored = Session.open(path)

    assert restored.id == session.id
    assert len(restored.history) == 1


def test_legacy_session_initializes_archive_from_its_current_history(tmp_path):
    path = tmp_path / "session.json"
    session = Session.open(path)
    message = session.append(Message(role="user", content=[]))
    payload = json.loads(path.read_text())
    payload.pop("archive")
    path.write_text(json.dumps(payload))

    restored = Session.open(path)

    assert [item.id for item in restored.archive] == [message.id]
