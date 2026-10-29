# Artifacts, files and vision

An `Artifact` asks a client to present JSON-described content. An `Image` supplies
pixels to the model. Neither implies the other, and a persisted presentation
request is not evidence that a client rendered it or a person saw it.

## Choose the right output

| Output | Model receives | Client receives | Persistence |
| --- | --- | --- | --- |
| `Artifact(kind=..., payload=...)` from a tool/command | Developer-message description of the request | Typed presentation item | Canonical session and bounded archive |
| `Image(path=..., description=...)` from a tool | Image bytes encoded at the API boundary, with metadata | No presentation request | Local path and metadata in session |
| Provider artifact/image | Ephemeral model projection | Provider artifacts may request temporary presentation | No history/archive entry |

Return multiple typed outputs in a tuple; a list is ordinary structured output.
See [tool outputs](tools.md) and [provider lifetime](providers.md). A repeated
artifact ID is idempotent while retained; it is not an independent permanent
publication registry. Clients rebuild durable presentations from history after
reconnection. They discard temporary provider presentations on turn termination
or connection loss; the next provider sample replaces the previous view.

## Publish local bytes

Declare `ARTIFACTS` from `the_framework.plugins.artifacts` in the application.
It supplies the agent extension, a session-scoped asset service and the bucket
endpoint. The shared bucket is `data_root/bucket/`, not session-private storage.
Only put material intended for authenticated clients there; keep credentials,
private sessions, browser profiles and deployment state outside it.

The `artifacts.show_artifact` tool takes `kind` and a JSON `payload`:

- `image`, `audio`, `file`: supply an existing **server-local** `payload.path`;
- optional `title` and `description` label the presentation; images can use `alt`;
- custom kinds carry JSON interpreted by an application-owned renderer.

A host-only path is not transferred into a container. The service copies bytes
under a unique name, removes `path` and any caller-supplied `src`, and returns
`src`, `name`, `size`, `mime_type` and remaining metadata. Reusing the same source
creates another publication, not a content-addressed deduplication.

This executable example exercises the asset service directly, **not** the worker
bridge, tool pipeline or browser renderer. It only writes temporary files:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote

from the_framework.agent import Artifact
from the_framework.plugins.artifacts.server import ArtifactAssets

with TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / "report.txt"
    source.write_text("first version", encoding="utf-8")
    assets = ArtifactAssets(root / "application")
    payload = assets.publish("file", {
        "path": str(source), "title": "Experiment report",
    })
    artifact = Artifact(kind="file", payload=payload)
    published = assets.root / unquote(payload["src"].split("/bucket/", 1)[1])
    source.unlink()
    assert published.read_text() == "first version"
    assert "path" not in artifact.payload
    assert artifact.payload["mime_type"] == "text/plain"
    assert "presentation request" in artifact.to_api_format()["content"][0]["text"]
```

An extension can avoid an extra display-tool invocation: await
`self.agent.bridge.call("artifacts", "publish", {"kind": "file", "payload":
{"path": str(result_path)}})`, then return `Artifact(kind="file", payload=result)`.
The bytes copy happens server-side; session commits remain worker-owned. Returning
that artifact already requests presentation: do not publish it a second time.

Image publication verifies PNG/JPEG/WebP/GIF raster content and fixes the served
suffix/MIME when necessary. Audio publication checks for a recognized audio
**filename/MIME**, not decoded audio validity; it requests manual playback, never
autoplay. Generic files are not interpreted as UI code. The bucket serves them
with attachment disposition, `nosniff`, sandbox CSP and byte-range support.

`FileBucket(data_root)` in `the_framework.server.api.bucket` provides `root`,
`url(path)` and `GET /api/v1/bucket/{path:path}`. The endpoint requires authentication
and `session:read`, interpreted by the application's [security policy](security.md).
Traversal, directories and resolved symlinks outside the bucket are rejected;
filenames are not credentials. Disabling an agent binding leaves routes/assets
available. Resetting a session does not automatically delete assets. Conversely,
deleting a published file leaves an artifact whose content is unavailable.

## Declare a custom kind

The catalogue is application-wide, not negotiated with the current device.
`supported_artifacts()` returns independent JSON values with `description`,
`payload_schema` and optional `example`. Built-ins are **image, audio and file**.
Their input schema uses `path`; stored presentations use `src`.

Use a typed `Artifact` subclass for custom payload validation and schema generation:

```python
import asyncio
from modict import modict
from the_framework.agent import Agent, Artifact, Session
from the_framework.plugins.artifacts import ARTIFACTS, ArtifactsExtension
from the_framework import Plugin

