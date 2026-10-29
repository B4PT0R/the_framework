# Files and conversation artifacts

[Documentation](../README.md) / [Reference](README.md)

To give the user a downloadable file, an image or a widget, return a conversation
artifact. Files intended for clients go through an authenticated shared bucket;
artifact records describe how clients should present them.

This page covers publication, storage and presentation. Sending an image to a
model for inspection is a separate operation from displaying it to the user.

## Client file bucket

All client-visible files share one tree: `data_root/bucket/`. The application
chooses its private data root. Uploads and published images, documents,
audio and videos use this same root. Subfolders may organize projects or resources;
there are no separate storage roots by media type. Private sessions, credentials,
memory, browser profiles and UI releases remain outside this tree.

`FileBucket(data_root)` from `the_framework.server.api.bucket` exposes `.root`,
`.url(path)` and an authenticated `GET /api/v1/bucket/{path:path}` endpoint. The
complete `ARTIFACTS` plugin includes that endpoint; the same files are served to
local clients and authorized remote desktop clients. Nested paths are supported;
directory listing, traversal and symlinks escaping the bucket are rejected.
Delivery supports byte ranges for large media and uses attachment disposition.
Only publish files intended for clients into this tree. There is no migration or
alias for earlier experimental `files/` and `artifacts/` layouts.

## Conversation artifacts

`Artifact` from `the_framework.agent` expresses presentation explicitly:

```python
from the_framework.agent import Artifact, Image

return (
    Image(path=screenshot),  # vision input, invisible in the chat
    Artifact(kind="widget", payload={"widget": "weather", "temperature": 18}),
)
```

Tools and commands can return an artifact directly or among tuple outputs. The
worker commits and emits the same item; session pages and archive retention supply
replay after reconnect, restart and compaction. A repeated artifact id is an
idempotent publication while retained in the archive. Its developer-message API
projection includes the id, kind and payload, confirming canonical presence only,
never successful client rendering. It does not send image pixels to the model.
Use `Image` separately when vision is required.

`Image.from_source(source, directory=..., description=...)` prepares a local path,
raw image bytes, or an HTTP(S) URL as an immutable, content-addressed local file.
Remote images are downloaded with an explicit application User-Agent and image
Accept headers, redirects and a 30-second network timeout,
limited to 50 MiB, then validated as PNG/JPEG/WebP/GIF before an item is returned.
Failures occur before session injection. For example:

```python
image = Image.from_source(
    "https://example.org/reference.png",
    directory=agent.session_root / "images",
    description="Reference for the requested analysis",
)
agent.add_response_item(image)
```

This factory performs blocking I/O; async tools should call it with
`await asyncio.to_thread(...)`. Ordinary `Image(path=...)` remains a reference to
an already-local file. Deserialization and API projection never download: the
projection reads the frozen file and emits a base64 `data:` image, never a remote
URL. The persisted item retains `path` as its image reference and `source_url`
as provenance for HTTP(S) inputs; the latter is never used by projection or
included in API input. Replays therefore do not depend on the source URL
remaining accessible. Source URLs may contain sensitive query parameters: use
an appropriate public source when that provenance should be retained.

Providers can return the same typed item, but **all provider outputs are ephemeral**.
On each model step, `agent.provider_artifacts.updated` replaces the current turn's
provider presentations. No provider output is appended to history or archive;
its API projection identifies its temporary scope. Clients discard these views
on turn completion, interruption, failure or connection loss. They are absent
from replay and renewed by a later provider sample. This supports a current
browser screenshot without filling the conversation with successive screenshots.

Add `ARTIFACTS` from `the_framework.plugins.artifacts` to the application's
plugins for local file publication. `artifacts.supported_artifacts` returns the
application-wide catalogue keyed by kind, with a description, `payload_schema`
(JSON Schema for the input to `show_artifact`) and `example`. Its instructions
recommend consulting this tool whenever the agent is unsure about a kind or
payload. The contract is shared by all clients; the agent does not negotiate
artifact types with the connected device. The default catalogue advertises only
the built-in `image` and `file` renderers.

Applications declare custom kinds with `Artifact` subclasses whose `payload`
field is a typed modict model. Register these classes explicitly in the
extension's `artifact_types` tuple; built-in kinds remain available automatically:

