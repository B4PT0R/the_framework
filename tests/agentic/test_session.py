import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pytest

from core.agent.models.content import InputText
from core.agent.models.usage import ResponseUsage
from core.agent.models.responses import (
    CommandOutput,
    CompactionSummary,
    FunctionCall,
    FunctionCallOutput,
    Image,
    MediaReference,
    Message,
    ToolOutput,
)
from core.agent.context.session import Session


def test_session_round_trip(tmp_path):
    path = tmp_path / "session.json"
    session = Session.open(path)

    session.append(Message(role="developer", kind="skill", content=[]))
    restored = Session.open(path)

    assert restored.id == session.id
    assert isinstance(restored.history[0], Message)
    assert restored.history[0].kind == "skill"
    assert restored.archive[0].id == restored.history[0].id
    assert restored.watermark() == {
        "session_id": session.id,
        "revision": 1,
        "anchor_id": None,
        "tail_start": 0,
    }


def test_concurrent_session_writes_are_serialized_and_use_unique_temporaries(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "session.json"
    session = Session.open(path)
    sources = []
    sources_lock = Lock()
    replace = os.replace

    def slow_replace(source, destination):
        with sources_lock:
            sources.append(str(source))
        time.sleep(0.002)
        replace(source, destination)

    monkeypatch.setattr(os, "replace", slow_replace)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(session.set_command, f"command-{index}", "queued")
            for index in range(16)
        ]
        for future in futures:
            future.result()

    restored = Session.open(path)
    assert len(sources) == 16
    assert len(set(sources)) == 16
    assert set(restored.commands) == {f"command-{index}" for index in range(16)}


def test_archive_pagination_uses_whole_conversational_turns():
    session = Session()
    for index in range(4):
        session.archive.extend([
            Message(id=f"user-{index}", role="user", content=[]),
            Message(id=f"assistant-{index}", role="assistant", content=[]),
            ToolOutput(
                id=f"tool-{index}",
                role="developer",
                call_id=f"call-{index}",
                content=[],
            ),
        ])

    latest = session.page_archive_turns(limit=2)
    assert [[item.id for item in turn] for turn in latest["turns"]] == [
        ["user-2", "assistant-2", "tool-2"],
        ["user-3", "assistant-3", "tool-3"],
    ]
    assert latest["next_before"] == "user-2"
    assert latest["has_more"] is True

    older = session.page_archive_turns(before=latest["next_before"], limit=2)
    assert [[item.id for item in turn] for turn in older["turns"]] == [
        ["user-0", "assistant-0", "tool-0"],
        ["user-1", "assistant-1", "tool-1"],
    ]
    assert older["next_before"] is None
    assert older["has_more"] is False


def test_context_usage_round_trip(tmp_path):
    path = tmp_path / "session.json"
    session = Session.open(path)
    session.set_context_usage(ResponseUsage(
        input_tokens=123,
        output_tokens=45,
        total_tokens=168,
        input_tokens_details={"cached_tokens": 100, "cache_write_tokens": 20},
    ))

    restored = Session.open(path)

    assert isinstance(restored.context_usage, ResponseUsage)
    assert restored.context_usage.input_tokens == 123
    assert restored.context_usage.input_tokens_details.cached_tokens == 100
    assert restored.context_usage.input_tokens_details.cache_write_tokens == 20


def test_replace_keeps_previous_history_when_write_fails(tmp_path, monkeypatch):
    session = Session(history=[Message(role="user", content=[])])
    session.set_attr("path", tmp_path / "session.json")
    previous = session.history

    monkeypatch.setattr(json, "dump", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))

    with pytest.raises(OSError):
        session.replace([Message(role="assistant", content=[])])

    assert session.history is previous
    assert session.revision == 0


def test_replace_changes_active_context_without_erasing_local_archive():
    message = Message(role="user", content=[])
    session = Session(history=[message], archive=[message])

    session.replace([Message(role="assistant", content=[])], anchor_id="anchor")

    assert session.history[0].role == "assistant"
    assert [item.id for item in session.archive] == [message.id]


def test_compaction_archive_retains_only_the_configured_anchor_epochs():
    session = Session()

    for index in range(12):
        session.append(Message(role="user", content=[]))
        anchor = CompactionSummary(encrypted_content=f"anchor-{index}")
        session.commit_compaction(
            [anchor],
            anchor_id=f"response-{index}",
            archive_anchor_limit=10,
        )

    anchors = [
        item.encrypted_content
        for item in session.archive
        if isinstance(item, CompactionSummary)
    ]
    assert anchors == [f"anchor-{index}" for index in range(2, 12)]
    assert session.archive[0].encrypted_content == "anchor-2"
    assert session.archive[-1].encrypted_content == "anchor-11"


