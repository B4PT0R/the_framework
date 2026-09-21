from modict import modict

from .responses import ResponseItem
from .base import TypedBase


class Event(TypedBase):
    type: str = "unknown"

class Error(Event):
    type: str = "error"
    code: str | None
    message: str
    param: str | None
    sequence_number: int

class ResponseQueued(Event):
    type: str = "response.queued"
    response: object
    sequence_number: int

class ResponseCreated(Event):
    type: str = "response.created"
    response: object
    sequence_number: int

class ResponseInProgress(Event):
    type: str = "response.in_progress"
    response: object
    sequence_number: int

class ResponseCompleted(Event):
    type: str = "response.completed"
    response: object
    sequence_number: int

class ResponseFailed(Event):
    type: str = "response.failed"
    response: object
    sequence_number: int

class ResponseIncomplete(Event):
    type: str = "response.incomplete"
    response: object
    sequence_number: int

class ResponseOutputItemAdded(Event):
    type: str = "response.output_item.added"
    item: ResponseItem
    output_index: int
    sequence_number: int

    @modict.validator("item", mode="before")
    def reconstruct_item(self, value):
        return value if isinstance(value, ResponseItem) else ResponseItem.from_dict(dict(value))

class ResponseOutputItemDone(Event):
    type: str = "response.output_item.done"
    item: ResponseItem
    output_index: int
    sequence_number: int

    @modict.validator("item", mode="before")
    def reconstruct_item(self, value):
        return value if isinstance(value, ResponseItem) else ResponseItem.from_dict(dict(value))

class ResponseContentPartAdded(Event):
    type: str = "response.content_part.added"
    content_index: int
    item_id: str
    output_index: int
    part: object
    sequence_number: int

class ResponseContentPartDone(Event):
    type: str = "response.content_part.done"
    content_index: int
    item_id: str
    output_index: int
    part: object
    sequence_number: int

class ResponseOutputTextDelta(Event):
    type: str = "response.output_text.delta"
    content_index: int
    delta: str
    item_id: str
    logprobs: list
    output_index: int
    sequence_number: int

class ResponseOutputTextDone(Event):
    type: str = "response.output_text.done"
    content_index: int
    item_id: str
    logprobs: list
    output_index: int
    sequence_number: int
    text: str

class ResponseOutputTextAnnotationAdded(Event):
    type: str = "response.output_text.annotation.added"
    annotation: object
    annotation_index: int
    content_index: int
    item_id: str
    output_index: int
    sequence_number: int

class ResponseRefusalDelta(Event):
    type: str = "response.refusal.delta"
    content_index: int
    delta: str
    item_id: str
    output_index: int
    sequence_number: int

class ResponseRefusalDone(Event):
    type: str = "response.refusal.done"
    content_index: int
    item_id: str
    output_index: int
    refusal: str
    sequence_number: int

class ResponseFunctionCallArgumentsDelta(Event):
    type: str = "response.function_call_arguments.delta"
    delta: str
    item_id: str
    output_index: int
    sequence_number: int

class ResponseFunctionCallArgumentsDone(Event):
    type: str = "response.function_call_arguments.done"
    arguments: str
    item_id: str
    name: str
    output_index: int
    sequence_number: int

class ResponseCustomToolCallInputDelta(Event):
    type: str = "response.custom_tool_call_input.delta"
    delta: str
    item_id: str
    output_index: int
    sequence_number: int

class ResponseCustomToolCallInputDone(Event):
    type: str = "response.custom_tool_call_input.done"
    input: str
    item_id: str
    output_index: int
    sequence_number: int

class ResponseReasoningTextDelta(Event):
    type: str = "response.reasoning_text.delta"
    content_index: int
    delta: str
    item_id: str
    output_index: int
    sequence_number: int

class ResponseReasoningTextDone(Event):
    type: str = "response.reasoning_text.done"
    content_index: int
    item_id: str
    output_index: int
    sequence_number: int
    text: str

