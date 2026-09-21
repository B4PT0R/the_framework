"""Transactional tools and live inventory for a dedicated memory curator."""

from html import escape
from typing import Literal

from ...agent.models.config import Config
from ...agent.extensions.plugin import Plugin
from ...agent.extensions.providers import provider
from ...utils.tokens import token_count
from ...agent.extensions.tools import tool

from harness_core.plugins.embeddings import embed_texts
from harness_core.plugins.memory.store import MemoryStore, format_memory_table_xml


class MemoryCuratorConfig(Config):
    store_path: str = ":memory:"
    entry_token_limit: int = 400
    entry_maintenance_token_threshold: int = 500
    max_entries: int = 400
    memory_token_budget: int = 150_000
    capacity_hard_ratio: float = 1.1
    tokenizer_model: str = "gpt-5.6-luna"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 128
    embedding_precision: int = 5


class MemoryCuratorPlugin(Plugin):
    name = "memory_curator"
    description = "Tools and full-table context for autonomous memory curation."
    config = MemoryCuratorConfig
    provider_channels = ("text",)

    def load(self):
        if self.loaded:
            return self
        super().load()
        if self.config.entry_maintenance_token_threshold <= 0:
            raise ValueError(
                "memory_curator.entry_maintenance_token_threshold must be positive"
            )
        self.store = MemoryStore(
            self.config.store_path,
            entry_token_limit=self.config.entry_token_limit,
            max_entries=self.config.max_entries,
            token_budget=self.config.memory_token_budget,
            capacity_hard_ratio=self.config.capacity_hard_ratio,
            tokenizer_model=self.config.tokenizer_model,
        )
        self._inventory_sources = None
        self._inventory = {}
        self._inventory_reviewed = False
        self._inventory_completed = False
        return self

    def _inventory_xml(self):
        if self._inventory_sources is None:
            return (
                '<curation_inventory status="missing">'
                "No candidate inventory has been recorded for this task."
                "</curation_inventory>"
            )
        status = "complete" if self._inventory_completed else "active"
        lines = [
            (
                f'<curation_inventory status="{status}" '
                f'total="{len(self._inventory)}" '
                f'unresolved="{len(self._unresolved_candidates())}" '
                f'final_review="{str(self._inventory_reviewed).lower()}">'
            ),
            "  <source_item_ids>",
        ]
        lines.extend(
            f"    <source_item_id>{escape(source_id)}</source_item_id>"
            for source_id in self._inventory_sources
        )
        lines.append("  </source_item_ids>")
        for candidate in self._inventory.values():
            attributes = [
                f'id="{escape(candidate["id"])}"',
                f'status="{escape(candidate["status"])}"',
            ]
            if candidate.get("resolution"):
                attributes.append(
                    f'resolution="{escape(candidate["resolution"])}"'
                )
            lines.append(f"  <candidate {' '.join(attributes)}>")
            lines.append(f"    <fact>{escape(candidate['fact'])}</fact>")
            if candidate.get("memory_ids"):
                lines.append(
                    "    <memory_ids>"
                    + escape(",".join(candidate["memory_ids"]))
                    + "</memory_ids>"
                )
            if candidate.get("rationale"):
                lines.append(
                    f"    <rationale>{escape(candidate['rationale'])}</rationale>"
                )
            lines.append("  </candidate>")
        lines.append("</curation_inventory>")
        return "\n".join(lines)

    def _unresolved_candidates(self):
        return [
            candidate
            for candidate in self._inventory.values()
            if candidate["status"] == "unresolved"
        ]

    def _validate_candidate_ids(self, candidate_ids):
        if self._inventory_sources is None:
            raise ValueError(
                "Create the exhaustive candidate inventory before mutating memory."
            )
        normalized = []
        for raw_id in candidate_ids:
            candidate_id = str(raw_id or "").strip()
            if not candidate_id:
                raise ValueError("candidate_ids must not contain empty IDs")
            if candidate_id not in self._inventory:
                raise ValueError(f"unknown curation candidate: {candidate_id}")
            if candidate_id in normalized:
                raise ValueError(f"duplicate curation candidate: {candidate_id}")
            normalized.append(candidate_id)
        return normalized

    def _validate_mutation_candidates(self, candidate_ids, source_item_ids):
        candidate_ids = self._validate_candidate_ids(candidate_ids)
        sources = tuple(dict.fromkeys(
            str(source_id or "").strip() for source_id in source_item_ids
        ))
        if any(not source_id for source_id in sources):
            raise ValueError("source_item_ids must not contain empty IDs")
        if candidate_ids and set(sources) != set(self._inventory_sources):
            raise ValueError(
                "a mutation resolving candidates must cite the inventory's "
                "canonical source_item_ids"
            )
        if sources and not candidate_ids:
            raise ValueError(
                "a mutation citing current task evidence must name the candidate_ids "
                "it preserves"
            )
        return candidate_ids

    def _resolve_after_mutation(self, candidate_ids, *, operation, memory_ids):
        for candidate_id in candidate_ids:
            candidate = self._inventory[candidate_id]
            candidate.update({
                "status": "resolved",
                "resolution": operation,
                "memory_ids": list(memory_ids),
                "rationale": "",
            })

    def inventory_constraint_error(self):
        if self._inventory_sources is None:
            return (
                "Curation cannot complete before an exhaustive candidate inventory. "
                "Call inventory with every distinct supported fact or memorable "
                "situation from the supplied session summary, then account for each."
            )
        unresolved = self._unresolved_candidates()
        if unresolved:
            ids = [candidate["id"] for candidate in unresolved]
            return (
                "Curation cannot complete while candidates remain unresolved. "
                "Preserve them through a memory mutation or explicitly resolve them "
                "as already_preserved or excluded, then perform a final evidence scan. "
                f"Unresolved candidate IDs: {ids}."
            )
        if not self._inventory_reviewed:
            return (
                "Curation cannot complete before the mandatory final evidence "
                "review. Re-read the entire supplied summary after resolving the "
                "initial checklist, then call inventory again with any missed "
                "atomic candidates, or with empty candidate lists if none were missed."
            )
        return None

    def embed(self, content):
        return self.embed_many([content])[0]

    def embed_many(self, contents):
        return embed_texts(
            self.agent.client,
            contents,
            model=self.config.embedding_model,
            dimensions=self.config.embedding_dimensions,
            precision=self.config.embedding_precision,
        )

    def completion_constraint_error(self):
        capacity = self.store.capacity()
        oversized = [
            {
                "id": entry.id,
                "key": entry.key,
                "title": entry.title,
                "tokens": tokens,
            }
            for entry in self.store.active()
            if (
                tokens := token_count(
                    entry.content,
                    model=self.config.tokenizer_model,
                )
            ) > self.config.entry_maintenance_token_threshold
        ]
        if capacity.serialized_tokens < capacity.token_budget and not oversized:
            return None

        instructions = [
            (
                "Curation cannot complete because the active memory still violates "
                "its terminal volume constraints. Continue curating with memory tools, "
                "then call complete_curation again."
            )
        ]
        if capacity.serialized_tokens >= capacity.token_budget:
            instructions.append(
                "Reduce total serialized memory strictly below 100% capacity "
                f"({capacity.serialized_tokens}/{capacity.token_budget} tokens now). "
                "Use targeted edits, updates, and merges to remove repetition and "
                "inflated wording while preserving supported facts and nuance."
            )
        if oversized:
            instructions.append(
                "Reduce or split every oversized entry so each resulting entry is at "
                f"most {self.config.entry_maintenance_token_threshold} tokens. "
                f"Oversized entries: {oversized}."
            )
        instructions.append(
            "Pass [] as source_item_ids when a repair should inherit existing evidence."
        )
        return " ".join(instructions)

    @provider
    def complete_memory(self):
        """Complete active memory table, saturation, and current curation checklist."""
        entries = self.store.active()
        return (
            format_memory_table_xml(entries, self.store.capacity())
            + "\n"
            + self._inventory_xml()
        )

    @tool
    def inventory(
        self,
        candidate_ids: list[str],
        candidate_facts: list[str],
        source_item_ids: list[str],
    ):
        """
        description: >-
          Record the exhaustive atomic checklist extracted from the supplied
          evidence before mutating memory. Calling it again with the same sources
          appends candidates found during a later coverage pass.
        parameters:
          properties:
            candidate_ids:
              description: >-
                Short stable IDs for distinct atomic candidates. Use [] only when
                the evidence truly has no durable content.
            candidate_facts:
              description: >-
                Self-contained supported facts or memorable situations in the same
                order as candidate_ids. Do not substitute broad themes for their
                individual facts.
            source_item_ids:
              description: Canonical evidence IDs supplied with this curation task.
        """
        sources = tuple(dict.fromkeys(
            str(source_id or "").strip() for source_id in source_item_ids
        ))
        if not sources or any(not source_id for source_id in sources):
            raise ValueError("source_item_ids must contain at least one non-empty ID")
        if len(candidate_ids) != len(candidate_facts):
            raise ValueError(
                "candidate_ids and candidate_facts must have the same length"
            )
        reset = self._inventory_sources != sources or self._inventory_completed
        if reset:
            self._inventory_sources = sources
            self._inventory = {}
            self._inventory_reviewed = False
            self._inventory_completed = False
        elif self._unresolved_candidates():
            raise ValueError(
                "Resolve the current candidate checklist before the final inventory "
                "review."
            )
        else:
            self._inventory_reviewed = True
        added = []
        for raw_id, raw_fact in zip(candidate_ids, candidate_facts, strict=True):
            candidate_id = str(raw_id or "").strip()
            fact = str(raw_fact or "").strip()
            if not candidate_id or len(candidate_id) > 64:
                raise ValueError("candidate id must contain 1..64 characters")
            if not fact:
                raise ValueError(f"candidate {candidate_id!r} has an empty fact")
            existing = self._inventory.get(candidate_id)
            if existing:
                if existing["fact"] != fact:
                    raise ValueError(
                        f"candidate {candidate_id!r} already exists with different text"
                    )
                continue
            self._inventory[candidate_id] = {
                "id": candidate_id,
                "fact": fact,
                "status": "unresolved",
                "resolution": "",
                "memory_ids": [],
                "rationale": "",
            }
            added.append(candidate_id)
        return {
            "status": "inventory_recorded",
            "added_candidate_ids": added,
            "total_candidates": len(self._inventory),
            "unresolved_candidates": len(self._unresolved_candidates()),
            "final_review": self._inventory_reviewed,
        }

    @tool
    def resolve_candidates(
        self,
        candidate_ids: list[str],
        disposition: Literal["already_preserved", "excluded"],
        memory_ids: list[str],
        rationale: str,
    ):
        """Resolve candidates already preserved or deliberately excluded."""
        candidate_ids = self._validate_candidate_ids(candidate_ids)
        if not candidate_ids:
            raise ValueError("candidate_ids must not be empty")
        if disposition not in {"already_preserved", "excluded"}:
            raise ValueError(
                "disposition must be already_preserved or excluded"
            )
        rationale = str(rationale or "").strip()
        if not rationale:
            raise ValueError("rationale must explain this resolution")
        memory_ids = list(dict.fromkeys(
            str(memory_id or "").strip() for memory_id in memory_ids
        ))
        if disposition == "already_preserved":
            if not memory_ids or any(not memory_id for memory_id in memory_ids):
                raise ValueError(
                    "already_preserved requires at least one active memory_id"
                )
            missing = [
                memory_id for memory_id in memory_ids
                if self.store.find(memory_id, active_only=True) is None
            ]
            if missing:
                raise ValueError(f"inactive or unknown memory IDs: {missing}")
        elif any(memory_ids):
            raise ValueError("excluded candidates must use [] for memory_ids")
        for candidate_id in candidate_ids:
            self._inventory[candidate_id].update({
                "status": "resolved",
                "resolution": disposition,
                "memory_ids": memory_ids,
                "rationale": rationale,
            })
        return {
            "status": "candidates_resolved",
            "candidate_ids": candidate_ids,
            "disposition": disposition,
            "unresolved_candidates": len(self._unresolved_candidates()),
        }

    @tool
    def add(
        self,
        key: str,
        title: str,
        content: str,
        source_item_ids: list[str],
        candidate_ids: list[str],
    ):
        """Add one new concise memory supported by canonical message IDs."""
        candidate_ids = self._validate_mutation_candidates(
            candidate_ids, source_item_ids
        )
        entry = self.store.add(
            key=key, title=title, content=content, embedding=[],
            source_item_ids=source_item_ids,
            defer_embedding=True,
        )
        self._resolve_after_mutation(
            candidate_ids, operation="added", memory_ids=[entry.id]
        )
        return {
            "status": "added",
            "id": entry.id,
            "saturation": self.store.capacity().saturation,
        }

    @tool
    def update(
        self,
        memory_id: str,
        content: str,
        source_item_ids: list[str],
        candidate_ids: list[str],
        title: str | None = None,
    ):
        """Replace one active memory paragraph; [] preserves existing evidence IDs."""
        candidate_ids = self._validate_mutation_candidates(
            candidate_ids, source_item_ids
        )
        entry = self.store.update(
            memory_id, content=content, title=title, embedding=[],
            source_item_ids=source_item_ids,
            defer_embedding=True,
        )
        self._resolve_after_mutation(
            candidate_ids, operation="updated", memory_ids=[entry.id]
        )
        return {
            "status": "updated",
            "id": entry.id,
            "saturation": self.store.capacity().saturation,
        }

    @tool
    def edit(
        self,
        memory_id: str,
        old_string: str,
        new_string: str,
        source_item_ids: list[str],
        candidate_ids: list[str],
        replace_all: bool = False,
    ):
        """Edit a precise passage in one active memory using an exact text match.

        By default old_string must occur exactly once. Set replace_all only when
        every exact occurrence should change. Pass [] as source_item_ids to
        preserve the entry's existing evidence IDs.
        """
        candidate_ids = self._validate_mutation_candidates(
            candidate_ids, source_item_ids
        )
        current = self.store.find(memory_id, active_only=True)
        if current is None:
            raise KeyError(memory_id)
        old_string = str(old_string or "")
        if not old_string:
            raise ValueError("memory edit requires a non-empty old_string")
        if new_string is None:
            raise ValueError("memory edit requires a non-null new_string")
        count = current.content.count(old_string)
        if count == 0:
            raise ValueError("old_string not found in active memory content")
        if count > 1 and not replace_all:
            raise ValueError(
                f"old_string matched {count} times in active memory content; "
                "set replace_all=true"
            )
        entry = self.store.edit(
            memory_id,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
            embedding=[],
            source_item_ids=source_item_ids,
            defer_embedding=True,
        )
        self._resolve_after_mutation(
            candidate_ids, operation="edited", memory_ids=[entry.id]
        )
        return {
            "status": "edited",
            "id": entry.id,
            "replacements": count if replace_all else 1,
            "saturation": self.store.capacity().saturation,
        }

    @tool
    def merge(
        self,
        memory_ids: list[str],
        key: str,
        title: str,
        content: str,
        source_item_ids: list[str],
        candidate_ids: list[str],
    ):
        """Merge exactly two memories; [] inherits their evidence IDs."""
        candidate_ids = self._validate_mutation_candidates(
            candidate_ids, source_item_ids
        )
        entry = self.store.merge(
            memory_ids, key=key, title=title, content=content,
            embedding=[], source_item_ids=source_item_ids,
            defer_embedding=True,
        )
        self._resolve_after_mutation(
            candidate_ids, operation="merged", memory_ids=[entry.id]
        )
        return {
            "status": "merged",
            "id": entry.id,
            "saturation": self.store.capacity().saturation,
        }

    @tool
    def split(
        self,
        memory_id: str,
        first_key: str,
        first_title: str,
        first_content: str,
        second_key: str,
        second_title: str,
        second_content: str,
        source_item_ids: list[str],
        candidate_ids: list[str],
    ):
        """Atomically replace one memory with two focused rewritten memories."""
        candidate_ids = self._validate_mutation_candidates(
            candidate_ids, source_item_ids
        )
        entries = self.store.split(
            memory_id,
            first={
                "key": first_key,
                "title": first_title,
                "content": first_content,
                "embedding": [],
            },
            second={
                "key": second_key,
                "title": second_title,
                "content": second_content,
                "embedding": [],
            },
            source_item_ids=source_item_ids,
            defer_embedding=True,
        )
        self._resolve_after_mutation(
            candidate_ids,
            operation="split",
            memory_ids=[entry.id for entry in entries],
        )
        return {
            "status": "split",
            "ids": [entry.id for entry in entries],
            "saturation": self.store.capacity().saturation,
        }

    @tool
    def complete_curation(self, coverage_summary: str):
        """Declare curation complete after the final evidence-coverage pass.

        Call this exactly once only after every durable candidate has been added,
        integrated, found already preserved, or deliberately excluded. Briefly
        summarize the subjects reviewed so completion remains auditable.
        """
        coverage_summary = str(coverage_summary or "").strip()
        if not coverage_summary:
            raise ValueError("coverage_summary must not be empty")
        inventory_error = self.inventory_constraint_error()
        if inventory_error:
            raise ValueError(inventory_error)
        constraint_error = self.completion_constraint_error()
        if constraint_error:
            raise ValueError(constraint_error)
        pending = self.store.pending_embeddings()
        if pending:
            embeddings = self.embed_many(entry.content for entry in pending)
            self.store.set_embeddings({
                entry.id: embedding
                for entry, embedding in zip(pending, embeddings, strict=True)
            })
        self._inventory_completed = True
        self.agent.end_turn()
        return {
            "status": "curation_complete",
            "coverage_summary": coverage_summary,
            "reindexed_entries": len(pending),
            "saturation": self.store.capacity().saturation,
        }
