import asyncio
import hashlib
import logging
from collections import Counter, deque
from html import escape

from codex_backend_sdk import OpenAI

from ...agent.runtime.protocol import (
    CommandCompleted,
    CommandEvent,
    CommandFailed,
    ExternalEventRequest,
    PromptRequest,
)
from ...utils.tokens import token_count
from ...agent.models.base import TypedBase
from core.utils.ids import timestamp_id

logger = logging.getLogger(__name__)

LIVE_APPEND_TOKEN_LIMIT = 480
LIVE_APPEND_TOKEN_MODELS = ("gpt-live-1-codex", "gpt-5.4")


class RealtimeEvent(TypedBase):
    call_id: str
    event: dict


class RealtimeDelegationUpdate(TypedBase):
    call_id: str
    delegation_id: str
    command_id: str
    state: str


class RealtimeController:
    """Own one ephemeral Codex Live transport for the application."""

    recoverable_context_prefixes = ()

    def __init__(self, harness, client_factory=None):
        self.harness = harness
        self.client_factory = client_factory or self._authenticated_client
        self.client = None
        self.prepare_task = None
        self.call_id = None
        self.sideband = None
        self.sideband_manager = None
        self.peer_ready = False
        self.reader_task = None
        self.lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.delegations = {}
        self.pending_delegation_deliveries = set()
        self.pending_provider_contexts = []
        self.typed_inputs = Counter()
        self.ephemeral_context_inputs = Counter()
        self.ephemeral_context_order = deque()
        self.barriers = {}
        self.closing = False
        self.skip_user_turns = 0
        self.skip_assistant_turns = 0
        self._realtime_response_active = False
        self.response_started_count = 0
        self.response_state_changed = asyncio.Event()
        self.pending_restart_resumes = []
        self.restart_resume_input_paused = False
        self.bind_restart_resume = None
        harness.register_observer(self.handle_worker_output)

    def start_context(self, projection):
        """Initialize application context after the live transport is connected."""

    async def ready_context(self):
        """Seed application context once the client can receive it."""

    async def flush_context(self):
        """Release application updates after response/delegation barriers."""

    async def worker_context(self, output):
        """Offer committed worker output to optional context producers."""

    async def close_context(self):
        """Stop application context producers before releasing the transport."""

    @staticmethod
    def _authenticated_client():
        return OpenAI().authenticate(interactive=False)

    @property
    def active(self):
        return self.call_id is not None or self.closing

    @property
    def realtime_response_active(self):
        return self._realtime_response_active

    @realtime_response_active.setter
    def realtime_response_active(self, active):
        active = bool(active)
        if active and not self._realtime_response_active:
            self.response_started_count += 1
        if active != self._realtime_response_active:
            self._realtime_response_active = active
            self.response_state_changed.set()

    async def wait_for_voice_turn_completion(
        self,
        *,
        activation_timeout=5,
        completion_timeout=120,
    ):
        baseline = self.response_started_count
        observed = self.realtime_response_active
        if not observed:
            deadline = asyncio.get_running_loop().time() + activation_timeout
            while self.response_started_count == baseline and not self.realtime_response_active:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return False
                self.response_state_changed.clear()
                try:
                    await asyncio.wait_for(
                        self.response_state_changed.wait(),
                        timeout=remaining,
                    )
                except TimeoutError:
                    return False
            observed = True
        if observed and self.realtime_response_active:
            deadline = asyncio.get_running_loop().time() + completion_timeout
            while self.realtime_response_active:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    logger.warning(
                        "Realtime voice turn did not finish before restart timeout"
                    )
                    return False
                self.response_state_changed.clear()
                try:
                    await asyncio.wait_for(
                        self.response_state_changed.wait(),
                        timeout=remaining,
                    )
                except TimeoutError:
                    logger.warning(
                        "Realtime voice turn did not finish before restart timeout"
                    )
                    return False
        return observed

    def prewarm(self):
        if self.client is None and self.prepare_task is None:
            self.prepare_task = asyncio.create_task(
                asyncio.to_thread(self.client_factory)
            )
            self.prepare_task.add_done_callback(self._prepared)

    def _prepared(self, task):
        if self.prepare_task is task:
            self.prepare_task = None
        if task.cancelled():
            return
        try:
            self.client = task.result()
        except Exception:
            pass

    async def _client(self):
        if self.client is not None:
            return self.client
        self.prewarm()
        task = self.prepare_task
        self.client = await task
        return self.client

    async def start(self, offer_sdp, snapshot):
        async with self.lock:
            if self.active:
                raise RuntimeError("a realtime call is already active")
            extension = snapshot.extensions.get("realtime")
            if not extension:
                raise RuntimeError("the realtime plugin is not active")
            if extension.get("protocol") != "codex-realtime-v3":
                raise RuntimeError("unsupported realtime projection protocol")
            try:
                client = await self._client()
                live = await asyncio.to_thread(
                    client.live.create,
                    session=extension["session"],
                    transport={"type": "webrtc", "sdp": offer_sdp},
                )
                self.call_id = live.session.id
                self.sideband_manager = client.live.sideband.connect(
                    session_id=live.session.id,
                )
                self.sideband = await asyncio.to_thread(self.sideband_manager.enter)
                self.peer_ready = False
                self.reader_task = asyncio.create_task(self._read_sideband())
                self.pending_delegation_deliveries.clear()
                self.pending_provider_contexts.clear()
                self.start_context(extension)
                return {
                    "call_id": live.session.id,
                    "answer_sdp": live.transport.sdp,
                    "watermark": snapshot.watermark,
                    "model": extension["model"],
                }
            except Exception as error:
                await self._close()
                if isinstance(error, RuntimeError):
                    raise
                raise RuntimeError(
                    f"Realtime call creation rejected by the backend: {error}"
                ) from error

    async def _read_sideband(self):
        try:
            while self.sideband is not None:
                live_event = await asyncio.to_thread(self.sideband.recv)
                event = self._live_event_payload(live_event)
                handled = await self._handle_sideband_event(event)
                if not handled:
                    await self.harness.publish(RealtimeEvent(
                        call_id=self.call_id,
                        event=event,
                    ))
                if event.get("type") == "error" and not handled:
                    return
        except Exception as error:
            if self.sideband is not None:
                await self.harness.publish(RealtimeEvent(
                    call_id=self.call_id,
                    event={"type": "error", "error": str(error)},
                ))
        finally:
            if self.reader_task is asyncio.current_task():
                async with self.lock:
                    if self.reader_task is asyncio.current_task():
                        await self._close()

    @staticmethod
    def _live_event_payload(event):
        if isinstance(event, dict):
            return event
        to_dict = getattr(event, "to_dict", None)
        if not callable(to_dict):
            raise TypeError("Expected a Live sideband event object")
        payload = to_dict(mode="json", exclude_unset=False)
        if not isinstance(payload, dict):
            raise TypeError("Expected a Live sideband event to serialize to an object")
        return payload

    @staticmethod
    async def _release_sideband(sideband, manager):
        if manager is not None:
            await asyncio.to_thread(manager.__exit__, None, None, None)
        elif sideband is not None:
            await asyncio.to_thread(sideband.close)

    async def _handle_sideband_event(self, event):
        event_type = event.get("type")
        if event_type in {"response.created", "response.in_progress"}:
            self.realtime_response_active = True
        elif event_type in {
            "response.done", "response.failed", "response.cancelled",
            "response.incomplete",
        }:
            self.realtime_response_active = False
            await self._flush_pending_provider_contexts()
            await self.flush_context()
            await self._resume_restart_input(event_type)
        if event_type == "error":
            error = event.get("error") or {}
            event_id = str(error.get("event_id") or event.get("event_id") or "")
            if event_id.startswith(self.recoverable_context_prefixes):
                logger.warning("Recoverable Realtime context append error: %s", error)
                return True
        if event_type == "delegation.created":
            await self._start_delegation(event)
            return
        if event_type != "turn.done":
            return False
        turn = event.get("turn") or {}
        role = turn.get("role")
        transcript = str(turn.get("transcript") or "").strip()
        if not transcript or role not in {"user", "assistant"}:
            return False
        if self._consume_ephemeral_context(transcript):
            # Provider and application context is useful only inside this live
            # projection. It must never become canonical conversation merely
            # because the Realtime backend reports an appended item as a turn.
            return False
        if role == "user":
            # Server VAD may begin the answer before the canonical worker has
            # acknowledged this transcript. Consider that response active now
            # so provider replacement cannot race its first audio tokens.
            self.realtime_response_active = True
        else:
            self.realtime_response_active = False
            # A speakable delegation result is complete only once its assistant
            # turn has actually finished. Until then, no dynamic provider may
            # alter the vocal model's live context.
            self.pending_delegation_deliveries.clear()
            await self._flush_pending_provider_contexts()
            await self.flush_context()
            # Delegated speech can finish with turn.done without a response.*
            # terminal event. This is the authoritative end of audible output.
            await self._resume_restart_input("assistant_turn_done")
        if role == "user" and self.typed_inputs[transcript]:
            self.typed_inputs[transcript] -= 1
            return False
        if role == "user" and self.skip_user_turns:
            self.skip_user_turns -= 1
            return False
        if role == "assistant" and self.skip_assistant_turns:
            self.skip_assistant_turns -= 1
            return False
        turn_id = str(turn.get("id") or self._content_id(role, transcript))
        await self.harness.supervisor.send(ExternalEventRequest(
            id=f"realtime:{self.call_id}:{turn_id}:{role}",
            name="realtime_turn",
            payload={
                "call_id": self.call_id,
                "turn_id": turn_id,
                "role": role,
                "transcript": transcript,
                "interrupted": bool(turn.get("interrupted", False)),
            },
        ))
        return False

    async def _start_delegation(self, event):
        item = event.get("item") or {}
        if item.get("type") != "delegation" or item.get("target") != "client":
            return
        delegation_id = str(item.get("id") or "")
        transcript = "".join(
            str(content.get("text") or "")
            for content in item.get("content") or []
            if isinstance(content, dict) and content.get("type") == "input_text"
        ).strip()
        if not delegation_id or not transcript:
            return
        command_id = f"realtime-delegation:{self.call_id}:{delegation_id}"
        restart_resume = next(
            (item for item in self.pending_restart_resumes if item.get("injected")),
            None,
        )
        if restart_resume is not None:
            self.pending_restart_resumes.remove(restart_resume)
            if self.bind_restart_resume is not None:
                bound = self.bind_restart_resume(restart_resume["action_id"], command_id)
                if asyncio.iscoroutine(bound):
                    await bound
        self.skip_user_turns += 1
        append_prompt = not bool(self.typed_inputs[transcript])
        if not append_prompt:
            self.typed_inputs[transcript] -= 1
        self.delegations[command_id] = {
            "id": delegation_id,
            "command_id": command_id,
            "fallback": "",
            "final": "",
            "items": {},
            "context_seeded": not append_prompt,
        }
        await self.harness.supervisor.send(PromptRequest(
            id=command_id,
            prompt=transcript,
            append_prompt=append_prompt,
            reasoning_effort="none",
        ))
        await self.harness.publish(RealtimeDelegationUpdate(
            call_id=self.call_id,
            delegation_id=delegation_id,
            command_id=command_id,
            state="started",
        ))

    async def handle_worker_output(self, output):
        await self.worker_context(output)

        command_id = getattr(output, "command_id", None)
        if command_id is None and hasattr(output, "command"):
            command_id = output.command.id
        barrier = self.barriers.get(command_id)
        if barrier is not None and isinstance(output, (CommandCompleted, CommandFailed)):
            if not barrier.done():
                if isinstance(output, CommandFailed):
                    barrier.set_exception(RuntimeError(output.error))
                else:
                    barrier.set_result(None)
            return
        delegation = self.delegations.get(command_id)
        if delegation is None:
            return
        if isinstance(output, CommandEvent):
            event = output.event
            if event.get("type") == "agent.response_item.added":
                return
            if event.get("type") == "agent.tool_call.start":
                feedback = str(event.get("user_feedback") or "").strip()
                if feedback:
                    await self._send_delegation(
                        delegation["id"],
                        feedback,
                        channel="commentary",
                    )
                    await self.harness.publish(RealtimeDelegationUpdate(
                        call_id=self.call_id,
                        delegation_id=delegation["id"],
                        command_id=command_id,
                        state="progress",
                    ))
                return
            if event.get("type") == "response.output_item.added":
                item = event.get("item") or {}
                if item.get("type") == "message" and item.get("role") == "assistant":
                    delegation["items"][str(item.get("id") or "")] = {
                        "phase": item.get("phase"),
                        "text": "",
                    }
                return
            if event.get("type") == "response.output_text.delta":
                delta = str(event.get("delta") or "")
                item = delegation["items"].get(str(event.get("item_id") or ""))
                if item is None:
                    delegation["fallback"] += delta
                elif item["phase"] == "commentary":
                    item["text"] += delta
                    if len(item["text"].encode("utf-8")) >= 240:
                        await self._flush_progress(delegation, item)
                elif item["phase"] == "final_answer":
                    delegation["final"] += delta
                else:
                    delegation["fallback"] += delta
                return
            if event.get("type") == "response.output_text.done":
                item = delegation["items"].get(str(event.get("item_id") or ""))
                if item is not None and item["phase"] == "commentary":
                    await self._flush_progress(delegation, item)
            return
        if isinstance(output, CommandCompleted):
            for item in delegation["items"].values():
                if item["phase"] == "commentary":
                    await self._flush_progress(delegation, item)
            answer = (delegation["final"] or delegation["fallback"]).strip()
            if not answer:
                answer = "La tâche est terminée, sans réponse textuelle."
            self.skip_assistant_turns += 1
            self.pending_delegation_deliveries.add(delegation["id"])
            await self._send_delegation(delegation["id"], answer)
            await self.harness.publish(RealtimeDelegationUpdate(
                call_id=self.call_id,
                delegation_id=delegation["id"],
                command_id=command_id,
                state="completed",
            ))
            self.delegations.pop(command_id, None)
            return
        if isinstance(output, CommandFailed):
            self.skip_assistant_turns += 1
            self.pending_delegation_deliveries.add(delegation["id"])
            await self._send_delegation(
                delegation["id"],
                f"Je n’ai pas pu terminer cette demande : {output.error}",
            )
            await self.harness.publish(RealtimeDelegationUpdate(
                call_id=self.call_id,
                delegation_id=delegation["id"],
                command_id=command_id,
                state="failed",
            ))
            self.delegations.pop(command_id, None)

    async def _flush_progress(self, delegation, item):
        text = item["text"].strip()
        item["text"] = ""
        if not text:
            return
        await self._send_delegation(delegation["id"], text, channel="commentary")
        await self.harness.publish(RealtimeDelegationUpdate(
            call_id=self.call_id,
            delegation_id=delegation["id"],
            command_id=delegation["command_id"],
            state="progress",
        ))

    async def _send_delegation(self, delegation_id, text, *, channel="speakable"):
        await self._send_context_appends(
            text,
            delegation_id=delegation_id,
            channel=channel,
        )

    async def ready(self):
        async with self.lock:
            if self.sideband is None or self.call_id is None:
                raise RuntimeError("the realtime call is not accepting readiness")
            pending = any(not item["injected"] for item in self.pending_restart_resumes)
            if pending:
                paused = await self._send({"type": "input_audio.pause"})
                self.restart_resume_input_paused = paused
                logger.info("Realtime restart input pause sent=%s", paused)

            first_ready = not self.peer_ready
            self.peer_ready = True
            if first_ready:
                # A connected sideband is not sufficient evidence that the
                # WebRTC peer can consume appended context. Seed and release
                # queued provider updates only after the owning client has
                # explicitly completed its peer connection.
                await self.ready_context()
                await self._flush_pending_provider_contexts()
                await self.flush_context()

            if not pending:
                return {"status": "ready", "resume_injected": False}
            injected = await self._flush_restart_resumes()
            if not injected:
                await self._resume_restart_input("resume_not_injected")
            return {"status": "ready", "resume_injected": injected}

    async def _resume_restart_input(self, trigger="unknown"):
        if not self.restart_resume_input_paused:
            return False
        sent = await self._send({"type": "input_audio.resume"})
        if sent:
            self.restart_resume_input_paused = False
        logger.info(
            "Realtime restart input resume trigger=%s sent=%s",
            trigger,
            sent,
        )
        return sent

    async def queue_restart_resume(self, intent):
        action_id = str(intent.get("action_id") or "").strip()
        instruction = str(intent.get("resume_instruction") or "").strip()
        if not action_id or not instruction:
            raise ValueError("invalid Realtime restart resume intent")
        if any(item["action_id"] == action_id for item in self.pending_restart_resumes):
            return
        self.pending_restart_resumes.append({
            "action_id": action_id,
            "instruction": instruction,
            "injected": False,
        })
        await self._flush_restart_resumes()

    async def _flush_restart_resumes(self):
        if self.sideband is None or self.call_id is None:
            return False
        resume_injected = False
        for item in self.pending_restart_resumes:
            if item["injected"]:
                continue
            body = (
                f'<server_restart_resume action_id="{escape(item["action_id"])}">\n'
                "A delegated agentic task was interrupted by the supervised server restart. "
                "Do not perform or answer the task yourself. Create a client delegation now "
                "whose instruction resumes the task below, then relay its progress and result "
                "normally through this Realtime conversation. Take the conversational initiative: "
                "do not wait for another user message before creating the delegation or speaking "
                "again when useful.\n"
                f'<instruction>{escape(item["instruction"])}</instruction>\n'
                "</server_restart_resume>"
            )
            item["injected"] = True
            sent = await self._send_context_appends(
                body,
                event_id=f"restart:resume:{item['action_id']}:{timestamp_id()}",
                ephemeral=True,
            )
            if not sent:
                item["injected"] = False
                return resume_injected
            resume_injected = True
        return resume_injected

    async def send_text(self, text):
        text = text.strip()
        if not text:
            raise ValueError("text is required")
        if self.sideband is None or self.call_id is None:
            raise RuntimeError("the realtime call is not accepting input")
        turn_id = timestamp_id()
        self.realtime_response_active = True
        self.typed_inputs[text] += 1
        await self.harness.supervisor.send(ExternalEventRequest(
            id=f"realtime:{self.call_id}:typed-{turn_id}:user",
            name="realtime_turn",
            payload={
                "call_id": self.call_id,
                "turn_id": f"typed-{turn_id}",
                "role": "user",
                "transcript": text,
                "typed": True,
            },
        ))
        await self._send({
            "type": "session.context.append",
            "content": [
                {"type": "input_text", "text": chunk}
                for chunk in self._live_append_chunks(text)
            ],
        })
        return turn_id

    @staticmethod
    def _context_digest(text):
        return hashlib.sha256(str(text).strip().encode("utf-8")).hexdigest()

    def _remember_ephemeral_context(self, text):
        digest = self._context_digest(text)
        self.ephemeral_context_inputs[digest] += 1
        self.ephemeral_context_order.append(digest)
        while len(self.ephemeral_context_order) > 256:
            expired = self.ephemeral_context_order.popleft()
            self.ephemeral_context_inputs[expired] -= 1
            if self.ephemeral_context_inputs[expired] <= 0:
                del self.ephemeral_context_inputs[expired]
        return digest

    def _forget_ephemeral_context(self, digest):
        if not self.ephemeral_context_inputs[digest]:
            return
        self.ephemeral_context_inputs[digest] -= 1
        if self.ephemeral_context_inputs[digest] <= 0:
            del self.ephemeral_context_inputs[digest]
        try:
            self.ephemeral_context_order.remove(digest)
        except ValueError:
            pass

    def _consume_ephemeral_context(self, text):
        digest = self._context_digest(text)
        if not self.ephemeral_context_inputs[digest]:
            return False
        self._forget_ephemeral_context(digest)
        return True

    async def _send_ephemeral_context(self, text, *, event_id=None):
        """Append live-only context while preventing canonical reconciliation."""
        if not self.peer_ready or self._vocal_provider_injections_suspended():
            # Final race guard: a provider task may have passed its semantic
            # gate just before delegation.created was received. Preserve that
            # update for later instead of letting it interleave with delegation
            # sideband traffic.
            self.pending_provider_contexts.append((text, event_id))
            del self.pending_provider_contexts[:-32]
            return True
        return await self._send_context_appends(
            text,
            event_id=event_id,
            ephemeral=True,
        )

    async def _send_context_appends(
        self,
        text,
        *,
        delegation_id=None,
        channel=None,
        event_id=None,
        ephemeral=False,
    ):
        """Send token-bounded Codex Live context updates in their original order."""
        chunks = self._live_append_chunks(text)
        if not chunks:
            return False
        base_event_id = event_id
        for index, chunk in enumerate(chunks, start=1):
            digest = self._remember_ephemeral_context(chunk) if ephemeral else None
            event = {
                "type": (
                    "delegation.context.append"
                    if delegation_id is not None
                    else "session.context.append"
                ),
                "content": [{"type": "input_text", "text": chunk}],
            }
            if base_event_id is not None:
                event["event_id"] = (
                    base_event_id
                    if len(chunks) == 1
                    else f"{base_event_id}:part:{index}-of-{len(chunks)}"
                )
            if delegation_id is not None:
                event.update({
                    "delegation_item_id": delegation_id,
                    "channel": channel,
                })
            sent = await self._send(event)
            if not sent:
                if digest is not None:
                    self._forget_ephemeral_context(digest)
                return False
        return True

    def _vocal_provider_injections_suspended(self):
        return bool(self.delegations or self.pending_delegation_deliveries)

    def _vocal_provider_context_blocked(self):
        return (
            not self.peer_ready
            or self.realtime_response_active
            or self._vocal_provider_injections_suspended()
        )

    async def _flush_pending_provider_contexts(self):
        if (
            self.realtime_response_active
            or self._vocal_provider_injections_suspended()
            or not self.pending_provider_contexts
        ):
            return False
        pending = self.pending_provider_contexts
        self.pending_provider_contexts = []
        sent = False
        for text, event_id in pending:
            sent = await self._send_ephemeral_context(
                text,
                event_id=event_id,
            ) or sent
        return sent

    async def _send(self, event):
        sideband = self.sideband
        if sideband is None:
            return False
        async with self.send_lock:
            await asyncio.to_thread(sideband.send, event)
        return True

    @staticmethod
    def _live_append_token_count(text):
        # The exact tokenizer of the backend-only Codex snapshot is not public.
        # Bound against both plausible OpenAI tokenizers and leave headroom
        # below the documented 500-token server limit.
        return max(
            token_count(text, model=model)
            for model in LIVE_APPEND_TOKEN_MODELS
        )

    @classmethod
    def _live_append_chunks(cls, text, limit=LIVE_APPEND_TOKEN_LIMIT):
        text = str(text or "")
        if not text:
            return []
        chunks = []
        remaining = text
        while cls._live_append_token_count(remaining) > limit:
            low, high = 1, len(remaining)
            while low < high:
                length = (low + high + 1) // 2
                if cls._live_append_token_count(remaining[:length]) <= limit:
                    low = length
                else:
                    high = length - 1
            split_at = low
            # Prefer a nearby semantic boundary without discarding whitespace.
            floor = max(1, split_at * 3 // 5)
            boundaries = (
                remaining.rfind("\n\n", floor, split_at),
                remaining.rfind("\n", floor, split_at),
                remaining.rfind(" ", floor, split_at),
            )
            boundary = max(boundaries)
            if boundary >= floor:
                split_at = boundary + 1
            chunk = remaining[:split_at]
            # Defensive guard against non-monotonic tokenizer edge cases.
            while len(chunk) > 1 and cls._live_append_token_count(chunk) > limit:
                chunk = chunk[:-1]
            chunks.append(chunk)
            remaining = remaining[len(chunk):]
        if remaining:
            chunks.append(remaining)
        return chunks

    @staticmethod
    def _content_id(role, transcript):
        digest = hashlib.sha256(f"{role}\0{transcript}".encode()).hexdigest()[:20]
        return f"content-{digest}"

    async def stop(self, *, barrier_timeout=5):
        async with self.lock:
            self.closing = True
            try:
                call_id = self.call_id
                if self.sideband is not None:
                    await self._send({"type": "session.close"})
                    if self.reader_task is not None:
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(self.reader_task),
                                timeout=1,
                            )
                        except TimeoutError:
                            pass
                if call_id is not None:
                    try:
                        await self._worker_barrier(
                            call_id,
                            timeout=barrier_timeout,
                        )
                    except TimeoutError:
                        logger.warning(
                            "Realtime worker barrier timed out during shutdown; "
                            "continuing transport cleanup"
                        )
            finally:
                try:
                    await self._close()
                finally:
                    self.closing = False

    async def _worker_barrier(self, call_id, *, timeout=5):
        command_id = f"realtime-barrier:{call_id}:{timestamp_id()}"
        barrier = asyncio.get_running_loop().create_future()
        self.barriers[command_id] = barrier
        try:
            await self.harness.supervisor.send(ExternalEventRequest(
                id=command_id,
                name="realtime_barrier",
                payload={"call_id": call_id},
            ))
            await asyncio.wait_for(barrier, timeout=timeout)
        finally:
            self.barriers.pop(command_id, None)

    async def _close(self):
        sideband, task = self.sideband, self.reader_task
        sideband_manager = self.sideband_manager
        self.sideband = None
        self.sideband_manager = None
        self.reader_task = None
        self.call_id = None
        self.peer_ready = False
        self.delegations.clear()
        self.pending_delegation_deliveries.clear()
        self.pending_provider_contexts.clear()
        self.typed_inputs.clear()
        self.ephemeral_context_inputs.clear()
        self.ephemeral_context_order.clear()
        self.skip_user_turns = 0
        self.skip_assistant_turns = 0
        self.restart_resume_input_paused = False
        self.realtime_response_active = False
        await self.close_context()
        await self._release_sideband(sideband, sideband_manager)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
