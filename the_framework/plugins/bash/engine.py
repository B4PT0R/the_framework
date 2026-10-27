"""Bash execution owned entirely by the selected process environment."""

import codecs
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

from .config import BashConfig


class BashEngine:
    def __init__(self, config):
        self.config = BashConfig(**config)
        self._processes = set()
        self._jobs = {}
        self._process_lock = threading.Lock()
        self._closed = False
        self._execution_env = self._capture_execution_environment()

    def close(self):
        with self._process_lock:
            self._closed = True
        self._stop_all_processes()

    def _capture_execution_environment(self):
        environment = os.environ.copy()
        if not self.config.inherit_interactive_environment:
            return environment
        marker = b"\0__FRAMEWORK_INTERACTIVE_ENV__\0"
        try:
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-ic",
                    "printf '\\0__FRAMEWORK_INTERACTIVE_ENV__\\0'; env -0",
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

    @staticmethod
    def _items(value, name):
        if isinstance(value, Mapping):
            return [value]
        if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
            return value
        raise ValueError(f"{name} must be an object or a list of objects")

    def write(self, writes: object):
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

    def edit(self, edits: object):
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

    def command(self, command: str, cwd: str = ".", timeout_seconds: int | None = None):
        job = self._start(["/bin/bash", "-c", command], cwd, timeout_seconds)
        return self._collect_command(job)

    def job(self, command: str, cwd: str = ".", timeout_seconds: int | None = None):
        job = self._start(["/bin/bash", "-c", command], cwd, timeout_seconds)
        return {
            "status": "started",
            "job_id": job["id"],
            "cwd": job["cwd"],
            "timeout_seconds": job["timeout_seconds"],
        }

    def check(self, job_id: str):
        with self._process_lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ValueError(f"unknown or expired job: {job_id}")
        return self._snapshot(job)

    def interrupt(self, job_id: str):
        with self._process_lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ValueError(f"unknown or expired job: {job_id}")
        if job["process"].poll() is None:
            job["interrupted"] = True
            self._terminate(job["process"])
            job["completed_at"] = time.monotonic()
        return self._snapshot(job)

    def python(self, code: str, cwd: str = ".", timeout_seconds: int | None = None):
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
            if self._closed:
                raise RuntimeError("Bash worker is closed")
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
            self._processes.add(process)
            self._jobs[process_id] = job
        if timeout is not None:
            threading.Thread(target=self._watchdog, args=(job,), daemon=True).start()
        return job

    def _capture(self, job, name, pipe):
        try:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                data = os.read(pipe.fileno(), 4096)
                chunk = decoder.decode(data, final=not data)
                if not chunk and not data:
                    break
                with job["output_lock"]:
                    used = sum(len(value) for value in job[name])
                    room = max(0, self.config.max_output_chars - used)
                    if room:
                        job[name].append(chunk[:room])
                    job["discarded"][name] += max(0, len(chunk) - room)
                if not data:
                    break
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
            self._terminate(process)
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
        # A leader may exit while descendants still hold the captured pipes.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=.1)
        except subprocess.TimeoutExpired:
            pass
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
