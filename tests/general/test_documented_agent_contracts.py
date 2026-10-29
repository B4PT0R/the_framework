"""Execute complete Python sequences published in the documentation (no inference)."""
import re
from pathlib import Path

import pytest


@pytest.mark.parametrize("page", [
    "README.md", "docs/reference/README.md", "docs/deployment.md",
    *(f"docs/reference/{name}.md" for name in ("tools", "extensions", "providers", "agents",
        "application", "plugins", "services", "hosting", "sessions", "context",
        "events", "security", "realtime", "artifacts", "surfaces", "browser", "execution", "primitives", "work-and-scheduling")),
])
def test_reference_python_sequences(page):
    path = Path(__file__).resolve().parents[2] / page
    namespace = {"__name__": "documentation_example"}
    blocks = re.findall(r"```python\n(.*?)\n```", path.read_text(), re.S)
    assert blocks, f"{path} no longer contains an executable example"
    for index, source in enumerate(blocks):
        exec(compile(source, f"{path}:block-{index + 1}", "exec"), namespace)
