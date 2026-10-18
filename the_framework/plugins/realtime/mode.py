"""Canonical voice-mode boundaries shared by text and voice projections."""

from ...agent.models.content import InputText
from ...agent.models.responses import Message


def voice_mode_marker(session_id, state):
    if state == "started":
        text = (
            "Voice session started. The user is now interacting through realtime "
            "audio. The Voice agent speaks to the user; the Text agent handles "
            "delegated work."
        )
    elif state == "ended":
        text = (
            "Voice session ended. Realtime audio is no longer active. Subsequent "
            "interaction uses text unless a later voice-session start marker "
            "indicates otherwise."
        )
    else:
        raise ValueError("voice mode state must be started or ended")
    return Message(
        id=f"realtime-mode:{session_id}:{state}", role="developer", kind="realtime_mode",
        voice_session_id=session_id, voice_state=state,
        content=[InputText(text=f"{text} This is application context, not a user request.")],
    )
