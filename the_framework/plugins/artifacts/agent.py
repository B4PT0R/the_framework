from copy import deepcopy

from ...agent import AgentExtension, Artifact, tool
from ...utils.model_schema import model_schema


_FILE_PAYLOAD_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "minLength": 1,
                 "description": "Path to an existing local file on the server; copied into the served bucket."},
        "title": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["path"],
    "additionalProperties": True,
}


_BUILTIN_ARTIFACTS = {
    "image": {
        "description": "Display a local PNG, JPEG, WebP or GIF image in the chat.",
        "payload_schema": {
            **_FILE_PAYLOAD_SCHEMA,
            "properties": {**_FILE_PAYLOAD_SCHEMA["properties"], "alt": {"type": "string"}},
        },
        "example": {"path": "/tmp/chart.png", "title": "Chart", "alt": "Quarterly sales"},
    },
    "file": {
        "description": "Offer an existing local file as a download in the chat.",
        "payload_schema": _FILE_PAYLOAD_SCHEMA,
        "example": {"path": "/tmp/report.pdf", "title": "Report"},
    },
}


class ArtifactsExtension(AgentExtension):
    name = "artifacts"
    description = "Show images, files and application-defined widgets in the conversation."
    # Explicit application-wide declarations, never discovered from connected clients.
    artifact_types: tuple[type[Artifact], ...] = ()
    instructions = (
        "Use show_artifact to present content in the chat. Consult supported_artifacts "
        "whenever unsure which kinds are supported or how to compose their payloads. "
        "Its catalogue is the application's contract for every client; do not adapt "
        "artifact choices to the connected client or invent widget payloads. "
        "For image/file, pass "
        "payload.path pointing to an existing local file; it is copied to durable "
        "storage. Optional payload.title and payload.description label it. For "
        "widgets, pass a JSON description supported by the application's renderer. "
        "Unknown kinds remain readable as JSON. Artifacts request presentation; "
        "they do not prove the user has seen it. Image items and browser screenshots "
        "are vision inputs only: they do not display anything in the chat. To both "
        "inspect and present an image, use a vision tool and show_artifact separately."
    )

    @tool
    def supported_artifacts(self):
        """List supported artifact kinds, their show_artifact payload schemas and examples.

        This catalogue applies to every client. Consult it whenever unsure about
        a kind or payload structure. Examples describe inputs, not existing files.
        """
        catalogue = deepcopy(_BUILTIN_ARTIFACTS)
        for kind, model in self._artifact_models().items():
            entry = {
                "description": (model.__doc__ or "").strip(),
                "payload_schema": model_schema(model)["properties"]["payload"],
            }
            example = getattr(model, "payload_example", None)
            if example is not None:
                entry["example"] = deepcopy(example)
            catalogue[kind] = entry
        return catalogue

    def _artifact_models(self):
        models = {}
        for model in self.artifact_types:
            if not isinstance(model, type) or not issubclass(model, Artifact):
                raise TypeError("artifact_types must contain Artifact subclasses")
            kind = model.__fields__["kind"].default
            if not isinstance(kind, str) or not kind.strip():
                raise ValueError(f"{model.__name__} must declare a non-empty kind default")
            if kind in _BUILTIN_ARTIFACTS or kind in models:
                raise ValueError(f"duplicate artifact kind: {kind}")
            models[kind] = model
        return models

    @tool
    async def show_artifact(self, kind: str, payload: dict):
        """
        description: Present a durable artifact; image/file with payload.path, or custom JSON.
        strict: false
        """
        if kind in {"image", "file"}:
            payload = await self.agent.bridge.call(
                "artifacts", "publish", {"kind": kind, "payload": payload},
                timeout_ms=120_000,
            )
        model = self._artifact_models().get(kind, Artifact)
        return model(kind=kind, payload=payload)