class ResponseReasoningSummaryPartAdded(Event):
    type: str = "response.reasoning_summary_part.added"
    item_id: str
    output_index: int
    part: object
    sequence_number: int
    summary_index: int

class ResponseReasoningSummaryPartDone(Event):
    type: str = "response.reasoning_summary_part.done"
    item_id: str
    output_index: int
    part: object
    sequence_number: int
    summary_index: int

class ResponseReasoningSummaryTextDelta(Event):
    type: str = "response.reasoning_summary_text.delta"
    delta: str
    item_id: str
    output_index: int
    sequence_number: int
    summary_index: int

class ResponseReasoningSummaryTextDone(Event):
    type: str = "response.reasoning_summary_text.done"
    item_id: str
    output_index: int
    sequence_number: int
    summary_index: int
    text: str

class ResponseWebSearchCallInProgress(Event):
    type: str = "response.web_search_call.in_progress"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseWebSearchCallSearching(Event):
    type: str = "response.web_search_call.searching"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseWebSearchCallCompleted(Event):
    type: str = "response.web_search_call.completed"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseFileSearchCallInProgress(Event):
    type: str = "response.file_search_call.in_progress"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseFileSearchCallSearching(Event):
    type: str = "response.file_search_call.searching"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseFileSearchCallCompleted(Event):
    type: str = "response.file_search_call.completed"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseCodeInterpreterCallInProgress(Event):
    type: str = "response.code_interpreter_call.in_progress"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseCodeInterpreterCallInterpreting(Event):
    type: str = "response.code_interpreter_call.interpreting"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseCodeInterpreterCallCompleted(Event):
    type: str = "response.code_interpreter_call.completed"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseCodeInterpreterCallCodeDelta(Event):
    type: str = "response.code_interpreter_call_code.delta"
    delta: str
    item_id: str
    output_index: int
    sequence_number: int

class ResponseCodeInterpreterCallCodeDone(Event):
    type: str = "response.code_interpreter_call_code.done"
    code: str
    item_id: str
    output_index: int
    sequence_number: int

class ResponseImageGenerationCallInProgress(Event):
    type: str = "response.image_generation_call.in_progress"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseImageGenerationCallGenerating(Event):
    type: str = "response.image_generation_call.generating"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseImageGenerationCallPartialImage(Event):
    type: str = "response.image_generation_call.partial_image"
    item_id: str
    output_index: int
    partial_image_b64: str
    partial_image_index: int
    sequence_number: int

class ResponseImageGenerationCallCompleted(Event):
    type: str = "response.image_generation_call.completed"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseMcpCallInProgress(Event):
    type: str = "response.mcp_call.in_progress"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseMcpCallArgumentsDelta(Event):
    type: str = "response.mcp_call_arguments.delta"
    delta: str
    item_id: str
    output_index: int
    sequence_number: int

class ResponseMcpCallArgumentsDone(Event):
    type: str = "response.mcp_call_arguments.done"
    arguments: str
    item_id: str
    output_index: int
    sequence_number: int

class ResponseMcpCallCompleted(Event):
    type: str = "response.mcp_call.completed"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseMcpCallFailed(Event):
    type: str = "response.mcp_call.failed"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseMcpListToolsInProgress(Event):
    type: str = "response.mcp_list_tools.in_progress"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseMcpListToolsCompleted(Event):
    type: str = "response.mcp_list_tools.completed"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseMcpListToolsFailed(Event):
    type: str = "response.mcp_list_tools.failed"
    item_id: str
    output_index: int
    sequence_number: int

class ResponseAudioDelta(Event):
    type: str = "response.audio.delta"
    delta: str
    sequence_number: int

class ResponseAudioDone(Event):
    type: str = "response.audio.done"
    sequence_number: int

class ResponseAudioTranscriptDelta(Event):
    type: str = "response.audio.transcript.delta"
    delta: str
    sequence_number: int

class ResponseAudioTranscriptDone(Event):
    type: str = "response.audio.transcript.done"
    sequence_number: int
