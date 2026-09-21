import pytest

from core.server.composition.dependencies import dependency_order


def test_stable_order_visits_shared_dependencies_once():
    graph = {"first": ["shared"], "second": ["shared"], "shared": [], "last": []}
    assert dependency_order(graph, kind="test") == ["shared", "first", "second", "last"]
    assert graph["first"] == ["shared"]
    assert dependency_order({}, kind="test") == []


@pytest.mark.parametrize("graph", [{"a": ["a"]}, {"a": ["b"], "b": ["a"]}])
def test_cycles_fail_before_any_runtime_is_started(graph):
    with pytest.raises(ValueError, match="cyclic test dependency"):
        dependency_order(graph, kind="test")


def test_missing_dependencies_are_reported_together():
    with pytest.raises(ValueError, match="unknown test dependencies: x, z"):
        dependency_order({"a": iter(["z", "x"])}, kind="test")
