"""Stable dependency ordering shared by compilation and runtime lifecycle."""


def dependency_order(dependencies, *, kind):
    """Return dependencies first, preserving declaration order between peers."""
    graph = {name: tuple(values) for name, values in dependencies.items()}
    missing = {value for values in graph.values() for value in values if value not in graph}
    if missing:
        raise ValueError(f"unknown {kind} dependencies: {', '.join(sorted(missing))}")
    visiting, visited, ordered = set(), set(), []

    def visit(name):
        if name in visited:
            return
        if name in visiting:
            raise ValueError(f"cyclic {kind} dependency: {name}")
        visiting.add(name)
        for dependency in graph[name]:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)
        ordered.append(name)

    for name in graph:
        visit(name)
    return ordered
