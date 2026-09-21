import asyncio
import inspect
import json
import time
from collections import deque
from threading import Condition, Lock
from threading import Event as ThreadEvent

import requests
from modict import modict

GENERATION_MAX_ATTEMPTS = 4
GENERATION_RETRY_BASE_DELAY = 0.5


def retryable_generation_error(error):
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(error, requests.HTTPError):
        status = getattr(getattr(error, "response", None), "status_code", None)
        return status == 429 or (isinstance(status, int) and 500 <= status < 600)
    return (
        isinstance(error, AttributeError)
        and "'NoneType' object has no attribute 'read'" in str(error)
    )


class MissingResponseStream(RuntimeError):
    pass


class GenerationUnavailable(RuntimeError):
    pass


class StreamGenerationError(RuntimeError):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


_STREAM_LIFECYCLE_EVENTS = {
    "response.queued",
    "response.created",
    "response.in_progress",
}


def event_field(payload, name, default=None):
    if isinstance(payload, dict):
        return payload.get(name, default)
    return getattr(payload, name, default)


def streamed_generation_error(payload):
    event_type = event_field(payload, "type")
    details = None
    if event_type == "error":
        details = event_field(payload, "error")
    elif event_type == "response.failed":
        response = event_field(payload, "response")
        details = event_field(response, "error")
    if details is None:
        return None
    code = str(
        event_field(details, "code")
        or event_field(details, "type")
        or "response_failed"
    )
    message = str(
        event_field(details, "message")
        or "The response stream failed"
    )
    retryable = (
        code in {
            "server_is_overloaded",
            "rate_limit_exceeded",
            "service_unavailable",
            "timeout",
        }
        or str(event_field(details, "type") or "") == "service_unavailable_error"
    )
    return StreamGenerationError(f"{code}: {message}", retryable=retryable)


from ..models.events import Event, ResponseCompleted, ResponseOutputItemDone
from ..models.lifecycle import (
    AgentGenerationEnd,
    AgentGenerationStart,
    AgentInterrupted,
    AgentStepEnd,
    AgentStepStart,
    AgentToolCallEnd,
    AgentToolCallsEnd,
    AgentToolCallsStart,
    AgentToolCallStart,
    AgentTurnEnd,
    AgentTurnStart,
)
from ..context.caching import resolve_prompt_cache_key
from ..models.responses import (
    CommandOutput,
    FunctionCall,
    FunctionCallOutput,
    Image,
    ToolOutput,
    ToolSearchCall,
    ToolSearchOutput,
)
from ..extensions.tools import USER_FEEDBACK_PARAMETER
from harness_core.utils.ids import timestamp_id


class PendingSteering(modict):
    _config = modict.config(strict=True, extra="forbid", auto_convert=False)

    prompt: str
    append_prompt: bool = True
    prompt_role: str = "user"
    prompt_kind: str = "message"
    reasoning_effort: str | None = None
    input_items: tuple = ()
    future: asyncio.Future | None = None


