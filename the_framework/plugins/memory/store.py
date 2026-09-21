"""Durable memory records, curation transactions and retrieval ranking."""

import json
import math
import os
import sqlite3
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from modict import modict

from ...utils.tokens import token_count
from ...agent.models.base import Base
from the_framework.utils.ids import timestamp_id


def utc_now():
    return datetime.now(UTC).isoformat()


class MemoryEntry(Base):
    id: str
    key: str
    title: str
    content: str
    status: str
    created_at: str
    updated_at: str
    embedding: list[float] = modict.factory(list)
    source_item_ids: list[str] = modict.factory(list)
    provenance: list[dict] = modict.factory(list)


class MemoryCapacity(Base):
    entries: int
    max_entries: int
    content_tokens: int
    entry_token_limit: int
    serialized_tokens: int
    token_budget: int
    hard_token_budget: int
    saturation: float


class MemoryStore:
    def __init__(
        self,
        path,
        *,
        entry_token_limit=400,
        max_entries=400,
        token_budget=150_000,
        capacity_hard_ratio=1.1,
        tokenizer_model="gpt-5.6-luna",
    ):
        if (
            entry_token_limit <= 0
            or max_entries <= 0
            or token_budget <= 0
            or capacity_hard_ratio < 1
        ):
            raise ValueError("memory limits must be positive")
        self.path = Path(path)
        self.entry_token_limit = entry_token_limit
        self.max_entries = max_entries
        self.token_budget = token_budget
        self.capacity_hard_ratio = float(capacity_hard_ratio)
        self.tokenizer_model = tokenizer_model
        self._connection = None
        if str(self.path) == ":memory:":
            self._connection = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        connection = self._connection or sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def initialize(self):
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    embedding TEXT NOT NULL DEFAULT '[]',
                    source_item_ids TEXT NOT NULL DEFAULT '[]',
                    provenance TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS memory_revisions (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    content TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    source_item_ids TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_embedding_queue (
                    memory_id TEXT PRIMARY KEY,
                    queued_at TEXT NOT NULL,
                    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
                );
            """)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(memories)")
            }
            if "embedding" not in columns:
                connection.execute(
                    "ALTER TABLE memories ADD COLUMN embedding TEXT NOT NULL DEFAULT '[]'"
                )
        if self._connection is None:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def _entry(self, row):
        if row is None:
            return None
        payload = dict(row)
        return MemoryEntry(
            id=payload["id"],
            key=payload["key"],
            title=payload["title"],
            content=payload["content"],
            status=payload["status"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            embedding=json.loads(payload.get("embedding") or "[]"),
            source_item_ids=json.loads(payload.get("source_item_ids") or "[]"),
            provenance=json.loads(payload.get("provenance") or "[]"),
        )

    def active(self):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memories WHERE status = 'active' ORDER BY updated_at, id"
            ).fetchall()
        return [self._entry(row) for row in rows]

    def find(self, reference, *, active_only=False):
        condition = " AND status = 'active'" if active_only else ""
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM memories WHERE (id = ? OR key = ?){condition}",
                (reference, reference),
            ).fetchone()
        return self._entry(row)

    def _validate_content(self, content):
        content = str(content or "").strip()
        if not content:
            raise ValueError("memory content must not be empty")
        return content

    def _active_from_connection(self, connection):
        rows = connection.execute(
            "SELECT * FROM memories WHERE status = 'active' ORDER BY updated_at, id"
        ).fetchall()
        return [self._entry(row) for row in rows]

    def _validate_capacity(self, connection, *, previous=None):
        capacity = self._capacity(self._active_from_connection(connection))
        hard_token_budget = int(self.token_budget * self.capacity_hard_ratio)
        exceeds_hard_limit = capacity.serialized_tokens > hard_token_budget
        if exceeds_hard_limit:
            if (
                previous is not None
                and capacity.serialized_tokens < previous.serialized_tokens
            ):
                return capacity
            raise ValueError(
                "memory snapshot exceeds its hard token budget: "
                f"{capacity.serialized_tokens}/{hard_token_budget} serialized tokens, "
                "reduce existing memory before expanding it further"
            )
        return capacity

    def _validate_identity(self, key, title):
        key = str(key or "").strip()
        title = str(title or "").strip()
        if not key or len(key) > 128:
            raise ValueError("memory key must contain 1 to 128 characters")
        if not title or len(title) > 200:
            raise ValueError("memory title must contain 1 to 200 characters")
        return key, title

    def _provenance(self, source_item_ids, operation):
        ids = [str(value) for value in source_item_ids or [] if str(value)]
        if not ids:
            raise ValueError("memory mutation requires canonical source_item_ids")
        return ids, {
            "operation": operation,
            "source_item_ids": ids,
            "recorded_at": utc_now(),
        }

    def _insert_memory(self, connection, entry):
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(memories)")
        }
        payload = {
            **entry,
            "kind": "memory",
            "importance": 0.5,
            "source_session_id": "",
            "source_revision": 0,
            "entities": "[]",
        }
        names = [name for name in payload if name in columns]
        connection.execute(
            f"INSERT INTO memories ({', '.join(names)}) VALUES "
            f"({', '.join('?' for _ in names)})",
            [payload[name] for name in names],
        )

    @staticmethod
    def _queue_embedding(connection, memory_id, queued_at):
        connection.execute(
            "INSERT INTO memory_embedding_queue (memory_id, queued_at) VALUES (?, ?) "
            "ON CONFLICT(memory_id) DO UPDATE SET queued_at=excluded.queued_at",
            (memory_id, queued_at),
        )

    def add(
        self, *, key, title, content, embedding, source_item_ids,
        defer_embedding=False,
    ):
        key, title = self._validate_identity(key, title)
        content = self._validate_content(content)
        ids, provenance = self._provenance(source_item_ids, "add")
        now = provenance["recorded_at"]
        with self.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM memories WHERE key = ?", (key,)
            ).fetchone():
                raise ValueError(f"memory key already exists: {key}")
            memory_id = timestamp_id()
            self._insert_memory(connection, {
                "id": memory_id, "key": key, "title": title, "content": content,
                "status": "active", "created_at": now, "updated_at": now,
                "embedding": json.dumps(list(embedding)),
                "source_item_ids": json.dumps(ids),
                "provenance": json.dumps([provenance]),
            })
            self._validate_capacity(connection)
            self._revision(connection, memory_id, "add", content, now, ids)
            if defer_embedding:
                self._queue_embedding(connection, memory_id, now)
        return self.find(memory_id)

    def update(
        self, reference, *, content, embedding, source_item_ids, title=None,
        defer_embedding=False,
    ):
        content = self._validate_content(content)
        with self.connect() as connection:
            previous = self._capacity(self._active_from_connection(connection))
            row = connection.execute(
                "SELECT * FROM memories WHERE (id = ? OR key = ?) AND status = 'active'",
                (reference, reference),
            ).fetchone()
            if row is None:
                raise KeyError(reference)
            inherited_ids = json.loads(row["source_item_ids"])
            ids, provenance = self._provenance(
                source_item_ids or inherited_ids,
                "update",
            )
            now = provenance["recorded_at"]
            new_title = row["title"] if title is None else self._validate_identity(row["key"], title)[1]
            history = [*json.loads(row["provenance"]), provenance][-64:]
            connection.execute(
                "UPDATE memories SET title=?, content=?, updated_at=?, embedding=?, "
                "source_item_ids=?, provenance=? WHERE id=?",
                (
                    new_title, content, now,
                    row["embedding"] if defer_embedding else json.dumps(list(embedding)),
                    json.dumps(ids), json.dumps(history), row["id"],
                ),
            )
            self._validate_capacity(connection, previous=previous)
            self._revision(connection, row["id"], "update", content, now, ids)
            if defer_embedding:
                self._queue_embedding(connection, row["id"], now)
        return self.find(row["id"])

    def edit(
        self,
        reference,
        *,
        old_string,
        new_string,
        replace_all,
        embedding,
        source_item_ids,
        defer_embedding=False,
    ):
        old_string = str(old_string or "")
        if not old_string:
            raise ValueError("memory edit requires a non-empty old_string")
        if new_string is None:
            raise ValueError("memory edit requires a non-null new_string")
        new_string = str(new_string)
        if old_string == new_string:
            raise ValueError("memory edit old_string and new_string must differ")

        with self.connect() as connection:
            previous = self._capacity(self._active_from_connection(connection))
            row = connection.execute(
                "SELECT * FROM memories WHERE (id = ? OR key = ?) AND status = 'active'",
                (reference, reference),
            ).fetchone()
            if row is None:
                raise KeyError(reference)

            count = row["content"].count(old_string)
            if count == 0:
                raise ValueError("old_string not found in active memory content")
            if count > 1 and not replace_all:
                raise ValueError(
                    f"old_string matched {count} times in active memory content; "
                    "set replace_all=true"
                )
            content = self._validate_content(
                row["content"].replace(old_string, new_string, -1 if replace_all else 1)
            )
            inherited_ids = json.loads(row["source_item_ids"])
            ids, provenance = self._provenance(
                source_item_ids or inherited_ids,
                "edit",
            )
            now = provenance["recorded_at"]
            history = [*json.loads(row["provenance"]), provenance][-64:]
            connection.execute(
                "UPDATE memories SET content=?, updated_at=?, embedding=?, "
                "source_item_ids=?, provenance=? WHERE id=?",
                (
                    content, now,
                    row["embedding"] if defer_embedding else json.dumps(list(embedding)),
                    json.dumps(ids),
                    json.dumps(history), row["id"],
                ),
            )
            self._validate_capacity(connection, previous=previous)
            self._revision(connection, row["id"], "edit", content, now, ids)
            if defer_embedding:
                self._queue_embedding(connection, row["id"], now)
        return self.find(row["id"])

    def set_embedding(self, reference, embedding):
        """Backfill a technical embedding without changing semantic timestamps."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM memories WHERE (id = ? OR key = ?)",
                (reference, reference),
            ).fetchone()
            if row is None:
                raise KeyError(reference)
            connection.execute(
                "UPDATE memories SET embedding = ? WHERE id = ?",
                (json.dumps(list(embedding)), row["id"]),
            )
            connection.execute(
                "DELETE FROM memory_embedding_queue WHERE memory_id = ?",
                (row["id"],),
            )
        return self.find(row["id"])

    def pending_embeddings(self):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT memories.* FROM memories "
                "JOIN memory_embedding_queue ON memory_embedding_queue.memory_id = memories.id "
                "WHERE memories.status = 'active' "
                "ORDER BY memory_embedding_queue.queued_at, memories.id"
            ).fetchall()
        return [self._entry(row) for row in rows]

    def set_embeddings(self, embeddings):
        embeddings = dict(embeddings)
        if not embeddings:
            return []
        with self.connect() as connection:
            for memory_id in embeddings:
                row = connection.execute(
                    "SELECT id FROM memories WHERE id = ? AND status = 'active'",
                    (memory_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(memory_id)
            connection.executemany(
                "UPDATE memories SET embedding = ? WHERE id = ?",
                [
                    (json.dumps(list(embedding)), memory_id)
                    for memory_id, embedding in embeddings.items()
                ],
            )
            connection.executemany(
                "DELETE FROM memory_embedding_queue WHERE memory_id = ?",
                [(memory_id,) for memory_id in embeddings],
            )
        return [self.find(memory_id) for memory_id in embeddings]

    def merge(
        self, references, *, key, title, content, embedding, source_item_ids,
        defer_embedding=False,
    ):
        references = list(dict.fromkeys(references))
        if len(references) != 2:
            raise ValueError("memory merge requires exactly two distinct active entries")
        key, title = self._validate_identity(key, title)
        content = self._validate_content(content)
        with self.connect() as connection:
            previous = self._capacity(self._active_from_connection(connection))
            rows = []
            for reference in references:
                row = connection.execute(
                    "SELECT * FROM memories WHERE (id = ? OR key = ?) AND status = 'active'",
                    (reference, reference),
                ).fetchone()
                if row is None:
                    raise KeyError(reference)
                rows.append(row)
            if len({row["id"] for row in rows}) != 2:
                raise ValueError("memory merge requires exactly two distinct active entries")
            inherited_ids = list(dict.fromkeys(
                source_id
                for row in rows
                for source_id in json.loads(row["source_item_ids"])
            ))
            ids, provenance = self._provenance(
                source_item_ids or inherited_ids,
                "merge",
            )
            now = provenance["recorded_at"]
            collision = connection.execute(
                "SELECT 1 FROM memories WHERE key = ?", (key,)
            ).fetchone()
            if collision:
                raise ValueError(f"memory key already exists: {key}")
            memory_id = timestamp_id()
            combined = []
            for row in rows:
                combined.extend(json.loads(row["provenance"]))
            provenance["supersedes"] = [row["id"] for row in rows]
            self._insert_memory(connection, {
                "id": memory_id, "key": key, "title": title, "content": content,
                "status": "active", "created_at": now, "updated_at": now,
                "embedding": json.dumps(list(embedding)),
                "source_item_ids": json.dumps(ids),
                "provenance": json.dumps([*combined, provenance][-64:]),
            })
            connection.executemany(
                "UPDATE memories SET status='superseded', updated_at=? WHERE id=?",
                [(now, row["id"]) for row in rows],
            )
            connection.executemany(
                "DELETE FROM memory_embedding_queue WHERE memory_id = ?",
                [(row["id"],) for row in rows],
            )
            self._validate_capacity(connection, previous=previous)
            self._revision(connection, memory_id, "merge", content, now, ids)
            if defer_embedding:
                self._queue_embedding(connection, memory_id, now)
        return self.find(memory_id)

    def split(
        self,
        reference,
        *,
        first,
        second,
        source_item_ids,
        defer_embedding=False,
    ):
        replacements = []
        for candidate in (first, second):
            key, title = self._validate_identity(candidate["key"], candidate["title"])
            replacements.append({
                "key": key,
                "title": title,
                "content": self._validate_content(candidate["content"]),
                "embedding": list(candidate["embedding"]),
            })
        if replacements[0]["key"] == replacements[1]["key"]:
            raise ValueError("memory split requires two distinct replacement keys")
        ids, provenance = self._provenance(source_item_ids, "split")
        now = provenance["recorded_at"]
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE (id = ? OR key = ?) AND status = 'active'",
                (reference, reference),
            ).fetchone()
            if row is None:
                raise KeyError(reference)
            for replacement in replacements:
                if connection.execute(
                    "SELECT 1 FROM memories WHERE key = ?", (replacement["key"],)
                ).fetchone():
                    raise ValueError(f"memory key already exists: {replacement['key']}")

            source_provenance = json.loads(row["provenance"])
            new_ids = [timestamp_id(), timestamp_id()]
            if new_ids[0] == new_ids[1]:
                new_ids[1] = f"{new_ids[1]}-split"
            for memory_id, replacement in zip(new_ids, replacements):
                split_provenance = {
                    **provenance,
                    "supersedes": [row["id"]],
                    "split_siblings": new_ids,
                }
                self._insert_memory(connection, {
                    "id": memory_id,
                    **replacement,
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                    "embedding": json.dumps(replacement["embedding"]),
                    "source_item_ids": json.dumps(ids),
                    "provenance": json.dumps(
                        [*source_provenance, split_provenance][-64:]
                    ),
                })
            connection.execute(
                "UPDATE memories SET status='superseded', updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            connection.execute(
                "DELETE FROM memory_embedding_queue WHERE memory_id = ?",
                (row["id"],),
            )
            self._validate_capacity(connection)
            for memory_id, replacement in zip(new_ids, replacements):
                self._revision(
                    connection,
                    memory_id,
                    "split",
                    replacement["content"],
                    now,
                    ids,
                )
                if defer_embedding:
                    self._queue_embedding(connection, memory_id, now)
        return [self.find(memory_id) for memory_id in new_ids]

    def _revision(self, connection, memory_id, operation, content, changed_at, ids):
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(memory_revisions)")
        }
        payload = {
            "id": timestamp_id(), "memory_id": memory_id, "operation": operation,
            "content": content, "changed_at": changed_at,
            "source_item_ids": json.dumps(ids), "source_session_id": "",
            "source_revision": 0,
        }
        names = [name for name in payload if name in columns]
        connection.execute(
            f"INSERT INTO memory_revisions ({', '.join(names)}) VALUES "
            f"({', '.join('?' for _ in names)})",
            [payload[name] for name in names],
        )

    def _capacity(self, entries):
        content_tokens = sum(
            token_count(entry.content, model=self.tokenizer_model)
            for entry in entries
        )
        capacity = MemoryCapacity(
            entries=len(entries), max_entries=self.max_entries,
            content_tokens=content_tokens,
            entry_token_limit=self.entry_token_limit,
            serialized_tokens=0,
            token_budget=self.token_budget,
            hard_token_budget=int(self.token_budget * self.capacity_hard_ratio),
            saturation=0.0,
        )
        for _ in range(8):
            serialized_tokens = token_count(
                format_memory_table_xml(entries, capacity),
                model=self.tokenizer_model,
            )
            saturation = serialized_tokens / self.token_budget
            if (
                serialized_tokens == capacity.serialized_tokens
                and saturation == capacity.saturation
            ):
                break
            capacity.serialized_tokens = serialized_tokens
            capacity.saturation = saturation
        return capacity

    def capacity(self):
        return self._capacity(self.active())

    def revision_count(self, reference):
        entry = self.find(reference)
        if entry is None:
            return 0
        with self.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM memory_revisions WHERE memory_id = ?", (entry.id,)
            ).fetchone()[0]


