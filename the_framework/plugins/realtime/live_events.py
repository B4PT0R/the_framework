"""Normalize live sideband signals before any publication to application clients."""
from .events import VoiceError, VoiceResponseState, VoiceSpeechState, VoiceTranscriptUpdated, VoiceTurnCompleted


class LiveEvents:
    def __init__(self):
        self.transcripts = {}

    def convert(self, event):
        kind = event.get("type")
        roles = {"input_transcript.added": "user", "output_transcript.added": "assistant"}
        if kind in roles:
            role = roles[kind]
            text = (event.get("item") or {}).get("text")
            if not isinstance(text, str) or not text:
                return ()
            current = self.transcripts.get(role, "")
            updated = text if text.startswith(current) else current if current.endswith(text) else current + text
            if updated == current:
                return ()
            self.transcripts[role] = updated
            return (VoiceTranscriptUpdated(role=role, text=updated),)
        if kind == "turn.done":
            turn = event.get("turn") or {}
            role = turn.get("role")
            if role not in {"user", "assistant"}:
                return ()
            self.transcripts.pop(role, None)
            return (VoiceTurnCompleted(role=role, turn_id=turn.get("id"),
                                      transcript=str(turn.get("transcript") or "").strip(),
                                      interrupted=bool(turn.get("interrupted", False))),)
        states = {"response.created": "started", "response.in_progress": "started", "response.done": "completed",
                  "response.failed": "failed", "response.cancelled": "cancelled", "response.incomplete": "incomplete"}
        if kind in states:
            return (VoiceResponseState(status=states[kind]),)
        if kind in {"input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped"}:
            return (VoiceSpeechState(status="started" if kind.endswith("started") else "ended"),)
        if kind == "error":
            error = event.get("error") or {}
            return (VoiceError(message=str(error.get("message") or "Voice backend failed"), code=error.get("code")),)
        return ()