class AgenticLoop:
    def __init__(self, agent):
        self.agent = agent
        self.end_turn = False
        self.request_next_step = False
        self.tool_calls = []
        self.stream = None
        self.retry_wake = ThreadEvent()
        self.pending = deque()
        self.pending_lock = Lock()
        self.activity_condition = Condition()
        self.activity_sequence = 0
        self.reasoning_effort_override = None

    @property
    def pending_count(self):
        with self.pending_lock:
            return len(self.pending)

    def can_steer(self):
        return not self.end_turn and not getattr(
            self.agent,
            "end_of_turn_requested",
            False,
        )

    def queue_steering(
        self,
        prompt,
        *,
        append_prompt=True,
        prompt_role="user",
        prompt_kind="message",
        reasoning_effort=None,
        input_items=(),
        future=None,
    ):
        if prompt_role not in {"user", "developer"}:
            raise ValueError("prompt_role must be user or developer")
        steering = PendingSteering(
            prompt=prompt,
            append_prompt=append_prompt,
            prompt_role=prompt_role,
            prompt_kind=prompt_kind,
            reasoning_effort=reasoning_effort,
            input_items=tuple(input_items),
            future=future,
        )
        with self.pending_lock:
            self.pending.append(steering)
        self._signal_activity()
        return steering

    def _signal_activity(self):
        """Wake tools idling until the active turn receives new work."""
        with self.activity_condition:
            self.activity_sequence += 1
            self.activity_condition.notify_all()

    def wait_for_activity(self, timeout_seconds):
        """Wait without blocking worker IPC, returning timeout, steering, or interrupt."""
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative")
        deadline = time.monotonic() + timeout_seconds
        while True:
            if self.end_turn:
                return "interrupt"
            if self.pending_count:
                return "steering"

            # Capture the generation before checking state again. This avoids
            # losing a signal delivered between the state check and wait.
            with self.activity_condition:
                sequence = self.activity_sequence
            if self.end_turn:
                return "interrupt"
            if self.pending_count:
                return "steering"

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout"
            with self.activity_condition:
                if self.activity_sequence != sequence:
                    continue
                self.activity_condition.wait(timeout=remaining)

    def checkpoint(self, *, request_model_step=True):
        with self.pending_lock:
            pending = tuple(self.pending)
            self.pending.clear()
        for steering in pending:
            try:
                for item in steering.input_items:
                    self.agent.add_response_item(item)
                prompt = self.agent.registry("hooks").run(
                    "before_turn",
                    steering.prompt,
                )
                if prompt and steering.append_prompt:
                    if steering.prompt_kind == "message":
                        self.agent.add_message(steering.prompt_role, prompt)
                    else:
                        self.agent.add_message(
                            steering.prompt_role,
                            prompt,
                            kind=steering.prompt_kind,
                        )
                if request_model_step:
                    self.request_next_step = True
                if steering.reasoning_effort is not None:
                    self.reasoning_effort_override = steering.reasoning_effort
                if steering.future is not None and not steering.future.done():
                    steering.future.set_result(None)
            except Exception as error:
                if steering.future is not None and not steering.future.done():
                    steering.future.set_exception(error)
        return pending

    def interrupt(self, reason=None):
        self.end_turn = True
        self.retry_wake.set()
        self._signal_activity()
        if self.stream is not None and hasattr(self.stream, "close"):
            try:
                self.stream.close()
            except Exception:
                # Some streaming transports do not support being closed from the
                # event-loop thread while their iterator is active in a worker.
                pass
        return self.agent.emit(AgentInterrupted(reason=reason))

    def handle(self, event):
        if isinstance(event, ResponseCompleted):
            self.agent.context.record_response_usage(event.response)
        if isinstance(event, ResponseOutputItemDone):
            if self.should_resolve(event.item):
                self.tool_calls.append(event.item)
            return self.agent.add_response_item(event.item)
        return None

    def should_resolve(self, item):
        return (
            isinstance(item, FunctionCall)
            or isinstance(item, ToolSearchCall)
            and item.execution == "client"
        )

    def _generation_payload(self, input_items):
        config = self.agent.configs.root().exclude(
            "compaction",
            "instructions",
            "max_input_images",
            "max_tool_output_tokens",
        )
        config["prompt_cache_key"] = resolve_prompt_cache_key(
            self.agent.session.id,
            config.get("prompt_cache_key"),
        )
        payload = self.agent.registry("hooks").run(
            "before_step",
            {
                **config,
                "instructions": self.agent.context.instructions(),
                "tools": self.agent.registry("tools").list(),
                "input": input_items,
                "stream": True,
            },
        )
        if self.reasoning_effort_override is not None:
            payload["reasoning"] = {"effort": self.reasoning_effort_override}
        return payload

    def generation_payload(self):
        return self._generation_payload(self.agent.context.input())

    async def ageneration_payload(self, provider_outputs=None):
        return self._generation_payload(
            await self.agent.context.ainput(provider_outputs)
        )

    def generation(self, payload=None):
        self.tool_calls = []
        self.request_next_step = False
        self.agent.emit(AgentGenerationStart())
        payload = payload or self.generation_payload()

        try:
            for attempt in range(GENERATION_MAX_ATTEMPTS):
                received_event = False
                received_output = False
                if self.end_turn:
                    return
                try:
                    events = self.agent.client.responses.create(**payload)
                    if events is None:
                        raise MissingResponseStream("OpenAI returned no response stream")
                    self.stream = events
                    for event_payload in events:
                        received_event = True
                        if self.end_turn:
                            break
                        failure = streamed_generation_error(event_payload)
                        if failure is not None:
                            raise failure
                        if event_field(event_payload, "type") not in _STREAM_LIFECYCLE_EVENTS:
                            received_output = True
                        event = Event.from_dict(event_payload)
                        event = self.agent.registry("hooks").run("on_event", event)
                        self.handle(event)
                        self.agent.emit(event)
                    return
                except Exception as error:
                    retryable = (
                        isinstance(error, MissingResponseStream)
                        or retryable_generation_error(error)
                        or isinstance(error, StreamGenerationError) and error.retryable
                    )
                    safe_stream_retry = (
                        isinstance(error, StreamGenerationError)
                        and error.retryable
                        and not received_output
                    )
                    if (received_event and not safe_stream_retry) or not retryable:
                        raise
                    if attempt + 1 >= GENERATION_MAX_ATTEMPTS:
                        raise GenerationUnavailable(
                            f"OpenAI generation unavailable after {GENERATION_MAX_ATTEMPTS} attempts: {error}"
                        ) from error
                    delay = GENERATION_RETRY_BASE_DELAY * (2 ** attempt)
                    if self.retry_wake.wait(delay) or self.end_turn:
                        return
                finally:
                    self.stream = None
        finally:
            self.agent.emit(AgentGenerationEnd())

    async def step(self):
        self.agent.emit(AgentStepStart())
        provider_outputs = await self.agent.registry("providers").aoutputs(
            channel="text"
        )
        payload = await self.ageneration_payload(provider_outputs)
        if self.agent.context.needs_compaction(payload):
            await asyncio.to_thread(self.agent.compaction.run)
            payload = await self.ageneration_payload(provider_outputs)
            self.agent.context.local_input_tokens(payload)
        await asyncio.to_thread(self.generation, payload)
        if self.tool_calls:
            await self.run_tool_calls(self.tool_calls)
            self.request_next_step = not self.end_turn and not getattr(
                self.agent,
                "end_of_turn_requested",
                False,
            )
        self.checkpoint(request_model_step=not self.end_turn)
        self.agent.emit(AgentStepEnd())

    async def tool_output(self, tool_call):
        if isinstance(tool_call, ToolSearchCall):
            return self.tool_search_output(tool_call)
        error_message = None
        try:
            arguments = json.loads(tool_call.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("function tool arguments must be a JSON object")
            user_feedback = arguments.pop(USER_FEEDBACK_PARAMETER, None)
            if not isinstance(user_feedback, str) or not user_feedback.strip():
                raise ValueError(
                    "missing required user_feedback: provide a brief user-facing explanation "
                    "of the function call"
                )
            tool = self.agent.registry("tools").resolve(
                tool_call.name,
                tool_call.get("namespace"),
            )
            if inspect.iscoroutinefunction(tool.function):
                result = await tool(**arguments)
            else:
                result = await asyncio.to_thread(tool, **arguments)
                if inspect.isawaitable(result):
                    result = await result
            status = "success"
        except Exception as error:
            error_message = str(error)
            result = {"error": error_message}
            status = "failure"
        items = result if isinstance(result, tuple) else (result,)
        output_items = []
        try:
            for item in items:
                if item is None:
                    continue
                if isinstance(item, ToolOutput):
                    item.bind(
                        name=tool_call.name,
                        call_id=tool_call.call_id,
                        max_tokens=self.agent.configs.max_tool_output_tokens,
                        model=self.agent.configs.model,
                    )
                elif isinstance(item, Image):
                    item.call_id = tool_call.call_id
                else:
                    item = ToolOutput.from_output(
                        name=tool_call.name,
                        call_id=tool_call.call_id,
                        output=item,
                        max_tokens=self.agent.configs.max_tool_output_tokens,
                        model=self.agent.configs.model,
                    )
                output_items.append(item)
        except Exception as error:
            error_message = str(error)
            status = "failure"
            output_items = [ToolOutput.from_output(
                name=tool_call.name,
                call_id=tool_call.call_id,
                output={"error": error_message},
                max_tokens=self.agent.configs.max_tool_output_tokens,
                model=self.agent.configs.model,
            )]
        feedback = {
            "status": status,
            "message_ids": [item.id for item in output_items],
        }
        if error_message is not None:
            feedback["error"] = error_message
        output = FunctionCallOutput(
            call_id=tool_call.call_id,
            output=json.dumps(feedback),
        )
        output.set_attr("output_items", output_items)
        return output

    def tool_search_output(self, tool_call):
        arguments = tool_call.arguments or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments or "{}")
        return ToolSearchOutput(
            call_id=tool_call.call_id,
            tools=self.agent.registry("tools").search(arguments),
        )

    async def run_tool_call(self, tool_call):
        user_feedback = self.user_feedback(tool_call)
        self.agent.emit(AgentToolCallStart(
            tool_call=tool_call,
            user_feedback=user_feedback,
        ))
        output = await self.tool_output(tool_call)
        output = self.agent.registry("hooks").run(
            "on_tool_output",
            output,
        )
        output_items = output.output_items or []
        self.agent.add_response_item(output)
        for item in output_items:
            self.agent.add_response_item(item)
        self.agent.emit(AgentToolCallEnd(
            tool_call=tool_call,
            user_feedback=user_feedback,
            output=output,
        ))
        return output

    @staticmethod
    def user_feedback(tool_call):
        try:
            arguments = json.loads(tool_call.arguments or "{}")
        except (TypeError, json.JSONDecodeError):
            return None
        feedback = arguments.get(USER_FEEDBACK_PARAMETER) if isinstance(arguments, dict) else None
        return feedback.strip() if isinstance(feedback, str) and feedback.strip() else None

    async def run_tool_calls(self, tool_calls):
        self.agent.emit(AgentToolCallsStart(tool_calls=tool_calls))
        outputs = []
        for index, tool_call in enumerate(tool_calls):
            if self.end_turn:
                outputs.extend(self.skip_tool_calls(tool_calls[index:]))
                break
            outputs.append(await self.run_tool_call(tool_call))
            if self.end_turn:
                outputs.extend(self.skip_tool_calls(tool_calls[index + 1:]))
                break
        self.agent.emit(AgentToolCallsEnd())
        return outputs

    def skip_tool_calls(self, tool_calls):
        outputs = []
        for tool_call in tool_calls:
            output = FunctionCallOutput(
                call_id=tool_call.call_id,
                output=json.dumps({
                    "status": "failure",
                    "message_ids": [],
                    "error": "Tool execution was interrupted before it started.",
                }, separators=(",", ":")),
            )
            self.agent.add_response_item(output)
            outputs.append(output)
        return outputs

    def command_outputs(self, prompt):
        commands = self.agent.registry("commands")
        parsed = commands.parse(prompt)
        if parsed == (None, None):
            return None

        name, _ = parsed
        command_id = timestamp_id()
        status = "success"
        try:
            result = commands.resolve(prompt)
        except Exception as error:
            result = {"error": str(error)}
            status = "failure"

        values = result if isinstance(result, tuple) else (result,)
        outputs = []
        try:
            for value in values:
                if value is None:
                    continue
                if isinstance(value, CommandOutput):
                    output = value.bind(
                        name=name,
                        command_id=command_id,
                        status=status,
                    )
                elif isinstance(value, Image):
                    value.command_id = command_id
                    output = value
                else:
                    output = CommandOutput.from_output(
                        name=name,
                        command_id=command_id,
                        output=value,
                        status=status,
                    )
                outputs.append(output)
        except Exception as error:
            outputs = [CommandOutput.from_output(
                name=name,
                command_id=command_id,
                output={"error": str(error)},
                status="failure",
            )]
        return self.agent.registry("hooks").run(
            "on_command_output",
            outputs,
        )

    async def turn(
        self,
        prompt=None,
        *,
        append_prompt=True,
        prompt_role="user",
        prompt_kind="message",
        reasoning_effort=None,
    ):
        if prompt_role not in {"user", "developer"}:
            raise ValueError("prompt_role must be user or developer")
        previous_reasoning_effort = self.reasoning_effort_override
        self.reasoning_effort_override = reasoning_effort
        try:
            prompt = self.agent.registry("hooks").run("before_turn", prompt)
            self.end_turn = False
            self.retry_wake.clear()
            self.agent.end_of_turn_requested = False
            self.request_next_step = False
            self.agent.emit(AgentTurnStart(prompt=prompt))
            command_outputs = self.command_outputs(prompt)
            if command_outputs is not None:
                for output in command_outputs:
                    self.agent.add_response_item(output)
                self.agent.emit(AgentTurnEnd())
                return
            if prompt and append_prompt:
                if prompt_kind == "message":
                    self.agent.add_message(prompt_role, prompt)
                else:
                    self.agent.add_message(prompt_role, prompt, kind=prompt_kind)
            await self.step()
            while (
                self.request_next_step
                and not self.end_turn
                and not getattr(self.agent, "end_of_turn_requested", False)
            ):
                await self.step()
            self.checkpoint(request_model_step=False)
            self.agent.emit(AgentTurnEnd())
        finally:
            self.reasoning_effort_override = previous_reasoning_effort

    def turn_sync(self, prompt=None):
        return asyncio.run(self.turn(prompt))