def cosine(left, right):
    if not left or not right or len(left) != len(right):
        return 0.0
    norm = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right)) / norm if norm else 0.0


def rank_memories(
    entries,
    context_embeddings,
    *,
    context_half_life_messages=2,
    semantic_moment_order=3,
    temporal_weight=0.05,
    temporal_half_life_days=90,
    minimum_score=None,
):
    if not context_embeddings:
        return []
    if context_half_life_messages <= 0:
        raise ValueError("context_half_life_messages must be positive")
    if semantic_moment_order <= 0:
        raise ValueError("semantic_moment_order must be positive")
    if temporal_weight < 0:
        raise ValueError("temporal_weight must not be negative")
    if temporal_half_life_days <= 0:
        raise ValueError("temporal_half_life_days must be positive")
    if minimum_score is not None and not 0 <= minimum_score <= 1:
        raise ValueError("minimum_score must be between 0 and 1")
    decay = math.log(2) / context_half_life_messages
    weights = [math.exp(-decay * index) for index in range(len(context_embeddings))]
    total = sum(weights)
    weights = [value / total for value in weights]
    now = datetime.now(UTC)

    def score(entry):
        semantic = sum(
            weight * (
                (cosine(entry.embedding, embedding) + 1) / 2
            ) ** semantic_moment_order
            for weight, embedding in zip(weights, reversed(context_embeddings))
        ) ** (1 / semantic_moment_order)
        age_days = max(0, (now - datetime.fromisoformat(entry.updated_at)).total_seconds() / 86400)
        temporal = math.exp(-math.log(2) * age_days / temporal_half_life_days)
        return (semantic + temporal_weight * temporal) / (1 + temporal_weight)

    scored = [(entry, score(entry)) for entry in entries]
    if minimum_score is not None:
        scored = [value for value in scored if value[1] >= minimum_score]
    return [entry for entry, _ in sorted(scored, key=lambda value: value[1], reverse=True)]


