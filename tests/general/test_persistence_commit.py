"""Replacement commits memory too, even when crash durability is uncertain."""
import json
import os
import stat

import pytest

from the_framework.agent.context.session import Session
from the_framework.agent.models.responses import Message
from the_framework.utils import persistence


@pytest.fixture
def fail_directory_sync(monkeypatch):
    sync = os.fsync

    def fail(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory sync failed")
        return sync(fd)

    monkeypatch.setattr(persistence.os, "fsync", fail)


@pytest.mark.parametrize("mutation", [
    lambda s: s.append(Message(role="user", content=[])),
    lambda s: s.replace([], anchor_id="new"),
    lambda s: s.commit_compaction([], anchor_id="new", archive_anchor_limit=2),
    lambda s: s.clear_context(),
    lambda s: s.set_command("new", "queued"),
    lambda s: s.discard_commands(prefixes=("old",)),
    lambda s: s.recover_commands(),
    lambda s: s.set_plugin("example", True),
    lambda s: s.update_agent(configuration={"model": "new"}, state={"ready": True}),
])
def test_session_retains_committed_mutation(tmp_path, fail_directory_sync, caplog, mutation):
    path = tmp_path / "session.json"
    session = Session.open(path)
    session.append(Message(role="user", content=[]))
    session.set_command("old", "active")
    mutation(session)
    assert json.loads(json.dumps(session)) == json.loads(json.dumps(Session.open(path)))
    assert "crash durability is uncertain" in caplog.text
    # A later mutation must not overwrite the committed file with stale memory.
    previous = json.loads(path.read_text())
    session.set_command("later", "queued")
    assert json.loads(json.dumps(session)) == json.loads(json.dumps(Session.open(path)))
    assert json.loads(json.dumps(session.history)) == previous["history"]
    assert session.revision == previous["revision"]
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("stage", ["serialization", "file_sync", "replace"])
def test_session_rolls_back_only_before_commit(tmp_path, monkeypatch, stage):
    path = tmp_path / "session.json"
    session = Session.open(path)
    session.append(Message(role="user", content=[]))
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("before commit")

    if stage == "serialization":
        monkeypatch.setattr(json, "dump", fail)
    else:
        monkeypatch.setattr(persistence.os, "fsync" if stage == "file_sync" else "replace", fail)
    with pytest.raises(OSError, match="before commit"):
        session.append(Message(role="user", content=[]))
    assert path.read_bytes() == before
    assert json.loads(json.dumps(session)) == json.loads(json.dumps(Session.open(path)))
    assert session.revision == 1
    assert list(tmp_path.iterdir()) == [path]


def test_mapping_owner_can_commit_memory_after_directory_sync_failure(tmp_path, fail_directory_sync):
    store = persistence.MappingStore(tmp_path / "mapping.json")
    state = {"revision": 1}
    store.save(state)
    next_state = {"revision": 2}
    store.save(next_state)
    state = next_state
    assert state == store.load()


def test_registry_owner_retains_commit_and_subsequent_edit(tmp_path, fail_directory_sync):
    from the_framework.agent import Agent
    from the_framework.plugins.registry import RegistryExtension

    agent = Agent(client=object())
    registry = agent.add_extension(RegistryExtension(agent, runtime_root=tmp_path))
    registry.set_value("$.first", "1")
    registry.set_value("$.second", "2")
    assert registry.store.data == {"first": 1, "second": 2}
    assert registry.store.revision == 2
    assert json.loads((tmp_path / "registry.json").read_text()) == registry.store.data
