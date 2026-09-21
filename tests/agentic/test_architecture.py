import ast
from importlib.util import resolve_name
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[2]
CORE_ROOT = PROJECT_ROOT / "harness_core"


def test_payload_models_do_not_depend_on_context_or_execution():
    """Lower-level payloads must remain reusable outside an agent runtime."""
    violations = []
    for path in (CORE_ROOT / "agent" / "models").rglob("*.py"):
        package = ".".join(path.relative_to(PROJECT_ROOT).parts[:-1])
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                target = "." * node.level + (node.module or "")
                imports = [resolve_name(target, package) if node.level else target]
            elif isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            else:
                continue
            for target in imports:
                if target.startswith((
                    "harness_core.agent.context",
                    "harness_core.agent.extensions",
                    "harness_core.agent.runtime",
                    "harness_core.server",
                    "harness_core.plugins",
                    "the_harness",
                )):
                    violations.append(f"{path.name}: {target}")
    assert not violations


def test_agent_core_has_no_product_or_server_dependencies():
    forbidden_text = ("Pandora", "TheHarness", "The Harness", "love_harness")
    forbidden_imports = (
        "from harness_core.server",
        "import harness_core.server",
        "from the_harness",
        "import the_harness",
    )

    violations = []
    assert (CORE_ROOT / "agent" / "__init__.py").is_file()
    for path in (CORE_ROOT / "agent").rglob("*"):
        if path.suffix not in {".py", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in (*forbidden_text, *forbidden_imports):
            if marker in text:
                violations.append(f"{path.relative_to(PROJECT_ROOT)}: {marker}")

    assert violations == []


def test_server_package_has_no_product_identity_or_composition_imports():
    violations = []
    assert (CORE_ROOT / "server" / "__init__.py").is_file()
    for path in (CORE_ROOT / "server").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in (
            "the_harness",
            "Pandora",
            "TheHarness",
            "The Harness",
            "love-harness",
        ):
            if marker in text:
                violations.append(f"{path.relative_to(PROJECT_ROOT)}: {marker}")

    assert violations == []