class ResultPayload(modict):
    _config = modict.config(extra="forbid")
    experiment: str
    score: float

class ResultArtifact(Artifact):
    """Show a measured experiment result."""
    kind = "experiment_result"
    payload: ResultPayload = modict.field(required="always")
    payload_example = modict.attr({"experiment": "baseline", "score": 0.75})

class Results(ArtifactsExtension):
    artifact_types = (ResultArtifact,)

plugin = Plugin({**ARTIFACTS, "agent_extension": Results})
extension = Results(Agent(client=object()))
catalogue = extension.supported_artifacts()
assert set(catalogue) == {"image", "audio", "file", "experiment_result"}
assert catalogue["experiment_result"]["payload_schema"]["additionalProperties"] is False
result = asyncio.run(extension.show_artifact(
    "experiment_result", {"experiment": "baseline", "score": 0.75},
))
assert isinstance(result, ResultArtifact)
session = Session()
session.append(result)
assert session.history[-1].type == "artifact"
```

This calls the extension directly without an inference request. Register the
class explicitly in `artifact_types`; kinds must be nonempty and cannot duplicate
a built-in or another declaration. `required="always"` keeps `payload` required
under the Framework base model's otherwise optional field policy. Payload models
supply their own modict required/default rules; Python validators are executable
constraints, not automatically inferred JSON Schema. `show_artifact` uses
`strict: false` so arbitrary JSON keys survive function-schema generation.

Custom items retain canonical `type="artifact"`. Replay needs the kind and JSON,
not the Python subclass. Unknown kinds may still be returned as generic artifacts;
the declaration does not install a renderer. Implement the renderer in each
application UI. The starter `App` accepts `artifactRenderers`; unknown kinds and
renderer errors fall back to readable JSON. Payloads are descriptions, not
executable HTML or JavaScript. Do not document a renderer as available merely
because a model can emit its kind.

## Freeze a vision source

`Image.from_source` accepts local paths, bytes or HTTP(S) URLs. It validates the
raster and writes a SHA-256-named local file before constructing the item:

```python
from PIL import Image as RasterImage
from the_framework.agent import Image

with TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / "reference.png"
    RasterImage.new("RGB", (8, 8), "teal").save(source)
    image = Image.from_source(
        source, directory=root / "frozen", description="Reference for analysis",
    )
    source.unlink()
    assert Path(image.path).is_file()
    api = image.to_api_format()
    assert api["content"][0]["image_url"].startswith("data:image/png;base64,")
    assert image.type != "artifact"
```

Local/remote/byte input is limited to 50 MiB and PNG/JPEG/WebP/GIF. Remote retrieval
follows redirects with image Accept and Framework User-Agent headers and a
30-second HTTP timeout. This is blocking I/O: async tools should use
`await asyncio.to_thread(Image.from_source, ...)`. Remote failures occur before
injection. A URL is downloaded only during explicit preparation; projection and
deserialization never fetch it. `source_url` is persisted provenance, so do not
use secret-bearing URLs casually. Outgoing model input contains frozen bytes,
not that remote URL. Ordinary `Image(path=...)` does not copy or validate its
source; the caller must keep it available. Neither representation displays the
image in chat. Freeze and publish separately when both vision and presentation
are intended; cleanup of those files is independent of archive retention.

## Implementation and checks

- Models and projections: `the_framework/agent/models/responses.py`.
- Catalogue/tools and publication: `the_framework/plugins/artifacts/{agent,server}.py`.
- Bucket/media/uploads: `the_framework/server/api/{bucket,media,uploads}.py`.
- Source freezing: `the_framework/utils/images.py`.
- Contracts: `tests/general/test_{artifacts,artifact_assets,file_bucket,image_sources}.py`.

[Reference index](README.md) · [Client integration](../guides/client-integration.md)
