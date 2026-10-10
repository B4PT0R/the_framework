"""Evidence curation and provenance-preserving maintenance share one checklist."""
import pytest

from the_framework.agent import Agent, AgentResult
from the_framework.plugins.memory.agent import MemoryExtension
from the_framework.plugins.memory.curator import MemoryCuratorExtension


@pytest.fixture
def curator(tmp_path, monkeypatch):
    agent = Agent(tmp_path / "curator-session.json", memory_curator={
        "store_path": str(tmp_path / "memory.sqlite3"),
    })
    extension = MemoryCuratorExtension(agent).load()
    monkeypatch.setattr(extension, "embed_many", lambda contents: [[1.0, 0.0] for _ in contents])
    return extension


def seed(curator, key="project", sources=None):
    return curator.store.add(key=key, title=key, content="The project uses Python. " * 8,
                             embedding=[1.0, 0.0], source_item_ids=sources or ["original-source"])


def test_maintenance_can_review_rewrite_and_complete_without_new_sources(curator):
    entry = seed(curator)
    first = curator.inventory([], [], [])
    assert not first["final_review"]
    assert 'mode="maintenance"' in curator.complete_memory()
    curator.update(entry.id, "The project uses Python.", [], [])
    updated = curator.store.find(entry.id)
    assert updated.source_item_ids == entry.source_item_ids
    assert curator.store.pending_embeddings()
    with pytest.raises(ValueError, match="final evidence review"):
        curator.complete_curation("Project wording shortened.")
    final = curator.inventory([], [], [])
    assert final["final_review"]
    result = curator.complete_curation("Reviewed project; removed repetition without changing facts.")
    assert result["status"] == "curation_complete"
    assert result["reindexed_entries"] == 1
    assert not curator.store.pending_embeddings()
    assert curator.agent.end_of_turn_requested


def test_maintenance_cannot_introduce_unsourced_candidates_or_memories(curator):
    with pytest.raises(ValueError, match="new evidence"):
        curator.inventory(["new"], ["Unsupported new fact."], [])
    assert 'status="missing"' in curator.complete_memory()
    curator.inventory([], [], [])
    with pytest.raises(ValueError, match="inventoried candidates"):
        curator.add("new", "New", "Unsupported new fact.", [], [])
    assert not curator.store.active()


@pytest.mark.parametrize("sources", [[""], [" "], ["valid", ""]])
def test_inventory_rejects_blank_source_ids(curator, sources):
    with pytest.raises(ValueError, match="non-empty"):
        curator.inventory([], [], sources)


def test_evidence_still_requires_candidates_provenance_and_final_review(curator):
    with pytest.raises(ValueError, match="before mutating"):
        curator.add("project", "Project", "Uses Python.", ["session-source"], ["fact"])
    curator.inventory(["fact"], ["The project uses Python."], ["session-source"])
    for sources in ([], ["different-source"]):
        with pytest.raises(ValueError, match="canonical source_item_ids"):
            curator.add("project", "Project", "Uses Python.", sources, ["fact"])
    with pytest.raises(ValueError, match="unresolved"):
        curator.complete_curation("Not yet preserved.")
    curator.add("project", "Project", "The project uses Python.", ["session-source"], ["fact"])
    curator.inventory([], [], ["session-source"])
    assert curator.complete_curation("Preserved project language.")["status"] == "curation_complete"
    assert curator.store.active()[0].source_item_ids == ["session-source"]
    assert not curator.inventory([], [], [])["final_review"]


def test_maintenance_merge_and_split_inherit_existing_sources(curator):
    first = seed(curator, "one", ["source-one"])
    second = seed(curator, "two", ["source-two"])
    curator.inventory([], [], [])
    merged = curator.merge([first.id, second.id], "merged", "Project", "Uses Python; tested locally.", [], [])
    assert set(curator.store.find(merged["id"]).source_item_ids) == {"source-one", "source-two"}
    split = curator.split(merged["id"], "language", "Language", "Uses Python.",
                          "tests", "Tests", "Tested locally.", [], [])
    for memory_id in split["ids"]:
        assert set(curator.store.find(memory_id).source_item_ids) == {"source-one", "source-two"}


def test_generated_maintenance_request_can_be_processed(tmp_path, monkeypatch):
    class ApplicationMemory(MemoryExtension):
        curator_instructions = "Preserve supported evidence."

    agent = Agent(tmp_path / "session.json", memory={"entry_maintenance_token_threshold": 16})
    memory = ApplicationMemory(agent).load()
    original = memory.store.add(key="project", title="Project", content="The project uses Python. " * 8,
                                embedding=[1.0, 0.0], source_item_ids=["original-source"])
    requests = []
    agent.events.subscribe("agent.task.requested", requests.append)
    result = AgentResult(id="result", task_id="task", profile="memory.jiminy", status="completed",
                         watermark=agent.session.watermark())
    assert memory.jiminy(result)["maintenance_requested"]
    request = requests[0]
    assert request.source_item_ids == []
    curator = MemoryCuratorExtension(Agent(memory_curator={
        "store_path": str(memory.store.path), "entry_maintenance_token_threshold": 16,
    })).load()
    monkeypatch.setattr(curator, "embed_many", lambda contents: [[1.0, 0.0] for _ in contents])
    curator.inventory([], [], request.source_item_ids)
    curator.edit(original.id, original.content, "The project uses Python.", [], [])
    curator.inventory([], [], request.source_item_ids)
    assert curator.complete_curation("Removed repeated wording; preserved Python preference.")["status"] == "curation_complete"
    assert memory.store.find(original.id).source_item_ids == ["original-source"]
    assert not memory.jiminy(result)["maintenance_requested"]


def test_maintenance_completion_keeps_volume_and_editorial_guards(curator):
    seed(curator)
    curator.inventory([], [], [])
    curator.inventory([], [], [])
    curator.config.entry_maintenance_token_threshold = 4
    with pytest.raises(ValueError, match="oversized"):
        curator.complete_curation("Review is not enough to excuse an oversized entry.")
    curator.config.entry_maintenance_token_threshold = 500
    curator.store.token_budget = 1
    with pytest.raises(ValueError, match="strictly below 100%"):
        curator.complete_curation("Review is not enough to excuse saturated memory.")
    assert not curator.agent.end_of_turn_requested


def test_split_capacity_recovery_and_atomic_rollback(curator):
    entry = curator.store.add(key="large", title="Large", content="Uses Python. " * 600,
                              embedding=[1.0, 0.0], source_item_ids=["original-source"])
    curator.inventory([], [], [])
    curator.store.token_budget = 1  # Simulate an existing table above a newly lowered limit.
    split = curator.split(entry.id, "one", "One", "Uses Python. " * 60,
                          "two", "Two", "Uses Python. " * 60, [], [])
    before = [(item.id, item.content) for item in curator.store.active()]
    with pytest.raises(ValueError, match="hard token budget"):
        curator.split(split["ids"][0], "expanded-one", "Expanded one", "Uses Python. " * 600,
                      "expanded-two", "Expanded two", "Uses Python. " * 600, [], [])
    assert [(item.id, item.content) for item in curator.store.active()] == before
    assert all(item.source_item_ids == ["original-source"] for item in curator.store.active())
