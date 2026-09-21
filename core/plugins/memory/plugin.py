from core.agent.spec import AgentSpec, QueuePolicy
from core.agent.extensions.instructions import Instruction
import json
import math
from pathlib import Path

from core.agent import AgentResult, Message, Plugin, agent_trigger
from ...agent.models.config import Config
from ...agent.models.lifecycle import AgentTaskRequested
from ...agent.extensions.providers import provider
from ...utils.tokens import token_count
from ...agent.extensions.tools import tool
from core.utils.ids import timestamp_id

from core.plugins.embeddings import embed_texts
from core.plugins.memory.store import MemoryStore, format_memories_xml, rank_memories


class MemoryConfig(Config):
    model: str = "gpt-5.6-terra"
    reasoning_effort: str = "medium"
    text_verbosity: str = "medium"
    queue_limit: int = 32
    curator_context_token_limit: int = 280_000
    curator_trigger_ratio: float = 0.95
    curator_working_token_reserve: int = 100_000
    entry_token_limit: int = 400
    max_entries: int = 400
    memory_token_budget: int = 150_000
    capacity_hard_ratio: float = 1.1
    entry_maintenance_token_threshold: int = 500
    maintenance_max_stalled_turns: int = 3
    provider_token_budget: int = 3_000
    provider_relevance_threshold: float = 0.3
    retrieval_context_messages: int = 16
    retrieval_limit: int = 10
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 128
    embedding_precision: int = 5
    context_half_life_messages: float = 2
    semantic_moment_order: float = 3
    temporal_weight: float = 0.05
    temporal_half_life_days: float = 90


def memory_path(session_path):
    return (
        Path(session_path).with_name("relational-memory.sqlite3")
        if session_path is not None
        else Path(":memory:")
    )


def message_text(message):
    return "".join(
        value.text
        for value in message.content
        if isinstance(getattr(value, "text", None), str)
    ).strip()


