"""Keep local inline Markdown navigation valid, including legacy entry anchors."""
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]


def prose(path):
    return re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)


def anchors(path):
    seen, result = {}, set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*$", prose(path), re.M):
        heading = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", heading).lower()
        slug = "".join(
            char for char in heading
            if char in "_- " or unicodedata.category(char)[0] in "LN"
        ).replace(" ", "-")
        number = seen.get(slug, 0)
        seen[slug] = number + 1
        result.add(slug + (f"-{number}" if number else ""))
    result.update(re.findall(r"<a\s+(?:id|name)=[\"']([^\"']+)", prose(path)))
    return result


def test_documentation_local_links_and_anchors():
    pages = [ROOT / name for name in (
        "README.md", "starter/README.md", "examples/notebook/README.md",
    )] + sorted((ROOT / "docs").rglob("*.md"))
    failures, checked, cached_anchors = [], 0, {}
    for page in pages:
        for target in re.findall(r"(?<!!)\[[^]\n]*\]\(([^)\s]+)\)", prose(page)):
            if urlsplit(target).scheme or target.startswith("//"):
                continue
            part, _, fragment = unquote(target).partition("#")
            destination = (page.parent / part).resolve() if part else page
            checked += 1
            if not destination.exists():
                failures.append(f"{page.relative_to(ROOT)}: missing {target}")
            elif fragment and destination.suffix == ".md":
                if destination not in cached_anchors:
                    cached_anchors[destination] = anchors(destination)
                if fragment not in cached_anchors[destination]:
                    failures.append(f"{page.relative_to(ROOT)}: missing anchor {target}")
    assert checked > 300, "Documentation navigation unexpectedly disappeared"
    assert not failures, "\n".join(failures)