```python
from modict import modict
from the_framework.agent import Artifact
from the_framework.plugins.artifacts import ArtifactsExtension

class WeatherPayload(modict):
    _config = modict.config(extra="forbid")
    city: str
    temperature: float

class WeatherArtifact(Artifact):
    """Show a city's current temperature."""
    kind = "weather"
    payload: WeatherPayload = modict.field(required="always")
    payload_example = modict.attr({"city": "Paris", "temperature": 18.0})

class Artifacts(ArtifactsExtension):
    artifact_types = (WeatherArtifact,)
```

Use this subclass as the ARTIFACTS plugin's `agent_extension`, for example
`Plugin({**ARTIFACTS, "agent_extension": Artifacts})`. The class docstring gives
the description; `payload_example` is optional non-payload metadata. The same
model provides the catalogue's JSON Schema and validates `show_artifact` inputs.
Requirement projection follows `modict.config(default_required=...)` when a
field's `required` is omitted (`MISSING`). An explicit field setting overrides
that default, including `required="never"` under `default_required="always"`.
Framework `Base` models use `default_required="never"`; the explicit
`required="always"` above is intentional: the payload must remain present even
after construction. Standalone modict payload models inherit `"at_init"` unless
configured otherwise. Defaults/factories do not require an input property.

Python validators remain executable validation, not inferred JSON Schema rules;
document those extra constraints in the description. No duplicate schema is
needed. Tools and providers may also return `WeatherArtifact(payload=...)`
directly. Custom subclasses keep the canonical item `type="artifact"`; replay
preserves their kind and JSON payload without needing the declaration loaded.

The application must implement a matching `weather` renderer in its UI;
declaring a kind does not install a renderer. Catalogue queries return independent
JSON values and never mutate application declarations. Built-in image/file
schemas describe local publication inputs (`path`); stored artifacts instead
reference the published `src`.

The optional `artifacts.show_artifact` tool
accepts `kind="image"` or `"file"` and `payload={"path": "/local/result.png",
"title": "Result", "description": "..."}`. It copies the bytes atomically below
`data_root/bucket` and returns an artifact with `payload.src`, `name`, `size`
and `mime_type`; deleting or overwriting the source cannot change this publication.
Only references and JSON metadata enter the session; neither original nor copied
file bytes are embedded there as base64. The copy lives separately in the served
files. If it disappears, the artifact remains in history and the UI reports the
content as unavailable. Vision inputs are encoded only for the outgoing model
request; that transient API representation is never written back to the session.

`show_artifact` accepts open JSON payloads and explicitly disables strict
function schemas. Function tools otherwise default to strict mode; a tool
requiring arbitrary JSON keys can declare `strict: false` in its YAML docstring
metadata. The generated declaration preserves that choice instead of closing
an open object and losing application-defined data.

The asset route requires authentication and `session:read`; filenames are not
credentials. Assets are bytes, with no second conversation or replay registry.
Image publication validates raster PNG/JPEG/WebP/GIF. Downloads cannot execute
supplied HTML in the application's origin. Assets remain available when the
plugin's agent binding is disabled, and are not automatically deleted on reset.

An extension producing a file can use the same service without a second model
call or an explicit display tool invocation:

```python
payload = await self.agent.bridge.call(
    "artifacts", "publish", {"kind": "image", "payload": {"path": result_path}},
)
return Artifact(kind="image", payload=payload)
```

Returning an artifact with already usable JSON or durable URLs needs no service.
Providers should reference their own current resources; presentation lifetime does
not imply deletion of referenced bytes. File asset cleanup is independent of
session replay. Avoid temporary paths and expiring URLs for durable artifacts.

Both UIs render `image` (`payload.src`, optional `alt`, `title`, `description`) and
`file` (`src`, `name`, optional `title`, `description`). Unknown kinds show expandable
JSON; a failing custom renderer falls back without breaking chat. Widgets are
serializable descriptions, interpreted by application-owned React components.
The Harness exposes `artifactRenderers={{weather: Weather}}` on `AssistantApp`
and `Conversation`; each renderer receives `{kind, payload}`. The starter's `App`
has the same prop for local composition. No supplied HTML or JavaScript is executed.

---

[Reference index](README.md) · [Documentation paths](../README.md)