def test_compaction_archive_limit_must_be_positive():
    session = Session()

    with pytest.raises(ValueError, match="archive_anchor_limit"):
        session.commit_compaction(
            [CompactionSummary(encrypted_content="anchor")],
            anchor_id="response",
            archive_anchor_limit=0,
        )


def test_compaction_archive_pruning_rolls_back_when_write_fails(tmp_path, monkeypatch):
    message = Message(role="user", content=[])
    session = Session(history=[message], archive=[message])
    session.set_attr("path", tmp_path / "session.json")
    previous_history = session.history
    previous_archive = session.archive
    monkeypatch.setattr(
        json,
        "dump",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError()),
    )

    with pytest.raises(OSError):
        session.commit_compaction(
            [CompactionSummary(encrypted_content="anchor")],
            anchor_id="response",
            archive_anchor_limit=10,
        )

    assert session.history is previous_history
    assert session.archive is previous_archive
    assert session.anchor_id is None
    assert session.revision == 0


def test_recovery_marks_unfinished_commands_as_interrupted(tmp_path):
    path = tmp_path / "session.json"
    session = Session.open(path)
    session.set_command("command", "active")

    restored = Session.open(path)
    restored.recover_commands()

    assert restored.command_status("command") == "interrupted"
    assert Session.open(path).command_status("command") == "interrupted"


def test_obsolete_technical_command_receipts_can_be_discarded_atomically(tmp_path):
    path = tmp_path / "session.json"
    session = Session.open(path)
    session.set_command("haptic-trace-runtime-1", "completed")
    session.set_command("conversation-1", "completed")

    assert session.discard_commands(prefixes=("haptic-trace-",)) == 1
    assert set(session.commands) == {"conversation-1"}
    assert set(Session.open(path).commands) == {"conversation-1"}
    assert session.discard_commands(prefixes=("haptic-trace-",)) == 0


def test_setting_an_unchanged_plugin_state_does_not_save(monkeypatch):
    session = Session(plugins={"haptics": True})
    saves = []
    monkeypatch.setattr(Session, "save", lambda self: saves.append(dict(self.plugins)))

    session.set_plugin("haptics", True)
    session.set_plugin("haptics", False)

    assert saves == [{"haptics": False}]


def test_plugin_activation_state_survives_session_reopen(tmp_path):
    path = tmp_path / "session.json"
    session = Session.open(path)
    session.set_plugin("haptics", True)
    session.set_plugin("memory", False)

    assert Session.open(path).plugins == {
        "haptics": True,
        "memory": False,
    }


def test_tool_output_round_trip_preserves_its_subtype(tmp_path):
    path = tmp_path / "session.json"
    session = Session.open(path)
    message = ToolOutput.from_output(
        name="lookup",
        call_id="call-1",
        output={"value": 42},
    )
    session.append(message)

    restored = Session.open(path).history[0]
    assert isinstance(restored, ToolOutput)
    assert restored.type == "message"
    assert restored.kind == "tool_output"
    assert restored.call_id == "call-1"


def test_command_output_round_trip_preserves_command_id(tmp_path):
    path = tmp_path / "session.json"
    session = Session.open(path)
    output = CommandOutput.from_output(
        name="inspect",
        command_id="command-1",
        output={"value": 42},
    )
    session.append(output)

    restored = Session.open(path).history[0]
    assert isinstance(restored, CommandOutput)
    assert restored.type == "message"
    assert restored.kind == "command_output"
    assert restored.command_id == "command-1"


