"""Agent tools selecting independent local/host Bash workers."""

import signal
import threading
import time
from typing import Literal

from the_framework.agent import AgentExtension, tool
from .config import BashConfig


class BashExtension(AgentExtension):
    name = "bash"
    description = "Inspect, edit and execute on an explicitly selected local or host environment."
    config = BashConfig
    instructions_file = "instructions.md"

    def __init__(self, agent):
        super().__init__(agent)
        self._clients = {}
        self._client_lock = threading.Lock()
        if agent is not None:
            agent.on("agent.interrupted", self._on_interrupted)

    def _client(self, target):
        from .worker import BashClient

        with self._client_lock:
            if target not in self._clients:
                self._clients[target] = BashClient(target, dict(self.config))
            return self._clients[target]

    def _on_interrupted(self, _event):
        with self._client_lock:
            clients = tuple(self._clients.values())
        for client in clients:
            client.process.send_signal(signal.SIGUSR1)

    def shutdown(self):
        with self._client_lock:
            clients, self._clients = tuple(self._clients.values()), {}
        errors = []
        for client in clients:
            try:
                client.close()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("Bash target cleanup failed", errors)

    def deactivate(self):
        self.shutdown()
        return super().deactivate()

    @tool
    def write(self, writes: object, *, target: Literal["local", "host"]):
        """
        description: Atomically create or replace complete UTF-8 text files using local-account permissions.
        parameters:
          properties:
            target:
              description: Explicit execution environment; local is the backend container when Dockerized, host uses the configured host service. No fallback.
            writes:
              description: One or more files containing a path and complete UTF-8 content.
              type: array
              items:
                type: object
                properties:
                  path:
                    type: string
                    description: Absolute path or path relative to the configured default cwd.
                  content:
                    type: string
                    description: Complete UTF-8 file content.
                required: [path, content]
                additionalProperties: false

        """
        return self._client(target).call('write', writes=writes)

    @tool
    def edit(self, edits: object, *, target: Literal["local", "host"]):
        """
        description: Atomically perform exact-string replacements in UTF-8 text files. By default old_string must occur exactly once.
        parameters:
          properties:
            target:
              description: Explicit execution environment; local is the backend container when Dockerized, host uses the configured host service. No fallback.
            edits:
              description: One or more exact replacements. Use null replace_all for the safe single-match default.
              type: array
              items:
                type: object
                properties:
                  path:
                    type: string
                    description: Absolute path or path relative to the configured default cwd.
                  old_string:
                    type: string
                    description: Exact non-empty text to replace.
                  new_string:
                    type: string
                    description: Replacement text.
                  replace_all:
                    type: [boolean, "null"]
                    description: True only when every exact match should be replaced; null replaces one match.
                required: [path, old_string, new_string, replace_all]
                additionalProperties: false

        """
        return self._client(target).call('edit', edits=edits)

    @tool
    def command(self, command: str, cwd: str='.', timeout_seconds: int | None=None, *, target: Literal["local", "host"]):
        """
        description: Execute a blocking Bash command with local account privileges, returning when it completes or an optional hard timeout is reached.
        parameters:
          properties:
            target:
              description: Explicit execution environment; local is the backend container when Dockerized, host uses the configured host service. No fallback.
            command:
              description: Raw Bash script text, without a Markdown code fence.
            cwd:
              description: Absolute or default-cwd-relative working directory; defaults to the configured default cwd.
            timeout_seconds:
              description: Optional hard command timeout capped by plugin configuration; null means no forced timeout.

        """
        return self._client(target).call('command', command=command, cwd=cwd, timeout_seconds=timeout_seconds)

    @tool
    def job(self, command: str, cwd: str='.', timeout_seconds: int | None=None, *, target: Literal["local", "host"]):
        """
        description: Start a long-running Bash job and immediately return an acknowledgement with a persistent job_id. Use check for progress and interrupt to stop it.
        parameters:
          properties:
            target:
              description: Explicit execution environment; local is the backend container when Dockerized, host uses the configured host service. No fallback.
            command:
              description: Raw Bash script text, without a Markdown code fence.
            cwd:
              description: Absolute or default-cwd-relative working directory; defaults to the configured default cwd.
            timeout_seconds:
              description: Optional hard job lifetime capped by plugin configuration; null means no forced timeout.

        """
        return self._client(target).call('job', command=command, cwd=cwd, timeout_seconds=timeout_seconds)

    @tool
    def check(self, job_id: str, *, target: Literal["local", "host"]):
        """
        description: Non-blockingly read the current status and accumulated stdout/stderr of a job. Completed job records remain available within bounded retention.
        parameters:
          properties:
            target:
              description: Explicit execution environment; local is the backend container when Dockerized, host uses the configured host service. No fallback.
            job_id:
              description: Persistent identifier returned by job.

        """
        return self._client(target).call('check', job_id=job_id)

    @tool
    def wait(self, job_id: str, timeout_seconds: int | None=None, *, target: Literal["local", "host"]):
        """
        description: Stay idle within the current agent turn while a job progresses, then return its state when it finishes or this wait window expires. This does not extend the job's hard timeout.
        parameters:
          properties:
            target:
              description: Explicit execution environment; local is the backend container when Dockerized, host uses the configured host service. No fallback.
            job_id:
              description: Persistent identifier returned by job.
            timeout_seconds:
              description: Wait window capped independently by plugin configuration.

        """
        duration = self.config.default_wait_seconds if timeout_seconds is None else timeout_seconds
        if isinstance(duration, bool) or not isinstance(duration, int) or not 0 <= duration <= self.config.max_wait_seconds:
            raise ValueError(f"wait timeout_seconds must be within 0..{self.config.max_wait_seconds}")
        client = self._client(target)
        deadline = time.monotonic() + duration
        reason = "timeout"
        while True:
            reason = self.agent.agentic_loop.wait_for_activity(0)
            if reason != "timeout":
                break
            result = client.call("check", job_id=job_id)
            if result["status"] != "running":
                reason = "job_completed"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            reason = self.agent.agentic_loop.wait_for_activity(min(remaining, .05))
            if reason != "timeout":
                break
        result = client.call("check", job_id=job_id)
        result["wait_reason"] = reason
        return result

    @tool
    def interrupt(self, job_id: str, *, target: Literal["local", "host"]):
        """
        description: Deliberately terminate a running job and its complete process group, then return its final observable state.
        parameters:
          properties:
            target:
              description: Explicit execution environment; local is the backend container when Dockerized, host uses the configured host service. No fallback.
            job_id:
              description: Persistent identifier returned by job.

        """
        return self._client(target).call('interrupt', job_id=job_id)

    @tool
    def python(self, code: str, cwd: str='.', timeout_seconds: int | None=None, *, target: Literal["local", "host"]):
        """
        description: Execute Python with the application interpreter and local-account permissions, with an optional hard timeout.
        parameters:
          properties:
            target:
              description: Explicit execution environment; local is the backend container when Dockerized, host uses the configured host service. No fallback.
            code:
              description: Raw Python source, without a Markdown code fence.
            cwd:
              description: Absolute or default-cwd-relative working directory; defaults to the configured default cwd.
            timeout_seconds:
              description: Optional hard timeout capped by plugin configuration; null means no forced timeout.

        """
        return self._client(target).call('python', code=code, cwd=cwd, timeout_seconds=timeout_seconds)
