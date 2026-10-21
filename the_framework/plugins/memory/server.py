"""Inline read-only consultations using the session's existing specialist fleet."""

import asyncio
from html import escape

from ...agent.context.session import SessionWatermark
from ...utils.ids import timestamp_id


class MemoryConsultations:
    def __init__(self, runtime):
        self.runtime = runtime
        self.pending = {}

    async def handle(self, method, payload):
        if method != "ask":
            raise ValueError(f"unknown memory operation: {method}")
        request_id = timestamp_id()
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            accepted = await self.runtime.fleet.submit(
                "memory.consultation",
                prompt=(
                    f'<memory_question>{escape(payload["question"])}</memory_question>\n'
                    + payload["memories"]
                ),
                watermark=SessionWatermark(**payload["watermark"]),
                observation_type="memory.consultation",
                metadata={"memory_consultation_id": request_id},
            )
            if not accepted:
                raise RuntimeError("memory consultation specialist is unavailable or full")
            result = await asyncio.wait_for(future, timeout=170)
            if result.status != "completed" or not result.output:
                raise RuntimeError(result.error or "memory consultation returned no answer")
            return {"status": "answered", "question": payload["question"],
                    "answer": result.output, "request_id": request_id}
        finally:
            self.pending.pop(request_id, None)

    def receive(self, result):
        if result.profile != "memory.consultation":
            return
        request_id = result.metadata.get("memory_consultation_id")
        future = self.pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(result)
        return True  # The answer belongs to the inline tool, including late results.

    async def stop(self):
        self.runtime.unregister_fleet_result_handler(self.receive)
        for future in self.pending.values():
            if not future.done():
                future.cancel()


def build_service(context, runtime):
    service = MemoryConsultations(runtime)
    runtime.bridge.register("memory", service.handle)
    runtime.register_fleet_result_handler(service.receive)
    return service
