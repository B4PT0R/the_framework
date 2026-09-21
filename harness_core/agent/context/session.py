import json
from functools import wraps
from pathlib import Path
from threading import RLock
from xml.etree import ElementTree

from modict import modict

from ..models.usage import ResponseUsage
from ...utils.persistence import atomic_text_writer
from ..models.responses import (
    Compaction as CompactionItem,
)
from ..models.responses import (
    FunctionCall,
    FunctionCallOutput,
    Image,
    MediaReference,
    Message,
    Portrait,
    ResponseItem,
)
from ..models.base import Base
from harness_core.utils.ids import timestamp_id

_SESSION_WRITE_LOCK = RLock()


def serialized_session_write(method):
    """Keep each in-memory mutation and its durable snapshot indivisible."""
    @wraps(method)
    def wrapped(*args, **kwargs):
        with _SESSION_WRITE_LOCK:
            return method(*args, **kwargs)
    return wrapped


class SessionWatermark(Base):
    session_id: str
    revision: int
    anchor_id: str | None
    tail_start: int


class CommandReceipt(Base):
    status: str
    error: str | None = None


class Session(Base):
    path = modict.attr(None)
    id: str = modict.factory(timestamp_id)
    history: list[ResponseItem] = modict.factory(list)
    archive: list[ResponseItem] = modict.factory(list)
    revision: int = 0
    anchor_id: str | None = None
    anchor_token_count: int | None = None
    tail_start: int = 0
    context_usage: ResponseUsage | None = None
    commands: dict[str, CommandReceipt] = modict.factory(dict)
    plugins: dict[str, bool] = modict.factory(dict)

    def watermark(self):
        return SessionWatermark(
            session_id=self.id,
            revision=self.revision,
            anchor_id=self.anchor_id,
            tail_start=self.tail_start,
        )

    def archive_turns(self):
        """Group the retained display archive without splitting conversational turns."""
        turns = []
        current = []
        seen = set()
        for item in self.archive:
            item_id = getattr(item, "id", None)
            if item_id and item_id in seen:
                continue
            if item_id:
                seen.add(item_id)
            starts_turn = isinstance(item, Message) and item.role == "user"
            if starts_turn and current:
                turns.append(current)
                current = []
            current.append(item)
        if current:
            turns.append(current)
        return turns

    def page_archive_turns(self, *, before=None, limit=25):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("session page limit must be an integer within 1..100")
        turns = self.archive_turns()
        end = len(turns)
        if before is not None:
            indexes = [
                index for index, turn in enumerate(turns)
                if turn and getattr(turn[0], "id", None) == before
            ]
            if indexes:
                end = indexes[0]
            else:
                end = 0
        start = max(0, end - limit)
        page = turns[start:end]
        return {
            "turns": page,
            "next_before": getattr(page[0][0], "id", None) if start > 0 and page else None,
            "has_more": start > 0,
        }

    @staticmethod
    def _interrupted_tool_output(call_id):
        return FunctionCallOutput(
            call_id=call_id,
            output=json.dumps({
                "status": "failure",
                "message_ids": [],
                "error": (
                    "Tool execution was interrupted before a durable result was "
                    "recorded. The tool was not automatically retried because its "
                    "side effects are unknown."
                ),
            }, separators=(",", ":")),
        )

    @classmethod
    def _local_targets_available(cls, item):
        if isinstance(item, (Image, MediaReference)):
            return Path(item.path).is_file()
        if not isinstance(item, Message) or item.kind != "copied_files":
            return True
        targets = []
        for content in item.content:
            text = getattr(content, "text", None)
            if not isinstance(text, str) or "<copied_files" not in text:
                continue
            try:
                root = ElementTree.fromstring(text)
            except ElementTree.ParseError:
                continue
            if root.tag != "copied_files":
                continue
            targets.extend(
                Path(path)
                for child in root.findall("file")
                if isinstance(path := child.get("path"), str) and path
            )
        return not targets or all(target.is_file() for target in targets)

    @classmethod
    def _prune_missing_local_references(cls, items):
        return [item for item in items if cls._local_targets_available(item)]

    @classmethod
    def _sanitize_tool_protocol(cls, items, *, preserve_duplicates=False):
        sanitized = []
        unresolved = {}
        resolved = set()
        repaired = []

        def flush_unresolved():
            for call_id in tuple(unresolved):
                sanitized.append(cls._interrupted_tool_output(call_id))
                repaired.append(call_id)
                resolved.add(call_id)
            unresolved.clear()

        for item in items:
            is_boundary = (
                isinstance(item, CompactionItem)
                or isinstance(item, Message) and item.role == "user"
            )
            if is_boundary:
                flush_unresolved()

            if isinstance(item, FunctionCall):
                unresolved[item.call_id] = item
                sanitized.append(item)
                continue
            if isinstance(item, FunctionCallOutput):
                if item.call_id in unresolved:
                    unresolved.pop(item.call_id)
                    resolved.add(item.call_id)
                    sanitized.append(item)
                elif preserve_duplicates or item.call_id not in resolved:
                    # An unmatched output may legitimately resolve a call hidden in
                    # an opaque compaction anchor, so it must not be discarded.
                    sanitized.append(item)
                continue
            sanitized.append(item)

        flush_unresolved()
        return sanitized, repaired

    @serialized_session_write
    def sanitize(self):
        """Atomically repair protocol sequences and discard stale local references."""
        history = self._prune_missing_local_references(self.history)
        archive = self._prune_missing_local_references(self.archive)
        sanitized_history, history_repairs = self._sanitize_tool_protocol(history)
        sanitized_archive, archive_repairs = self._sanitize_tool_protocol(
            archive,
            preserve_duplicates=True,
        )
        repaired = list(dict.fromkeys([*history_repairs, *archive_repairs]))
        history_changed = sanitized_history != self.history
        archive_changed = sanitized_archive != self.archive
        if not history_changed and not archive_changed:
            return {"changed": False, "repaired_function_calls": []}

        previous_history = list(self.history)
        previous_archive = list(self.archive)
        previous_revision = self.revision
        previous_usage = self.context_usage
        self.history[:] = sanitized_history
        self.archive[:] = sanitized_archive
        self.revision += 1
        self.context_usage = None
        try:
            self.save()
        except Exception:
            self.history[:] = previous_history
            self.archive[:] = previous_archive
            self.revision = previous_revision
            self.context_usage = previous_usage
            raise
        return {
            "changed": True,
            "repaired_function_calls": repaired,
        }

    def to_api_format(self, *, max_input_images=0, include_images=True, items=None):
        """Project lightweight local history into strict remote Responses input."""
        if not isinstance(max_input_images, int) or max_input_images < 0:
            raise ValueError("max_input_images must be a non-negative integer")
        canonical = items is None or items is self.history
        if canonical:
            self.sanitize()
        source = list(self.history if canonical else items)
        retained = {
            position
            for position in [
                index
                for index, item in enumerate(source)
                if isinstance(item, Image) and not isinstance(item, Portrait)
            ][-max_input_images:]
        } if include_images and max_input_images else set()
        projected = []
        for index, item in enumerate(source):
            if isinstance(item, Portrait):
                if include_images:
                    projected.append(item.to_api_format())
                continue
            if isinstance(item, Image):
                if index in retained:
                    projected.append(item.to_api_format())
                continue
            if isinstance(item, Message):
                payload = json.loads(json.dumps(item))
                projected.append({
                    "type": "message",
                    "role": item.role,
                    "content": payload.get("content", []),
                })
                continue
            if isinstance(item, MediaReference):
                projected.append(item.to_api_format())
                continue
            projected.append(
                json.loads(json.dumps(item))
                if isinstance(item, ResponseItem)
                else item
            )
        return projected

    @classmethod
    def open(cls, path):
        path = Path(path)
        if path.exists():
            payload = json.loads(path.read_text())
            payload["history"] = [
                ResponseItem.from_dict(item)
                for item in payload.get("history", [])
            ]
            payload["archive"] = [
                ResponseItem.from_dict(item)
                for item in payload.get("archive", payload.get("history", []))
            ]
            payload["commands"] = {
                command_id: CommandReceipt(**receipt)
                for command_id, receipt in payload.get("commands", {}).items()
            }
            if payload.get("context_usage") is not None:
                payload["context_usage"] = ResponseUsage.from_dict(
                    payload["context_usage"]
                )
            session = cls(**payload)
        else:
            session = cls()
        session.set_attr("path", path)
        return session

    @serialized_session_write
    def save(self):
        if self.path is None:
            return self
        with atomic_text_writer(self.path) as file:
            json.dump(self, file)
        return self

    @serialized_session_write
    def append(self, item):
        if not self._local_targets_available(item):
            return None
        self.history.append(item)
        self.archive.append(item)
        self.revision += 1
        try:
            self.save()
        except Exception:
            self.history.pop()
            self.archive.pop()
            self.revision -= 1
            raise
        return item

    @serialized_session_write
    def replace(self, history, anchor_id=None):
        previous = (
            self.history,
            self.revision,
            self.anchor_id,
            self.anchor_token_count,
            self.tail_start,
            self.context_usage,
        )
        self.history = list(history)
        self.revision += 1
        self.anchor_id = anchor_id
        self.anchor_token_count = None
        self.tail_start = len(self.history)
        self.context_usage = None
        try:
            self.save()
        except Exception:
            (
                self.history,
                self.revision,
                self.anchor_id,
                self.anchor_token_count,
                self.tail_start,
                self.context_usage,
            ) = previous
            raise
        return self.history

    @serialized_session_write
    def commit_compaction(
        self,
        history,
        *,
        anchor_id,
        archive_anchor_limit,
        anchor_token_count=None,
    ):
        if archive_anchor_limit <= 0:
            raise ValueError("compaction.archive_anchor_limit must be positive")
        previous = (
            self.history,
            self.archive,
            self.revision,
            self.anchor_id,
            self.anchor_token_count,
            self.tail_start,
            self.context_usage,
        )
        history = list(history)
        archive = [*self.archive, *history]
        anchor_indexes = [
            index
            for index, item in enumerate(archive)
            if isinstance(item, CompactionItem)
        ]
        if len(anchor_indexes) > archive_anchor_limit:
            archive = archive[anchor_indexes[-archive_anchor_limit]:]
        self.history = history
        self.archive = archive
        self.revision += 1
        self.anchor_id = anchor_id
        self.anchor_token_count = anchor_token_count
        self.tail_start = len(history)
        self.context_usage = None
        try:
            self.save()
        except Exception:
            (
                self.history,
                self.archive,
                self.revision,
                self.anchor_id,
                self.anchor_token_count,
                self.tail_start,
                self.context_usage,
            ) = previous
            raise
        return self.history

    @serialized_session_write
    def set_context_usage(self, usage):
        previous = self.context_usage
        self.context_usage = usage
        try:
            self.save()
        except Exception:
            self.context_usage = previous
            raise

    def command_status(self, command_id):
        receipt = self.commands.get(command_id)
        return receipt.status if receipt else None

    @serialized_session_write
    def set_command(self, command_id, status, error=None):
        previous = self.commands.copy()
        self.commands[command_id] = CommandReceipt(status=status, error=error)
        try:
            self.save()
        except Exception:
            self.commands = previous
            raise

    @serialized_session_write
    def discard_commands(self, *, prefixes=()):
        """Remove obsolete technical receipts without touching conversation data."""
        prefixes = tuple(str(prefix) for prefix in prefixes if str(prefix))
        if not prefixes:
            return 0
        retained = {
            command_id: receipt
            for command_id, receipt in self.commands.items()
            if not command_id.startswith(prefixes)
        }
        removed = len(self.commands) - len(retained)
        if not removed:
            return 0
        previous = self.commands.copy()
        self.commands = retained
        try:
            self.save()
        except Exception:
            self.commands = previous
            raise
        return removed

    @serialized_session_write
    def set_plugin(self, name, activated):
        if self.plugins.get(name) is activated:
            return self
        previous = self.plugins.copy()
        self.plugins[name] = activated
        try:
            self.save()
        except Exception:
            self.plugins = previous
            raise

    @serialized_session_write
    def clear_context(self):
        previous = (
            self.history, self.archive, self.revision, self.anchor_id,
            self.anchor_token_count, self.tail_start, self.context_usage,
        )
        self.history = []
        self.archive = []
        self.revision += 1
        self.anchor_id = None
        self.anchor_token_count = None
        self.tail_start = 0
        self.context_usage = None
        try:
            self.save()
        except Exception:
            (
                self.history, self.archive, self.revision, self.anchor_id,
                self.anchor_token_count, self.tail_start, self.context_usage,
            ) = previous
            raise

    @serialized_session_write
    def recover_commands(self):
        previous = self.commands.copy()
        interrupted = False
        for command_id, receipt in self.commands.items():
            if receipt.status in {"queued", "active"}:
                self.commands[command_id] = CommandReceipt(status="interrupted")
                interrupted = True
        if interrupted:
            try:
                self.save()
            except Exception:
                self.commands = previous
                raise
