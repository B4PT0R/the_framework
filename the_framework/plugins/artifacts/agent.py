from ...agent import AgentExtension, Artifact, tool


class ArtifactsExtension(AgentExtension):
    name = "artifacts"
    description = "Show images, files and application-defined widgets in the conversation."
    instructions = (
        "Use show_artifact to present content in the chat. For image/file, pass "
        "payload.path pointing to an existing local file; it is copied to durable "
        "storage. Optional payload.title and payload.description label it. For "
        "widgets, pass a JSON description supported by the application's renderer. "
        "Unknown kinds remain readable as JSON. Artifacts request presentation; "
        "they do not prove the user has seen it. Image items and browser screenshots "
        "are vision inputs only: they do not display anything in the chat. To both "
        "inspect and present an image, use a vision tool and show_artifact separately."
    )

    @tool
    async def show_artifact(self, kind: str, payload: dict):
        """Present a durable artifact: image/file with payload.path, or custom JSON."""
        if kind in {"image", "file"}:
            payload = await self.agent.bridge.call(
                "artifacts", "publish", {"kind": kind, "payload": payload},
                timeout_ms=120_000,
            )
        return Artifact(kind=kind, payload=payload)
