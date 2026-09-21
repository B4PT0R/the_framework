import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from core.agent import Config, Plugin, hook, tool


class BashConfig(Config):
    default_cwd: str = "."
    inherit_interactive_environment: bool = True
    max_timeout_seconds: int = 900
    default_wait_seconds: int = 10
    max_wait_seconds: int = 60
    max_concurrent_processes: int = 4
    max_process_records: int = 32
    max_output_chars: int = 50_000


class BashPlugin(Plugin):
    name = "bash"
    description = "Inspect, edit, and execute through the local account's shell environment."
    config = BashConfig
    instructions_file = "instructions.md"

    def __init__(self, agent):
        super().__init__(agent)
        self._processes = set()
        self._jobs = {}
        self._process_lock = threading.Lock()
        self._execution_env = None
        if agent is not None:
            agent.on("agent.interrupted", self._on_interrupted)

    def load(self):
        if self.loaded:
            return self
        super().load()
        self._execution_env = self._capture_execution_environment()
        return self

    def _capture_execution_environment(self):
        environment = os.environ.copy()
        if not self.config.inherit_interactive_environment:
            return environment
        marker = b"\0__HARNESS_INTERACTIVE_ENV__\0"
        try:
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-ic",
                    "printf '\\0__HARNESS_INTERACTIVE_ENV__\\0'; env -0",
                ],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if result.returncode != 0 or marker not in result.stdout:
                return environment
            payload = result.stdout.split(marker, 1)[1]
            captured = {}
            for entry in payload.split(b"\0"):
                if not entry or b"=" not in entry:
                    continue
                name, value = entry.split(b"=", 1)
                captured[os.fsdecode(name)] = os.fsdecode(value)
            return captured or environment
        except (OSError, subprocess.SubprocessError):
            return environment

    def _on_interrupted(self, _event):
        self._stop_all_processes(interrupted=True)

    @hook
    def on_shutdown(self, payload):
        """Terminate every process owned by this plugin before worker shutdown."""
        self._stop_all_processes()
        return payload

    def deactivate(self):
        self._stop_all_processes()
        return super().deactivate()

    def _stop_all_processes(self, *, interrupted=False):
        with self._process_lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if interrupted and job["process"].poll() is None:
                job["interrupted"] = True
            self._terminate(job["process"])
            self._cleanup_job(job)

    def _resolve(self, value, *, must_exist=False):
        if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
            raise ValueError("path must be a non-empty string")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(self.config.default_cwd).expanduser() / path
        path = path.resolve(strict=False)
        if must_exist and not path.exists():
            raise FileNotFoundError(path)
        return path

    def _limit(self, content, limit=None):
        limit = int(limit or self.config.max_output_chars)
        if limit <= 0:
            raise ValueError("output limit must be positive")
        content = str(content)
        if len(content) <= limit:
            return content
        omitted = len(content) - limit
        return f"{content[:limit]}\n… <truncated {omitted} characters>"

    @staticmethod
    def _items(value, name):
        if isinstance(value, Mapping):
            return [value]
        if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
            return value
        raise ValueError(f"{name} must be an object or a list of objects")

    @tool
    def write(self, writes: object):
        """
        description: Atomically create or replace complete UTF-8 text files using local-account permissions.
        parameters:
          properties:
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
        reports = []
        for item in self._items(writes, "writes"):
            path = self._resolve(item.get("path"))
            content = item.get("content")
            if content is None:
                raise ValueError("write content must not be null")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(path, str(content))
            reports.append(f"wrote {len(str(content))} characters to {path}")
        return "\n".join(reports)

    @tool
    def edit(self, edits: object):
        """
        description: Atomically perform exact-string replacements in UTF-8 text files. By default old_string must occur exactly once.
        parameters:
          properties:
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
        reports = []
        for item in self._items(edits, "edits"):
            path = self._resolve(item.get("path"), must_exist=True)
            old = item.get("old_string")
            new = item.get("new_string")
            if old is None or new is None or old == "":
                raise ValueError("edit requires non-empty old_string and non-null new_string")
            content = path.read_text(encoding="utf-8")
            count = content.count(str(old))
            replace_all = bool(item.get("replace_all", False))
            if count == 0:
                raise ValueError(f"old_string not found in {path}")
            if count > 1 and not replace_all:
                raise ValueError(f"old_string matched {count} times in {path}; set replace_all=true")
            updated = content.replace(str(old), str(new), -1 if replace_all else 1)
            self._atomic_write(path, updated)
            replaced = count if replace_all else 1
            reports.append(f"replaced {replaced} match{'es' if replaced != 1 else ''} in {path}")
        return "\n".join(reports)

    @staticmethod
    def _atomic_write(path, content):
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @tool
    def command(self, command: str, cwd: str = ".", timeout_seconds: int | None = None):
        """
        description: Execute a blocking Bash command with local account privileges, returning when it completes or an optional hard timeout is reached.
        parameters:
          properties:
            command:
              description: Raw Bash script text, without a Markdown code fence.
            cwd:
              description: Absolute or default-cwd-relative working directory; defaults to the configured default cwd.
            timeout_seconds:
              description: Optional hard command timeout capped by plugin configuration; null means no forced timeout.
        """
        job = self._start(["/bin/bash", "-lc", command], cwd, timeout_seconds)
        return self._collect_command(job)

    @tool
    def job(self, command: str, cwd: str = ".", timeout_seconds: int | None = None):
        """
        description: Start a long-running Bash job and immediately return an acknowledgement with a persistent job_id. Use check for progress and interrupt to stop it.
        parameters:
          properties:
            command:
              description: Raw Bash script text, without a Markdown code fence.
            cwd:
              description: Absolute or default-cwd-relative working directory; defaults to the configured default cwd.
            timeout_seconds:
              description: Optional hard job lifetime capped by plugin configuration; null means no forced timeout.
        """
        job = self._start(["/bin/bash", "-lc", command], cwd, timeout_seconds)
        return {
            "status": "started",
            "job_id": job["id"],
            "cwd": job["cwd"],
            "timeout_seconds": job["timeout_seconds"],
        }

    @tool
    def check(self, job_id: str):
        """
        description: Non-blockingly read the current status and accumulated stdout/stderr of a job. Completed job records remain available within bounded retention.
        parameters:
          properties:
            job_id:
              description: Persistent identifier returned by job.
        """
        with self._process_lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ValueError(f"unknown or expired job: {job_id}")
        return self._snapshot(job)

    @tool
    def wait(self, job_id: str, timeout_seconds: int | None = None):
        """
        description: Stay idle within the current agent turn while a job progresses, then return its state when it finishes or this wait window expires. This does not extend the job's hard timeout.
        parameters:
          properties:
            job_id:
              description: Persistent identifier returned by job.
            timeout_seconds:
              description: Wait window capped independently by plugin configuration.
        """
        with self._process_lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ValueError(f"unknown or expired job: {job_id}")
        duration = self.config.default_wait_seconds if timeout_seconds is None else timeout_seconds
        if isinstance(duration, bool) or not isinstance(duration, int) or not 0 <= duration <= self.config.max_wait_seconds:
            raise ValueError(f"wait timeout_seconds must be within 0..{self.config.max_wait_seconds}")
        deadline = time.monotonic() + duration
        reason = "timeout"
        while True:
            activity = self.agent.agentic_loop.wait_for_activity(0)
            if activity != "timeout":
                reason = activity
                break
            if job["process"].poll() is not None:
                reason = "job_completed"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Process completion has no portable wait-any primitive with the
            # steering condition. This short wait happens only in the tool
            # worker thread and keeps both inputs responsive.
            activity = self.agent.agentic_loop.wait_for_activity(
                min(remaining, 0.05)
            )
            if activity != "timeout":
                reason = activity
                break
        result = self._snapshot(job)
        result["wait_reason"] = reason
        return result

    @tool
    def interrupt(self, job_id: str):
        """
        description: Deliberately terminate a running job and its complete process group, then return its final observable state.
        parameters:
          properties:
            job_id:
              description: Persistent identifier returned by job.
        """
        with self._process_lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ValueError(f"unknown or expired job: {job_id}")
        if job["process"].poll() is None:
            job["interrupted"] = True
            self._terminate(job["process"])
            job["completed_at"] = time.monotonic()
        return self._snapshot(job)

    @tool
    def python(self, code: str, cwd: str = ".", timeout_seconds: int | None = None):
        """
        description: Execute Python with the harness interpreter and local-account permissions, with an optional hard timeout.
        parameters:
          properties:
            code:
              description: Raw Python source, without a Markdown code fence.
            cwd:
              description: Absolute or default-cwd-relative working directory; defaults to the configured default cwd.
            timeout_seconds:
              description: Optional hard timeout capped by plugin configuration; null means no forced timeout.
        """
        return self._run([sys.executable, "-c", code], cwd, timeout_seconds)

    def _start(self, argv, cwd, timeout_seconds):
        if not isinstance(argv[-1], str) or not argv[-1].strip():
            raise ValueError("execution content must not be empty")
        cwd = self._resolve(cwd, must_exist=True)
        if not cwd.is_dir():
            raise ValueError(f"execution cwd is not a directory: {cwd}")
        timeout = timeout_seconds
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= self.config.max_timeout_seconds
        ):
            raise ValueError(f"timeout_seconds must be within 1..{self.config.max_timeout_seconds}")
        self._prune_jobs()
        with self._process_lock:
            running = sum(job["process"].poll() is None for job in self._jobs.values())
        if running >= self.config.max_concurrent_processes:
            raise RuntimeError("bash concurrent process limit reached")
        process_id = str(uuid4())
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=self._execution_env or os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        job = {
            "id": process_id,
            "process": process,
            "cwd": str(cwd),
            "deadline": None if timeout is None else time.monotonic() + timeout,
            "timeout_seconds": timeout,
            "started_at": time.monotonic(),
            "completed_at": None,
            "timed_out": False,
            "interrupted": False,
            "stdout": [],
            "stderr": [],
            "discarded": {"stdout": 0, "stderr": 0},
            "output_lock": threading.Lock(),
            "readers": [],
        }
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            reader = threading.Thread(
                target=self._capture,
                args=(job, name, pipe),
                daemon=True,
            )
            job["readers"].append(reader)
            reader.start()
        with self._process_lock:
            self._processes.add(process)
            self._jobs[process_id] = job
        if timeout is not None:
            threading.Thread(target=self._watchdog, args=(job,), daemon=True).start()
        return job

    def _capture(self, job, name, pipe):
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                with job["output_lock"]:
                    used = sum(len(value) for value in job[name])
                    room = max(0, self.config.max_output_chars - used)
                    job[name].append(chunk[:room])
                    job["discarded"][name] += max(0, len(chunk) - room)
        finally:
            pipe.close()

    def _watchdog(self, job):
        process = job["process"]
        remaining = max(0, job["deadline"] - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            job["timed_out"] = True
            self._terminate(process)
        finally:
            job["completed_at"] = time.monotonic()
            with self._process_lock:
                self._processes.discard(process)

    def _prune_jobs(self):
        with self._process_lock:
            completed = sorted(
                (
                    job for job in self._jobs.values()
                    if job["process"].poll() is not None
                ),
                key=lambda job: job["completed_at"] or job["started_at"],
            )
            excess = max(0, len(self._jobs) - self.config.max_process_records + 1)
        for job in completed[:excess]:
            self._cleanup_job(job)

    def _collect_command(self, job):
        process = job["process"]
        remaining = (
            None if job["deadline"] is None
            else max(0, job["deadline"] - time.monotonic())
        )
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            job["timed_out"] = True
            self._terminate(process)
        result = self._snapshot(job)
        self._cleanup_job(job)
        return {
            key: value
            for key, value in result.items()
            if key != "job_id"
        }

    def _snapshot(self, job):
        process = job["process"]
        running = process.poll() is None
        if not running:
            for reader in job["readers"]:
                reader.join(timeout=1)
        status = (
            "running" if running
            else "timed_out" if job["timed_out"]
            else "interrupted" if job["interrupted"]
            else "completed" if process.returncode == 0
            else "failed"
        )
        result = {
            "job_id": job["id"],
            "status": status,
            "exit_code": None if running else process.returncode,
            "stdout": self._job_output(job, "stdout"),
            "stderr": self._job_output(job, "stderr"),
            "timed_out": job["timed_out"],
            "cwd": job["cwd"],
        }
        return result

    def _job_output(self, job, name):
        with job["output_lock"]:
            content = "".join(job[name])
            discarded = job["discarded"][name]
        if discarded:
            content += f"\n… <truncated {discarded} characters>"
        return content

    def _cleanup_job(self, job):
        for reader in job["readers"]:
            reader.join(timeout=1)
        with self._process_lock:
            self._processes.discard(job["process"])
            self._jobs.pop(job["id"], None)

    def _run(self, argv, cwd, timeout_seconds):
        job = self._start(argv, cwd, timeout_seconds)
        result = self._collect_command(job)
        return {key: value for key, value in result.items() if key != "status"}

    @staticmethod
    def _terminate(process):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=1)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
