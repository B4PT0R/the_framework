import hashlib
from copy import deepcopy
from xml.etree import ElementTree

from modict import modict

from the_framework.agent import Config, Image, Message, Plugin, hook, token_count

class RealtimeConfig(Config):
    model: str = "gpt-live-1-codex"
    voice: str = "maple"
    # Confirmed AVAS v3 call-creation limit. Initial items are role-bearing
    # messages; opaque Responses compaction items are not accepted here.
    max_initial_items: int = 128
    delegation_ack_filler: bool = True
    provider_whitelist: list[str] = modict.factory(list)
    provider_slot_max_characters: int = 12_000
    instruction_max_tokens: int = 16_000

    @modict.validator("max_initial_items", mode="after")
    def validate_max_initial_items(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 128:
            raise ValueError("realtime.max_initial_items must be within 2..128")
        return value

    @modict.validator("provider_whitelist", mode="after")
    def validate_provider_whitelist(self, value):
        if not isinstance(value, list) or any(
            not isinstance(name, str) or not name.strip() for name in value
        ) or len(value) != len(set(value)):
            raise ValueError("realtime.provider_whitelist must be a unique list")
        return value

    @modict.validator("provider_slot_max_characters", mode="after")
    def validate_provider_slot_max_characters(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("realtime.provider_slot_max_characters must be positive")
        return value

    @modict.validator("instruction_max_tokens", mode="after")
    def validate_instruction_max_tokens(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("realtime.instruction_max_tokens must be positive")
        return value

class RealtimePlugin(Plugin):
    """Project the canonical text agent into an ephemeral Realtime v3 session."""

    name = "realtime"
    description = "Full-duplex voice continuity for the canonical agent session."
    config = RealtimeConfig
    instruction_scope = "vocal"
    instructions_file = None

    @staticmethod
    def _text(message):
        return "\n".join(
            str(content.get("text") or "")
            for content in message.content
            if content.get("type") in {"input_text", "output_text"}
        ).strip()

    def _initial_items(self, items):
        projected = []
        for item in items:
            if not isinstance(item, Message):
                continue
            if item.role not in {"user", "assistant", "developer"}:
                continue
            text = self._text(item)
            if not text:
                continue
            projected.append({
                "type": "message",
                "role": item.role,
                "content": [{
                    "type": "output_text" if item.role == "assistant" else "input_text",
                    "text": text,
                }],
            })
        limit = self.config.max_initial_items
        return projected[-limit:]

    @staticmethod
    def _image_text(image):
        return image.description or ""

    def _provider_names(self):
        providers = self.agent.registry("providers")
        return tuple(
            name
            for name in self.config.provider_whitelist
            if (provider := providers.get(name)) is not None
            and "realtime" in provider.channels
        )

    def _provider_slots(self, names=None):
        slots = {}
        providers = self.agent.registry("providers")
        for name in names or self._provider_names():
            provider = providers[name]
            for output in provider.outputs()[:1]:
                if isinstance(output, Image):
                    text = self._image_text(output)
                elif isinstance(output, Message):
                    text = self._text(output)
                else:
                    text = ""
                text = text.strip()[:self.config.provider_slot_max_characters]
                if not text:
                    continue
                digest = hashlib.sha256(text.encode()).hexdigest()
                slots[name] = {
                    "hash": digest,
                    "text": text,
                }
        return slots

    @staticmethod
    def _memory_candidates(provider_slots):
        """Split the ranked RAG projection into independently appendable entries."""
        slot = provider_slots.get("relevant_memory") or {}
        text = slot.get("text")
        if not isinstance(text, str) or not text.strip():
            return []
        try:
            provider = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            return []
        if provider.tag != "context_provider":
            return []
        # Provider strings are wrapped once by the generic provider layer;
        # memory's own structured XML therefore sits one level deeper.
        memories = provider.find(".//retrieved_memories")
        if memories is None:
            return []
        candidates = []
        for memory in memories.findall("memory"):
            memory_id = str(memory.get("id") or "").strip()
            if not memory_id:
                continue
            candidate_provider = ElementTree.Element(provider.tag, provider.attrib)
            candidate_memories = ElementTree.SubElement(
                candidate_provider,
                "retrieved_memories",
            )
            candidate_memories.append(deepcopy(memory))
            candidates.append({
                "id": memory_id,
                "text": ElementTree.tostring(
                    candidate_provider,
                    encoding="unicode",
                ),
            })
        return candidates

    @staticmethod
    def _session_instructions(shared_instructions, provider_slots):
        provider_context = "\n\n".join(
            slot["text"] for slot in provider_slots.values()
        )
        if not provider_context:
            return shared_instructions
        return (
            f"{shared_instructions.rstrip()}\n\n"
            "# Dynamic live context\n"
            "The following provider context is current, ephemeral context. It is "
            "not a new utterance from the user.\n\n"
            f"{provider_context}"
        )

    def _fitted_provider_slots(self, shared_instructions, provider_slots):
        """Keep whole structured providers within the backend instruction budget."""
        if token_count(
            shared_instructions, model=self.config.model
        ) > self.config.instruction_max_tokens:
            raise ValueError(
                "canonical vocal instructions exceed the configured Realtime budget"
            )
        fitted = {}
        for name, slot in provider_slots.items():
            candidate = {**fitted, name: slot}
            instructions = self._session_instructions(
                shared_instructions, candidate
            )
            if token_count(
                instructions, model=self.config.model
            ) <= self.config.instruction_max_tokens:
                fitted[name] = slot
        return fitted

    @hook
    def session_snapshot(self, snapshot):
        """Attach an SDK-ready live-session projection to a canonical snapshot."""
        shared_instructions = self.agent.registry("instructions").render(
            scope="vocal"
        )
        provider_names = self._provider_names()
        provider_slots = self._provider_slots(provider_names)
        memory_candidates = self._memory_candidates(provider_slots)
        fitted_provider_slots = self._fitted_provider_slots(
            shared_instructions, provider_slots
        )
        session = {
            "model": self.config.model,
            "instructions": self._session_instructions(
                shared_instructions, fitted_provider_slots
            ),
            "audio": {"output": {"voice": self.config.voice}},
            "delegation": {
                "type": "client",
                "ack_filler": self.config.delegation_ack_filler,
            },
        }
        conversational_items = [
            item
            for item in snapshot["items"]
            if isinstance(item, Message) and item.role in {"user", "assistant"}
        ]
        initial_items = self._initial_items(conversational_items)
        if initial_items:
            session["initial_items"] = initial_items
        snapshot["extensions"][self.name] = {
            "protocol": "codex-realtime-v3",
            "model": self.config.model,
            "session": session,
            "provider_slots": fitted_provider_slots,
            # Ordered by the memory plugin's thresholded RAG ranking. The live
            # controller appends at most one previously unseen entry per user
            # turn, falling back through this list without ever injecting
            # below-threshold noise.
            "memory_candidates": memory_candidates,
        }
        return snapshot

    @hook
    def on_external_realtime_turn(self, payload):
        """Commit one provider-completed voice turn into canonical history."""
        role = payload.get("role")
        transcript = str(payload.get("transcript") or "").strip()
        if role not in {"user", "assistant"} or not transcript:
            raise ValueError("a realtime turn requires a user or assistant transcript")
        call_id = str(payload.get("call_id") or "")
        turn_id = str(payload.get("turn_id") or "")
        if not call_id or not turn_id:
            raise ValueError("a realtime turn requires call_id and turn_id")
        return self.agent.add_message(
            role,
            transcript,
            kind="realtime",
        )
