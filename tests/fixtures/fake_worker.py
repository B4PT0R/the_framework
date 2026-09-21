import argparse
import json
import sys


parser = argparse.ArgumentParser()
parser.add_argument("--hang-on-shutdown", action="store_true")
parser.add_argument("--exit-after-ready", action="store_true")
parser.add_argument("--malformed-after-ready", action="store_true")
parser.add_argument("--large-session-snapshot", action="store_true")
args = parser.parse_args()


def send(payload):
    print(json.dumps(payload, separators=(",", ":")), flush=True)


send({"type": "worker_ready", "session_id": "test", "protocol": 1})
pending_application = {}
config = {"model": "gpt-test", "reasoning": {"effort": "medium"}}
state = {"succubus_satiety": {}}
if args.exit_after_ready:
    raise SystemExit(3)
if args.malformed_after_ready:
    print("{malformed", flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "prompt_request":
        command = {
            "id": request["id"],
            "prompt": request["prompt"],
            "priority": request.get("priority", 10),
            "status": "completed",
        }
        send({
            "type": "command_accepted",
            "request_id": request["id"],
            "command": command,
        })
        if request["prompt"] == "application":
            call_id = f'{request["id"]}-call'
            pending_application[call_id] = command
            send({
                "type": "application_call",
                "request_id": call_id,
                "command_id": request["id"],
                "request": {
                    "id": call_id,
                    "capability": "example",
                    "method": "echo",
                    "payload": {"value": "hello"},
                    "timeout_ms": 30000,
                },
            })
            continue
        send({
            "type": "command_completed",
            "request_id": request["id"],
            "command": command,
        })
    if request["type"] == "external_event_request":
        command = {
            "id": request["id"],
            "name": request["name"],
            "payload": request.get("payload", {}),
            "priority": request.get("priority", 10),
            "status": "completed",
        }
        send({
            "type": "command_accepted",
            "request_id": request["id"],
            "command": command,
        })
        send({
            "type": "command_completed",
            "request_id": request["id"],
            "command": command,
        })
    if request["type"] == "transient_event_request":
        send({
            "type": "transient_event_completed",
            "request_id": request["id"],
            "result": None,
        })
    if request["type"] == "application_result":
        command = pending_application.pop(request["id"], None)
        if command is not None:
            send({
                "type": "command_completed",
                "request_id": command["id"],
                "command": command,
            })
    if request["type"] == "status_request":
        send({
            "type": "worker_status",
            "request_id": request["id"],
            "active": None,
            "pending": 0,
            "local_input_tokens": 48000,
            "context_token_limit": 200000,
            "context_saturation": 0.24,
            "quota_5h": {
                "used_percent": 12,
                "limit_window_seconds": 18000,
                "reset_at": 1800000000,
            },
            "quota_7d": {
                "used_percent": 36,
                "limit_window_seconds": 604800,
                "reset_at": 1800001000,
            },
            "quota_updated_at": 1799999000,
        })
    if request["type"] == "compact_request":
        command = {
            "id": request["id"],
            "priority": request.get("priority", 10),
            "status": "completed",
        }
        send({
            "type": "command_accepted",
            "request_id": request["id"],
            "command": command,
        })
        send({
            "type": "command_completed",
            "request_id": request["id"],
            "command": command,
        })
    if request["type"] == "session_snapshot_request":
        send({
            "type": "session_snapshot",
            "request_id": request["id"],
            "watermark": {
                "session_id": "test",
                "revision": 2,
                "anchor_id": None,
                "tail_start": 0,
            },
            "items": [
                {
                    "type": "message",
                    "id": "user-message",
                    "role": "user",
                    "kind": "message",
                    "content": [{"type": "input_text", "text": "Bonjour"}],
                },
                {
                    "type": "message",
                    "id": "assistant-message",
                    "role": "assistant",
                    "kind": "message",
                    "content": [{"type": "output_text", "text": "Salut"}],
                },
            ],
            "instructions": "x" * 128_000 if args.large_session_snapshot else "Be helpful.",
            "extensions": {
                "realtime": {
                    "protocol": "codex-realtime-v3",
                    "model": "gpt-live-1-codex",
                    "session": {
                        "model": "gpt-live-1-codex",
                        "instructions": "Speak naturally.",
                    },
                },
            },
        })
    if request["type"] == "session_page_request":
        send({
            "type": "session_page",
            "request_id": request["id"],
            "watermark": {
                "session_id": "test",
                "revision": 2,
                "anchor_id": None,
                "tail_start": 0,
            },
            "turns": [{
                "id": "user-message",
                "items": [
                    {
                        "type": "message",
                        "id": "user-message",
                        "role": "user",
                        "kind": "message",
                        "content": [{"type": "input_text", "text": "Bonjour"}],
                    },
                    {
                        "type": "message",
                        "id": "assistant-message",
                        "role": "assistant",
                        "kind": "message",
                        "content": [{"type": "output_text", "text": "Salut"}],
                    },
                ],
            }],
            "next_before": None,
            "has_more": False,
        })
    if request["type"] == "config_snapshot_request":
        send({
            "type": "config_snapshot",
            "request_id": request["id"],
            "config": config,
        })
    if request["type"] == "state_snapshot_request":
        send({
            "type": "state_snapshot",
            "request_id": request["id"],
            "state": state,
        })
    if request["type"] == "config_update_request":
        config.update(request["updates"])
        command = {
            "id": request["id"],
            "updates": request["updates"],
            "priority": request.get("priority", 10),
            "status": "completed",
        }
        send({
            "type": "command_accepted",
            "request_id": request["id"],
            "command": command,
        })
        send({
            "type": "command_event",
            "request_id": request["id"],
            "command_id": request["id"],
            "event": {
                "type": "agent.config.updated",
                "config": config,
                "restart_required": "memory" in request["updates"],
            },
        })
        send({
            "type": "command_completed",
            "request_id": request["id"],
            "command": command,
        })
    if request["type"] == "shutdown_request" and not args.hang_on_shutdown:
        send({"type": "worker_stopped"})
        break