class MemoryPlugin(Plugin):
    curator_instructions: str | None = None
    name = "memory"
    description = "Relational memory retrieval, deliberate recall, and autonomous curation."
    config = MemoryConfig
    restart_on_config_change = True
    instruction_scope = "general"
    provider_channels = ("text", "realtime")
    instructions_file = Path(__file__).with_name("instructions.md")

    def __init__(self, agent):
        super().__init__(agent)
        self.store = None
        self.last_curator_error = None
        self.last_retrieval_error = None

    def load(self):
        if self.loaded:
            return self
        if not isinstance(self.curator_instructions, str) or not self.curator_instructions.strip():
            raise ValueError("memory requires application-supplied curator_instructions")
        super().load()
        for name in (
            "curator_context_token_limit", "curator_working_token_reserve",
            "entry_token_limit", "max_entries", "memory_token_budget",
            "entry_maintenance_token_threshold",
            "maintenance_max_stalled_turns",
            "provider_token_budget", "retrieval_context_messages",
            "retrieval_limit", "embedding_dimensions",
            "embedding_precision",
            "context_half_life_messages", "semantic_moment_order",
            "temporal_half_life_days",
        ):
            if getattr(self.config, name) <= 0:
                raise ValueError(f"memory.{name} must be positive")
        if not 0 < self.config.curator_trigger_ratio <= 1:
            raise ValueError(
                "memory.curator_trigger_ratio must be greater than 0 and at most 1"
            )
        if not 1 <= self.config.capacity_hard_ratio <= 1.25:
            raise ValueError(
                "memory.capacity_hard_ratio must be between 1 and 1.25"
            )
        if self.config.temporal_weight < 0:
            raise ValueError("memory.temporal_weight must not be negative")
        if not 0 <= self.config.provider_relevance_threshold <= 1:
            raise ValueError(
                "memory.provider_relevance_threshold must be between 0 and 1"
            )
        if self.config.reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise ValueError(
                "memory.reasoning_effort must be low, medium, high, or xhigh"
            )
        if self.config.text_verbosity not in {"low", "medium", "high"}:
            raise ValueError(
                "memory.text_verbosity must be low, medium, or high"
            )
        minimum_context_limit = math.ceil((
            self.config.memory_token_budget * self.config.capacity_hard_ratio
            + self.config.curator_working_token_reserve
        ) / self.config.curator_trigger_ratio)
        curator_context_token_limit = max(
            self.config.curator_context_token_limit,
            minimum_context_limit,
        )
        self.store = MemoryStore(
            memory_path(self.agent.session.path),
            entry_token_limit=self.config.entry_token_limit,
            max_entries=self.config.max_entries,
            token_budget=self.config.memory_token_budget,
            capacity_hard_ratio=self.config.capacity_hard_ratio,
            tokenizer_model=self.config.model,
        )
        declaration = next(
            value for kind, value in self._contributions
            if kind == "agents" and value.name == "memory.jiminy"
        )
        configuration = {
            "model": self.config.model,
            "reasoning": {"effort": self.config.reasoning_effort},
            "text": {"verbosity": self.config.text_verbosity},
            "compaction": {
                "context_token_limit": curator_context_token_limit,
                "trigger_ratio": self.config.curator_trigger_ratio,
            },
            "memory_curator": {
                "store_path": str(memory_path(self.agent.session.path)),
                "entry_token_limit": self.config.entry_token_limit,
                "entry_maintenance_token_threshold": (
                    self.config.entry_maintenance_token_threshold
                ),
                "max_entries": self.config.max_entries,
                "memory_token_budget": self.config.memory_token_budget,
                "capacity_hard_ratio": self.config.capacity_hard_ratio,
                "tokenizer_model": self.config.model,
                "embedding_model": self.config.embedding_model,
                "embedding_dimensions": self.config.embedding_dimensions,
                "embedding_precision": self.config.embedding_precision,
            }
        }
        declaration.spec = AgentSpec({
            **declaration.spec,
            "configuration": configuration,
            "instructions": (Instruction(name="role", content=self.curator_instructions),),
            "queue": QueuePolicy({**declaration.spec.queue, "limit": self.config.queue_limit}),
        })
        return self

    @agent_trigger(
        AgentSpec(
            name="jiminy",
            instructions="Curate the supplied evidence into durable memory.",
            configuration={
                "reasoning": {"effort": "medium"},
                "text": {"verbosity": "medium"},
                "compaction": {"context_token_limit": 280000, "trigger_ratio": 0.95},
                "model": "gpt-5.6-terra",
            },
            plugins=["core.plugins.memory.curator:MemoryCuratorPlugin"],
            idle_timeout_seconds=60,
            completion_tool="complete_curation",
            completion_retry_limit=4,
            queue=QueuePolicy(limit=32),
        ),
        observes=["agent.compaction.end", "agent.task.requested"],
        input_item_field="retired_items",
    )
    def jiminy(self, payload):
        """Curate a past session summary after it leaves canonical working context."""
        result = AgentResult.from_dict(payload)
        self.last_curator_error = result.error if result.status != "completed" else None
        capacity = self.store.capacity()
        entry_token_counts = [
            (
                entry,
                token_count(entry.content, model=self.config.model),
            )
            for entry in self.store.active()
        ]
        oversized_entries = [
            (entry, tokens)
            for entry, tokens in entry_token_counts
            if tokens > self.config.entry_maintenance_token_threshold
        ]
        oversized_excess = sum(
            tokens - self.config.entry_maintenance_token_threshold
            for _, tokens in oversized_entries
        )
        metadata = dict(result.metadata or {})
        request_id = str(
            metadata.get("agent_request_id")
            or metadata.get("auxiliary_request_id")
            or ""
        )
        prefix = "memory-maintenance:"
        maintenance_turn = False
        stalled_turns = 0
        previous_tokens = None
        previous_oversized_excess = None
        previous_entries = None
        if request_id.startswith(prefix):
            try:
                _, stalled, tokens, excess, entries, _ = request_id.split(":", 5)
                stalled_turns = int(stalled)
                previous_tokens = int(tokens)
                previous_oversized_excess = int(excess)
                previous_entries = int(entries)
                maintenance_turn = True
            except (ValueError, IndexError):
                maintenance_turn = True
        over_limit = (
            capacity.serialized_tokens > capacity.token_budget
        )
        at_or_above_soft_limit = (
            capacity.serialized_tokens >= capacity.token_budget
        )
        needs_maintenance = over_limit or (
            maintenance_turn and at_or_above_soft_limit
        ) or bool(oversized_entries)
        if maintenance_turn and previous_tokens is not None and previous_entries is not None:
            progressed = (
                capacity.serialized_tokens < previous_tokens
                or (
                    previous_oversized_excess is not None
                    and oversized_excess < previous_oversized_excess
                )
            )
            stalled_turns = 0 if progressed else stalled_turns + 1
        maintenance_requested = False
        if result.status == "completed" and needs_maintenance:
            if stalled_turns < self.config.maintenance_max_stalled_turns:
                maintenance_request_id = (
                    f"{prefix}{stalled_turns}:{capacity.serialized_tokens}:"
                    f"{oversized_excess}:{capacity.entries}:{timestamp_id()}"
                )
                oversized_summary = [
                    {
                        "id": entry.id,
                        "key": entry.key,
                        "title": entry.title,
                        "tokens": tokens,
                    }
                    for entry, tokens in oversized_entries
                ]
                entry_instruction = (
                    " The following entries exceed the "
                    f"{self.config.entry_maintenance_token_threshold}-token editorial threshold: "
                    f"{json.dumps(oversized_summary, ensure_ascii=False)}. Target them "
                    "with concise updates or split each overbroad entry into two "
                    "independently useful, semantically focused memories. Preserve all "
                    "useful details while ensuring every resulting entry is at most "
                    f"{self.config.entry_maintenance_token_threshold} tokens."
                    if oversized_entries
                    else " Do not add or split memories during this global-volume pass."
                )
                self.agent.emit(AgentTaskRequested(
                    request_id=maintenance_request_id,
                    profile="memory.jiminy",
                    prompt=(
                        "The complete active memory table requires automatic maintenance: "
                        "it is over its configured capacity, contains oversized entries, "
                        "or remains unresolved after a previous recovery pass ("
                        f"entries={capacity.entries}, "
                        f"serialized_tokens={capacity.serialized_tokens}/"
                        f"{capacity.token_budget}). Reduce global "
                        "verbosity substantially through several targeted update and merge "
                        "operations. Preserve the useful factual, relational, emotional, "
                        "temporal, and project content as completely as possible; remove "
                        "repetition and decorative wording rather than information."
                        f"{entry_instruction} Pass an empty "
                        "source_item_ids list to update or merge so each operation inherits "
                        "the edited memories' existing canonical evidence. Continue making "
                        "useful reductions throughout this turn rather than stopping after "
                        "one small edit. Automatic maintenance will continue for as many "
                        "productive turns as necessary until serialized token usage is "
                        "strictly below 100% and no entry exceeds "
                        f"{self.config.entry_maintenance_token_threshold} tokens."
                    ),
                    source_item_ids=[],
                ))
                maintenance_requested = True
            else:
                self.last_curator_error = (
                    "memory remains at or above its soft capacity after "
                    f"{self.config.maintenance_max_stalled_turns} consecutive Jiminy "
                    "turns without measurable reduction"
                )
        return {
            "status": result.status,
            "error": self.last_curator_error,
            "maintenance_requested": maintenance_requested,
            "memory_saturation": capacity.saturation,
        }

    def conversation(self):
        return [
            item for item in self.agent.session.history
            if isinstance(item, Message)
            and item.role in {"user", "assistant"}
            and message_text(item)
        ][-self.config.retrieval_context_messages:]

    def embed(self, texts):
        return embed_texts(
            self.agent.client,
            texts,
            model=self.config.embedding_model,
            dimensions=self.config.embedding_dimensions,
            precision=self.config.embedding_precision,
        )

    def ensure_embeddings(self, entries):
        missing = [entry for entry in entries if not entry.embedding]
        if missing:
            vectors = self.embed([entry.content for entry in missing])
            for entry, embedding in zip(missing, vectors):
                entry.embedding = embedding
                self.store.set_embedding(entry.id, embedding)

    def source_item_ids(self):
        return [str(item.id) for item in self.conversation()]

    def ranked(self, query):
        entries = self.store.active()
        if not entries:
            return []
        self.ensure_embeddings(entries)
        embedding = self.embed([query])[0]
        return rank_memories(
            entries,
            [embedding],
            context_half_life_messages=self.config.context_half_life_messages,
            semantic_moment_order=self.config.semantic_moment_order,
            temporal_weight=self.config.temporal_weight,
            temporal_half_life_days=self.config.temporal_half_life_days,
        )

    def within_provider_budget(self, ranked, *, limit=None):
        selected = []
        candidates = ranked if limit is None else ranked[:limit]
        for entry in candidates:
            candidate = format_memories_xml([*selected, entry])
            if token_count(
                candidate,
                model=self.agent.configs.model,
            ) > self.config.provider_token_budget:
                continue
            selected.append(entry)
        return selected

    @tool
    def remember(self, content: str):
        """Ask Jiminy to curate something into durable relational memory.

        Describe freely what should be preserved and any nuance that matters.
        Jiminy will decide whether to add, update, merge, or decline the request.
        """
        content = str(content or "").strip()
        if not content:
            raise ValueError("memory request must not be empty")
        source_item_ids = self.source_item_ids()
        if not source_item_ids:
            raise ValueError("remember requires a recent user or assistant message as provenance")
        request_id = timestamp_id()
        self.agent.emit(AgentTaskRequested(
            request_id=request_id,
            profile="memory.jiminy",
            prompt=content,
            source_item_ids=source_item_ids,
        ))
        return {
            "status": "requested",
            "request_id": request_id,
            "curator": "memory.jiminy",
        }

    @tool
    def search(self, query: str, limit: int | None = None):
        """Actively search durable memory by semantic relevance.

        Use this when automatic recollections are insufficient or your companion asks
        you to remember, verify, or revisit something from the more distant past.
        """
        query = str(query or "").strip()
        if not query:
            raise ValueError("memory search query must not be empty")
        limit = self.config.retrieval_limit if limit is None else int(limit)
        if not 1 <= limit <= self.config.retrieval_limit:
            raise ValueError(
                f"memory search limit must be between 1 and {self.config.retrieval_limit}"
            )
        selected = self.within_provider_budget(self.ranked(query), limit=limit)
        return {
            "status": "found" if selected else "empty",
            "query": query,
            "count": len(selected),
            "memories": [
                {
                    "id": entry.id,
                    "key": entry.key,
                    "title": entry.title,
                    "content": entry.content,
                    "updated_at": entry.updated_at,
                    "source_item_ids": entry.source_item_ids,
                }
                for entry in selected
            ],
        }

    @provider
    def relevant_memory(self):
        """Memories semantically relevant to the current conversation."""
        conversation = self.conversation()
        entries = self.store.active()
        if not conversation or not entries:
            return None
        try:
            texts = [message_text(message) for message in conversation]
            self.ensure_embeddings(entries)
            embeddings = self.embed(texts)
        except Exception as error:  # noqa: BLE001 - retrieval must fail softly
            self.last_retrieval_error = str(error)
            return None
        self.last_retrieval_error = None
        ranked = rank_memories(
            entries,
            embeddings,
            context_half_life_messages=self.config.context_half_life_messages,
            semantic_moment_order=self.config.semantic_moment_order,
            temporal_weight=self.config.temporal_weight,
            temporal_half_life_days=self.config.temporal_half_life_days,
            minimum_score=self.config.provider_relevance_threshold,
        )
        selected = self.within_provider_budget(ranked)
        return format_memories_xml(selected)
