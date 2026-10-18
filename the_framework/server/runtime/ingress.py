"""Transport-independent prompt submission under the text/voice barrier."""

import asyncio

from ...agent.runtime.protocol import PromptRequest


class ChatIngress:
    """Route client prompts without racing voice activation or cleanup.

    ``voice`` is the installed realtime service, whose lock also protects call
    transitions. Receipts, authorization and wire responses belong to callers.
    """

    def __init__(self, runtime, voice=None, *, text_lock=None):
        self.runtime = runtime
        self.voice = voice
        self.lock = voice.lock if voice is not None else (
            text_lock if text_lock is not None else asyncio.Lock()
        )

    async def submit(self, command: PromptRequest) -> dict:
        """Accept one prompt and return its actual id and selected route.

        Voice accepts ordinary user messages only; structured or customized
        turns must not silently lose their input items or text-only semantics.
        """
        async with self.lock:
            if self.voice is not None and self.voice.controller.active:
                if (
                    not command.append_prompt or command.input_items
                    or command.prompt_role != "user" or command.prompt_kind != "message"
                    or command.response_kind != "message"
                    or command.reasoning_effort is not None
                ):
                    raise ValueError("This turn requires text mode; stop voice before submitting it")
                try:
                    turn_id = await self.voice.controller.send_text(command.prompt)
                except RuntimeError as error:
                    raise ValueError(str(error)) from error
                return {"id": turn_id, "route": "realtime"}
            await self.runtime.submit(command)
            return {"id": command.id, "route": "text"}
