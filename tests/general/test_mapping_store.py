import json
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest

from the_framework.utils.persistence import MappingStore


def test_mapping_store_roundtrip_is_private_and_versioned(tmp_path):
    path = tmp_path / "settings.json"
    store = MappingStore(path, field="settings")
    assert store.load() == {}
    store.save({"name": "épreuve"})
    assert store.load() == {"name": "épreuve"}
    assert json.loads(path.read_text()) == {"version": 1, "settings": {"name": "épreuve"}}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_failed_replace_preserves_snapshot_and_removes_temporary(tmp_path, monkeypatch):
    store = MappingStore(tmp_path / "state.json")
    store.save({"revision": 1})

    def fail(*args):
        raise OSError("replacement failed")

    monkeypatch.setattr("the_framework.utils.persistence.os.replace", fail)
    with pytest.raises(OSError, match="replacement failed"):
        store.save({"revision": 2})
    assert store.load() == {"revision": 1}
    assert list(tmp_path.iterdir()) == [store.path]


def test_serialization_failure_does_not_touch_snapshot(tmp_path):
    store = MappingStore(tmp_path / "state.json")
    store.save({"revision": 1})
    with pytest.raises(TypeError):
        store.save({"invalid": object()})
    assert store.load() == {"revision": 1}
    assert list(tmp_path.iterdir()) == [store.path]


def test_concurrent_snapshot_writes_do_not_share_a_temporary_file(tmp_path):
    store = MappingStore(tmp_path / "state.json")
    values = [{"revision": index, "body": str(index) * 1000} for index in range(20)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(store.save, values))
    assert store.load() in values
    assert list(tmp_path.iterdir()) == [store.path]


@pytest.mark.parametrize("document", [[], {"version": 2, "data": {}}, {"version": 1, "data": []}])
def test_invalid_mapping_documents_are_rejected(tmp_path, document):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        MappingStore(path).load()
