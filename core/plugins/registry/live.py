"""Optional live registry context; voice transport owns delivery barriers."""

import json

from ...agent.runtime.protocol import CommandEvent
from core.utils.ids import timestamp_id


class RegistryLiveContext:
    def __init__(self, controller):
        self.controller = controller
        self.reset()

    def reset(self):
        self.revision = 0
        self.pending = []

    async def handle_worker_output(self, output):
        if isinstance(output, CommandEvent):
            event = output.event
            if event.get("type") == "agent.response_item.added":
                delta = (event.get("item") or {}).get("registry_delta")
                if delta is not None:
                    await self.accept(delta)

    @staticmethod
    def _update_text(delta):
        encoded = json.dumps(
            delta,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
        return (
            '<context_provider_update name="persistent_registry">\n'
            "This is a technical state update, not a human utterance. Apply these "
            "operations to the persistent_registry snapshot already in context.\n"
            f"{encoded}\n"
            "</context_provider_update>"
        )

    async def accept(self, delta):
        if self.controller.sideband is None or self.controller.call_id is None or not isinstance(delta, dict):
            return False
        revision = delta.get("revision")
        operations = delta.get("operations")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
            return False
        if not isinstance(operations, list) or not operations:
            return False
        if revision <= self.revision:
            return False
        if any(item.get("revision") == revision for item in self.pending):
            return False
        if self.controller._vocal_provider_context_blocked():
            self.pending.append(delta)
            return True
        return await self._append(delta)

    async def _append(self, delta):
        revision = delta["revision"]
        if revision <= self.revision:
            return False
        sent = await self.controller._send_ephemeral_context(
            self._update_text(delta),
            event_id=f"registry:update:{revision}:{timestamp_id()}",
        )
        if sent:
            self.revision = revision
        return sent

    async def flush(self):
        if self.controller._vocal_provider_context_blocked() or not self.pending:
            return
        pending = self.pending
        self.pending = []
        for delta in pending:
            await self._append(delta)