def test_sanitize_repairs_interrupted_parallel_calls_before_the_next_user(tmp_path):
    path = tmp_path / "session.json"
    session = Session.open(path)
    session.append(Message(role="user", content=[]))
    session.append(FunctionCall(
        id="function-1",
        name="command",
        namespace="bash",
        arguments="{}",
        call_id="call-1",
    ))
    session.append(FunctionCall(
        id="function-2",
        name="command",
        namespace="bash",
        arguments="{}",
        call_id="call-2",
    ))
    session.append(Message(role="user", content=[]))

    result = session.sanitize()

    assert result == {
        "changed": True,
        "repaired_function_calls": ["call-1", "call-2"],
    }
    assert [item.type for item in session.history] == [
        "message",
        "function_call",
        "function_call",
        "function_call_output",
        "function_call_output",
        "message",
    ]
    outputs = session.history[3:5]
    assert [output.call_id for output in outputs] == ["call-1", "call-2"]
    assert all(json.loads(output.output)["status"] == "failure" for output in outputs)
    assert all("not automatically retried" in output.output for output in outputs)
    assert [item.type for item in session.archive] == [
        item.type for item in session.history
    ]
    assert Session.open(path).sanitize()["changed"] is False


def test_to_api_format_sanitizes_the_canonical_session_before_projection():
    session = Session(history=[FunctionCall(
        name="command",
        arguments="{}",
        call_id="orphan",
    )])

    projected = session.to_api_format()

    assert [item["type"] for item in projected] == [
        "function_call",
        "function_call_output",
    ]
    assert projected[1]["call_id"] == "orphan"


def test_to_api_format_projects_video_reference_as_supported_message(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    session = Session(history=[MediaReference(
        path=str(path),
        title="Shared clip",
        media_kind="video",
        mime_type="video/mp4",
    )])

    assert session.to_api_format() == [{
        "type": "message",
        "role": "developer",
        "content": [{
            "type": "input_text",
            "text": (
                    f'<mentioned_media title="Shared clip" kind="video" '
                    f'mime_type="video/mp4" rating="unrated" path="{path}">'
                "Selected by the user for the shared conversation."
                "</mentioned_media>"
            ),
        }],
    }]


def test_media_reference_distinguishes_a_rating_from_unrated(tmp_path):
    path = tmp_path / "rated.png"
    path.write_bytes(b"image")
    reference = MediaReference(
        path=str(path), title="Rated image", media_kind="image",
        mime_type="image/png", rating=4,
    )

    assert 'rating="4/5"' in reference.to_api_format()["content"][1]["text"]


def test_missing_image_is_not_added_and_stale_image_is_pruned_atomically(tmp_path):
    session = Session.open(tmp_path / "session.json")
    missing = Image(path=str(tmp_path / "missing.png"), description="Missing")

    assert session.append(missing) is None
    assert session.history == []
    assert session.archive == []
    assert session.revision == 0

    path = tmp_path / "available.png"
    path.write_bytes(b"image")
    image = Image(path=str(path), description="Available")
    assert session.append(image) is image
    path.unlink()

    assert session.sanitize() == {
        "changed": True,
        "repaired_function_calls": [],
    }
    assert session.history == []
    assert session.archive == []
    assert Session.open(session.path).history == []


def test_missing_copied_file_message_is_not_added_and_is_pruned_if_deleted(tmp_path):
    path = tmp_path / "attachment.txt"
    xml = lambda: InputText(text=(
        f'<copied_files destination="{tmp_path}" count="1">\n'
        f'  <file name="attachment.txt" path="{path}" size_bytes="4" />\n'
        "</copied_files>"
    ))
    session = Session.open(tmp_path / "session.json")

    missing = Message(role="developer", kind="copied_files", content=[xml()])
    assert session.append(missing) is None

    path.write_text("data")
    attached = Message(role="developer", kind="copied_files", content=[xml()])
    assert session.append(attached) is attached
    path.unlink()

    assert session.sanitize()["changed"] is True
    assert session.history == []
    assert session.archive == []


def test_sanitize_does_not_retry_or_duplicate_a_completed_call():
    call = FunctionCall(name="command", arguments="{}", call_id="complete")
    output = FunctionCallOutput(call_id="complete", output='{"status":"success"}')
    session = Session(history=[call, output], archive=[call, output])

    assert session.sanitize() == {
        "changed": False,
        "repaired_function_calls": [],
    }
    assert session.history == [call, output]


def test_sanitize_rolls_back_all_state_when_persistence_fails(tmp_path, monkeypatch):
    call = FunctionCall(name="command", arguments="{}", call_id="orphan")
    session = Session(history=[call], archive=[call], revision=7)
    session.set_attr("path", tmp_path / "session.json")
    previous_history = list(session.history)
    previous_archive = list(session.archive)
    monkeypatch.setattr(
        json,
        "dump",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        session.sanitize()

    assert session.history == previous_history
    assert session.archive == previous_archive
    assert session.revision == 7