def format_memories_xml(entries):
    if not entries:
        return None
    lines = ["<retrieved_memories>"]
    for entry in entries:
        lines.append(
            f'<memory id="{escape(entry.id, quote=True)}" key="{escape(entry.key, quote=True)}" '
            f'updated_at="{escape(entry.updated_at, quote=True)}">'
            f"{escape(entry.content)}</memory>"
        )
    lines.append("</retrieved_memories>")
    return "\n".join(lines)


def format_memory_table_xml(entries, capacity):
    lines = ["<memory_table>"]
    if capacity is not None:
        lines.append(
            f'<capacity entries="{capacity.entries}" '
            f'content_tokens="{capacity.content_tokens}" '
            f'serialized_tokens="{capacity.serialized_tokens}" '
            f'token_budget="{capacity.token_budget}" '
            f'hard_token_budget="{capacity.hard_token_budget}" '
            f'saturation="{capacity.saturation * 100:.2f}%" />'
        )
    for entry in entries:
        lines.append(
            f'<memory id="{escape(entry.id, quote=True)}" key="{escape(entry.key, quote=True)}" '
            f'title="{escape(entry.title, quote=True)}" created_at="{entry.created_at}" '
            f'updated_at="{entry.updated_at}">{escape(entry.content)}</memory>'
        )
    lines.append("</memory_table>")
    return "\n".join(lines)
