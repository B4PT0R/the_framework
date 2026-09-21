"""Optional live memory retrieval, owned independently from voice transport."""

import asyncio

from ...agent.runtime.protocol import (
    CommandCompleted,
    CommandEvent,
    ExternalEventRequest,
    SessionSnapshotRequest,
)
from core.utils.ids import timestamp_id


class MemoryLiveContext:
    recoverable_context_prefixes = ("memory:append:",)

    def __init__(self, controller):
        self.controller = controller
        self.memory_candidates = ()
        self.memory_context_ids = set()
        self.memory_append_lock = asyncio.Lock()
        self.memory_append_tasks = set()
        self.pending_memory_appends = 0
        self.memory_cache_refresh_task = None
        self.memory_cache_refresh_pending = False

    def start(self, projection):
        self.memory_candidates = tuple(
            projection.get("memory_candidates") or ()
        )
        self.memory_context_ids = {
            str(candidate.get("id"))
            for candidate in self.memory_candidates
            if candidate.get("id")
        }

    async def handle_worker_output(self, output):
        command_id = getattr(output, "command_id", None)
        command = getattr(output, "command", None)
        if command_id is None and command is not None:
            command_id = command.id
        if (
            isinstance(output, CommandCompleted)
            and isinstance(command, ExternalEventRequest)
            and command.name == "realtime_turn"
        ):
            if command.payload.get("role") == "user":
                self._schedule_memory_append()
            self._schedule_memory_cache_refresh()
        delegation = self.controller.delegations.get(command_id)
        if delegation is None:
            return
        if isinstance(output, CommandEvent):
            event = output.event
            if event.get("type") == "agent.response_item.added":
                item = event.get("item") or {}
                if item.get("role") == "user" and not delegation["context_seeded"]:
                    delegation["context_seeded"] = True
                    self._schedule_memory_append()
                    self._schedule_memory_cache_refresh()
        elif isinstance(output, CommandCompleted):
            self._schedule_memory_cache_refresh()

    async def close(self):
        self.memory_candidates = ()
        self.memory_context_ids.clear()
        self.pending_memory_appends = 0
        self.memory_cache_refresh_pending = False
        tasks = set(self.memory_append_tasks)
        if self.memory_cache_refresh_task is not None:
            tasks.add(self.memory_cache_refresh_task)
        self.memory_cache_refresh_task = None
        self.memory_append_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _schedule_memory_append(self):
        if self.controller.sideband is None or self.controller.call_id is None:
            return
        if self.controller._vocal_provider_context_blocked():
            self.pending_memory_appends += 1
            return
        task = asyncio.create_task(self._append_next_memory_safely())
        self.memory_append_tasks.add(task)
        task.add_done_callback(self.memory_append_tasks.discard)

    def _flush_pending_memory_appends(self):
        if self.controller._vocal_provider_context_blocked() or not self.pending_memory_appends:
            return
        count = self.pending_memory_appends
        self.pending_memory_appends = 0
        for _ in range(count):
            self._schedule_memory_append()

    async def _append_next_memory_safely(self):
        try:
            return await self._append_next_memory()
        except Exception:
            return False

    async def _append_next_memory(self):
        async with self.memory_append_lock:
            if self.controller.sideband is None or self.controller.call_id is None:
                return False
            try:
                candidate = next(
                    (
                        value for value in self.memory_candidates
                        if value.get("id")
                        and str(value["id"]) not in self.memory_context_ids
                        and isinstance(value.get("text"), str)
                        and value["text"].strip()
                    ),
                    None,
                )
                if candidate is None:
                    return False
                memory_id = str(candidate["id"])
                sent = await self.controller._send_ephemeral_context(
                    candidate["text"],
                    event_id=f"memory:append:{memory_id}:{timestamp_id()}",
                )
                if sent:
                    self.memory_context_ids.add(memory_id)
                return sent
            except (KeyError, TypeError, ValueError, OSError, RuntimeError):
                return False

    def _schedule_memory_cache_refresh(self):
        """Refresh the RAG ranking off the latency-sensitive live path."""
        if self.controller.sideband is None or self.controller.call_id is None:
            return
        task = self.memory_cache_refresh_task
        if task is not None and not task.done():
            self.memory_cache_refresh_pending = True
            return
        self.memory_cache_refresh_pending = False
        task = asyncio.create_task(self._refresh_memory_cache_loop())
        self.memory_cache_refresh_task = task
        task.add_done_callback(self._memory_cache_refresh_finished)

    def _memory_cache_refresh_finished(self, task):
        if self.memory_cache_refresh_task is task:
            self.memory_cache_refresh_task = None
        if (
            self.memory_cache_refresh_pending
            and self.controller.sideband is not None
            and self.controller.call_id is not None
        ):
            self.memory_cache_refresh_pending = False
            self._schedule_memory_cache_refresh()

    async def _refresh_memory_cache_loop(self):
        while self.controller.sideband is not None and self.controller.call_id is not None:
            self.memory_cache_refresh_pending = False
            try:
                snapshot = await self.controller.harness.request(
                    SessionSnapshotRequest(id=timestamp_id())
                )
                extension = snapshot.extensions.get("realtime") or {}
                candidates = extension.get("memory_candidates") or ()
                self.memory_candidates = tuple(
                    candidate
                    for candidate in candidates
                    if isinstance(candidate, dict)
                    and candidate.get("id")
                    and isinstance(candidate.get("text"), str)
                    and candidate["text"].strip()
                )
            except (KeyError, TypeError, ValueError, OSError, RuntimeError):
                pass
            # Let message commits that happened while the snapshot was being
            # built request exactly one follow-up refresh.
            await asyncio.sleep(0)
            if not self.memory_cache_refresh_pending:
                return
