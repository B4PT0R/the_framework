"""Real hosted Notebook, deterministic Responses stream; no inference/network."""
import json
from types import SimpleNamespace

from examples.notebook import NOTEBOOK
from the_framework import AgentResources, AgentSpec, Application


class Responses:
    def create(self, **payload):
        already_saved = any(item.get("type") == "function_call_output" for item in payload["input"])
        item = ({
            "type": "message", "id": "answer", "role": "assistant",
            "content": [{"type": "output_text", "text": "Saved."}],
        } if already_saved else {
            "type": "function_call", "id": "save", "call_id": "save-call",
            "name": "save_note", "namespace": "notebook", "arguments": json.dumps({
                "key": "design", "title": "Design", "body": "A worker saved this note.",
                "user_feedback": "I am saving this design note in the current project.",
            }),
        })
        yield {"type": "response.output_item.done", "item": item}
        yield {"type": "response.completed", "response": {"id": "response", "model": "test"}}


def resources(_workspace):
    return AgentResources(client=SimpleNamespace(responses=Responses()))


application = Application(
    name="Notebook integration test", version="1", surfaces=(),
    agents=(AgentSpec(name="assistant", resources=resources),), plugins=(NOTEBOOK,),
)
