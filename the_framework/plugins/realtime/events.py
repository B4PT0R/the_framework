"""Public voice events; independent of the live backend wire protocol."""
from typing import Literal
from ...agent.models.lifecycle import LocalEvent


class VoiceTranscriptUpdated(LocalEvent):
    type: str = "voice.transcript.updated"
    role: Literal["user", "assistant"]
    text: str


class VoiceTurnCompleted(LocalEvent):
    type: str = "voice.turn.completed"
    role: Literal["user", "assistant"]
    turn_id: str | None = None
    message_id: str | None = None
    transcript: str = ""
    interrupted: bool = False


class VoiceResponseState(LocalEvent):
    type: str = "voice.response.state"
    status: Literal["started", "completed", "failed", "cancelled", "incomplete"]


class VoiceSpeechState(LocalEvent):
    type: str = "voice.speech.state"
    status: Literal["started", "ended"]


class VoiceError(LocalEvent):
    type: str = "voice.error"
    message: str
    code: str | None = None


class RealtimeDelegationUpdate(LocalEvent):
    call_id: str
    delegation_id: str
    command_id: str
    state: Literal["started", "progress", "completed", "failed"]


class RealtimeReconnectRequested(LocalEvent):
    """Ask the owning client to renegotiate after canonical history has drained."""
    call_id: str
    error: str | None = None


class RealtimeRestarting(LocalEvent):
    """The owning client should retain voice intent until the server returns."""
    call_id: str


class RealtimeControlRequested(LocalEvent):
    """A client action: starts target the primary transport, stops a matching call."""
    action: Literal["start", "stop"]
    call_id: str | None = None


class RealtimeControlFailed(LocalEvent):
    action: Literal["start", "stop"]
    error: str
