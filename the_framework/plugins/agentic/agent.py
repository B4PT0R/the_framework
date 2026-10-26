import json
import time

from ...agent.models.config import Config

from ...agent.extensions.extension import AgentExtension
from ...agent.extensions.providers import provider
from ...agent.extensions.tools import tool


def object_json(value):
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("expected a JSON object")
    return result


class AgenticConfig(Config):
    default_wait_seconds: int = 30
    max_wait_seconds: int = 3600


class AgenticExtension(AgentExtension):
    name = "agentic"
    config = AgenticConfig
    description = "Persistent goals and tools for organizing autonomous work."
    instructions_file = "instructions.md"
    instruction_scope = "agentic"

    @provider
    def turn_timing(self):
        """Current turn boundary and timing of the previous completed turn."""
        timing = getattr(self.agent, "turn_timing", None)
        return timing.context() if timing is not None else None

    @provider(channels=("text",))
    def context_occupancy(self):
        """Latest local input estimate, relative to the configured context budget, not an API measurement."""
        config = self.agent.configs.compaction
        tokens = self.agent.context.last_local_input_tokens
        available = tokens > 0 and config.context_token_limit > 0
        return {
            "status": "estimated" if available else "unavailable",
            "source": "last_prepared_request_local_estimate",
            "input_tokens": tokens if available else None,
            "context_token_limit": config.context_token_limit,
            "occupied_percent": round(100 * tokens / config.context_token_limit, 1) if available else None,
            "automatic_compaction_percent": round(100 * config.trigger_ratio, 1),
            "compaction_requested": self.agent.agentic_loop.compaction_requested,
        }

    @tool
    def compact(self):
        """Request canonical context compaction before the next model step, after the current tool batch. Preserve needed task state first. This acknowledges a request, not completed compaction."""
        self.agent.agentic_loop.compaction_requested = True
        return {"status": "compaction_requested", "when": "before_next_model_step"}

    @tool
    def end_turn(self):
        """End the current turn gracefully after the current tool-call batch."""
        self.agent.end_turn()
        return {"status": "end_of_turn_requested"}

    @tool
    def wait(self, timeout_seconds: int | None = None):
        """
        description: Wait idly until the timeout expires, the user steers the active turn, or the turn is interrupted. Worker IPC remains responsive throughout.
        parameters:
          properties:
            timeout_seconds:
              description: Optional wait duration in seconds, capped by plugin configuration. Null uses the configured default.
        """
        duration = (
            self.config.default_wait_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int)
            or not 0 <= duration <= self.config.max_wait_seconds
        ):
            raise ValueError(
                f"wait timeout_seconds must be within 0..{self.config.max_wait_seconds}"
            )
        started_at = time.monotonic()
        reason = self.agent.agentic_loop.wait_for_activity(duration)
        return {
            "status": "elapsed" if reason == "timeout" else reason,
            "waited_seconds": round(time.monotonic() - started_at, 3),
        }

    async def _call(self, method, payload=None):
        return await self.agent.bridge.call("agentic", method, payload or {}, timeout_ms=10_000)

    @provider
    async def current_goal(self):
        """Current open goal, subtasks and continuation state; unavailable is not empty."""
        try:
            state = await self._call("current")
            return None if state.get("status") == "ready" and state.get("goal") is None else state
        except Exception as error:
            return {"status": "unavailable", "error": str(error)}

    @tool
    async def create_goal(self, goal_json: str):
        """
        description: >-
          Create the sole open goal from JSON: objective, scope, success_criteria (strings),
          tasks (title strings). Requires an established user intention and authorized scope.
          A suspended goal still occupies the open slot. No implicit extra permissions.
        """
        return await self._call("create", object_json(goal_json))

    @tool
    async def get_goal(self, goal_id: str):
        """Read full durable goal details and its current revision, including closed goals."""
        return await self._call("get", {"goal_id": goal_id})

    @tool
    async def list_goals(self):
        """List compact goal records, including suspended, completed and cancelled goals."""
        return await self._call("list")

    @tool
    async def update_goal_progress(self, goal_id: str, expected_revision: int, progress_json: str):
        """
        description: >-
          Record progress JSON: optional summary and tasks (complete ordered list).
          Existing tasks keep id; new tasks omit id. Each has title, status
          (pending/in_progress/completed/skipped), evidence. Preserve existing tasks;
          explain skipped/completed tasks in evidence. Cannot change the objective or scope.
        """
        return await self._call("progress", {
            "goal_id": goal_id, "expected_revision": expected_revision,
            "changes": object_json(progress_json),
        })

    @tool
    async def suspend_goal(self, goal_id: str, expected_revision: int, reason: str):
        """
        description: >-
          Suspend autonomous continuations, preserving work. Explain obstacle, attempts
          and what is needed to resume. Decide this yourself when further work is unreasonable.
        """
        return await self._call("suspend", {
            "goal_id": goal_id, "expected_revision": expected_revision, "reason": reason,
        })

    @tool
    async def complete_goal(self, goal_id: str, expected_revision: int, evidence: str):
        """
        description: >-
          Complete only after verifying all success criteria and resolving all subtasks.
          Evidence must explain the verification, not merely assert that boxes are checked.
        """
        return await self._call("complete", {
            "goal_id": goal_id, "expected_revision": expected_revision, "evidence": evidence,
        })

    @tool
    async def revise_goal(self, goal_id: str, expected_revision: int, user_instruction: str,
                          changes_json: str):
        """
        description: >-
          Only on an explicit human request: correct objective/scope/success_criteria,
          resume a suspended goal (status=active), or cancel (status=cancelled).
          Quote the actual user instruction; never invent authorization or cancel autonomously.
        """
        return await self._call("revise", {
            "goal_id": goal_id, "expected_revision": expected_revision,
            "user_instruction": user_instruction, "changes": object_json(changes_json),
        })
