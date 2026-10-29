# Local and host execution

The Framework supplies process **channels**, not a command sandbox or durable job
service. `the_framework.execution` launches a named worker locally or through an
explicitly configured host. The Bash plugin builds file/command/job tools on that
channel. A plugin owns its protocol and result format; the channel does not own
conversation state or infer where a path exists.

## Explicit target and authority

- `local` means the backend's current environment (the container when Dockerized).
- `host` means a separately configured host account, interpreter and filesystem.
- No failure falls back between them, and no file transfer/path translation is
  implicit. Check, wait and interrupt a job on the target that created it.

`launch_process(target, name, recipe)` returns `LocalProcess(recipe)` for local
execution. The host path reads runner settings from `THE_FRAMEWORK_HOST_SETTINGS`,
rejects validation runtimes or missing host configuration, and creates
`RemoteProcess(endpoint, name)`. The local recipe is not serialized to the host:
`name` selects a callable installed in the host's `ProcessHost.recipes` registry.
A remote caller cannot choose a new executable/module through this launch API.
That is an authority boundary, **not** safe execution of hostile code: a registered
Bash worker is itself a general code-execution capability. Keep its separate host
credential out of agent configuration, session history and logs.

## A real duplex worker

Public exports are `ExecutionTarget`, `launch_process`, `LocalProcess`,
`RemoteProcess`, `ProcessHost`. The following POSIX example creates a subprocess
and socketpair without binding a network listener or requiring host credentials:

```python
import sys
from the_framework.execution import launch_process

def reverse_recipe(fd):
    return [sys.executable, "-c", (
        "import socket,sys; s=socket.socket(fileno=int(sys.argv[1])); "
        "s.sendall(s.recv(64)[::-1])"
    ), str(fd)]

process = launch_process("local", "documentation_reverse", reverse_recipe)
try:
    process.socket.settimeout(5)
    process.socket.sendall(b"framework")
    assert process.socket.recv(64) == b"krowemarf"
    assert process.wait(timeout=5) == 0
finally:
    process.close()
assert process.socket.fileno() == -1
```

The recipe receives an inherited socket FD and returns argv evaluated where it
runs. Stdin/stdout/stderr are disconnected from the caller: use the duplex socket
for protocol data. The channel does not frame, validate or interpret those bytes.
Define your own bounded framing and error handling. Keep socket ownership explicit;
`LocalProcess` starts a new process session and provides `poll`, `wait`,
`send_signal(number, group=False)`, `close`.

`wait(timeout)` raises `subprocess.TimeoutExpired` but does not terminate by
itself. `close()` shuts down writing (cooperative EOF), waits up to two seconds,
closes its socket, kills the tracked process group and reaps the leader. A worker
recipe must clean up children that created independent sessions; no process group
mechanism can promise cleanup after every hard crash or deliberate detach.

`ProcessHost(recipes, max_processes=8)` bounds concurrent channels. `open(name)`
returns a random handle plus the process; `control` supports poll, close and a
restricted signal set (USR1/TERM/KILL). Finished handle status is bounded to 128
records; it is not a database. Authentication/listener policy belongs to the
private host server, not `ProcessHost` alone. Its async relay copies raw bytes
with backpressure and retires a process when either direction ends. Shutdown
cancels relays and closes owned processes. Remote control uses independent
requests so a busy data channel need not block interruption. A disconnected
channel is retired, not silently reattached or restarted. Lost status is an error,
not a guessed exit code.

## Bash plugin example

Declare `BASH` from `the_framework.plugins.bash` in an application. For standalone
code, add `BashExtension`. This sequence invokes its real local worker, temporary
file edits and job lifecycle, but bypasses model generation and the FunctionCall
feedback/commit pipeline described in [Tools](tools.md):

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from the_framework.agent import Agent
from the_framework.plugins.bash import BashExtension

with TemporaryDirectory() as directory:
    agent = Agent(client=object(), bash={
        "default_cwd": directory,
        "inherit_interactive_environment": False,
        "max_timeout_seconds": 10,
    })
    bash = agent.add_extension(BashExtension)
    try:
        bash.write([{"path": "report.txt", "content": "pending\n"}], target="local")
        bash.edit([{
            "path": "report.txt", "old_string": "pending", "new_string": "ready",
            "replace_all": None,
        }], target="local")
        result = bash.command("cat report.txt", timeout_seconds=5, target="local")
        assert result["exit_code"] == 0 and result["stdout"] == "ready\n"
        assert result["status"] == "completed"
        started = bash.job("printf 'part one;'; sleep .05; printf 'part two'",
                           timeout_seconds=5, target="local")
        finished = bash.wait(started["job_id"], timeout_seconds=5, target="local")
        assert finished["status"] == "completed"
        assert finished["stdout"] == "part one;part two"
        assert bash.check(started["job_id"], target="local")["exit_code"] == 0
        python = bash.python("print(6 * 7)", timeout_seconds=5, target="local")
        assert python["exit_code"] == 0 and python["stdout"] == "42\n"
        assert "status" not in python
        assert Path(directory, "report.txt").read_text() == "ready\n"
    finally:
        bash.shutdown()
```

The selected Bash client starts lazily and keeps a separate worker per target.
Configuration is captured at its launch; changing an extension config does not
retroactively rebuild an existing worker. Requests on one client serialize; a
blocking command can occupy it, so use `job` for work that needs subsequent checks.
A lost worker makes its jobs unavailable; the extension does not silently launch
a replacement client and replay the command. Shutdown/deactivation closes clients.

### Tools and result shapes

| Tool | Behavior |
| --- | --- |
| `write(writes, target=...)` | Whole UTF-8 snapshots; paths relative to configured cwd |
| `edit(edits, target=...)` | Exact-string replacement, not patch/fuzzy matching |
| `command(command, cwd=".", timeout_seconds=None, target=...)` | Blocking `/bin/bash -c`; result has terminal `status` |
| `python(code, cwd=".", timeout_seconds=None, target=...)` | Fresh selected worker's `sys.executable -c`; no persistent Python namespace; result omits `status` |
| `job(command, ..., target=...)` | Returns `status="started"`, `job_id`, cwd and deadline setting |
| `check(job_id, target=...)` | Current accumulated snapshot; repeatable, not a destructive read |
| `wait(job_id, timeout_seconds=None, target=...)` | Waits within the current agent turn; does not extend job lifetime |
| `interrupt(job_id, target=...)` | Terminates process group and returns final observable snapshot |

Command/check/wait results include `exit_code`, `stdout`, `stderr`, `timed_out`,
`cwd`; job snapshots add `job_id`. Status is `running`, `completed`, `failed`,
`timed_out` or `interrupted`. An acknowledgement of starting a job is not success.
`wait` also reports `wait_reason`; steering/interruption can end that wait without
being job completion. The native Python tool's smaller result shape above is
intentional documentation of the current API, not a universal command-result
schema. Generic tool acknowledgements are another layer entirely.

### Files and atomicity

Each write replaces one file through a same-directory temporary UTF-8 snapshot,
flush/fsync and `os.replace`. Existing permission bits are preserved; new files
use 0600. Parent directories are created. A list of writes/edits is processed
**sequentially, not as a multi-file transaction**: earlier entries can commit
before a later one fails. Edits default to exactly one match; empty/no-match or
multiple matches fail unless `replace_all=True`. In the strict tool schema,
`replace_all=None` selects the ordinary single-match default. Prefer absolute
paths when acting across distinct working directories. Relative paths resolve
under configured `default_cwd`, not a previous tool's shell cwd.

These are real account permissions: absolute paths and `..` are allowed, and
symlinks resolve before writing. Cwd is a convenience, not an access jail. Read
before replacing; preserve unrelated changes. Do not send secrets through command
output. Tool schemas constrain shape, not user authorization for file deletion,
external messaging, publication or privileged operations.

### Bounds, output and interruption

`BashConfig` defaults:

| Setting | Default |
| --- | --- |
| `default_cwd` | `.` |
| `inherit_interactive_environment` | `True` |
| `max_timeout_seconds` | `900` |
| `default_wait_seconds` / `max_wait_seconds` | `10` / `60` |
| `max_concurrent_processes` | `4` |
| `max_process_records` | `32` |
| `max_output_chars` | `50_000` per stream |

A supplied execution timeout must be an integer 1..maximum; `None` means **no
forced timeout**, not 900 seconds. A wait accepts 0..maximum and is separately
bounded. Output retains a prefix per stream then appends a discarded-character
marker; it is not a live tail and not complete logs. Large output can also be
truncated by the outer tool pipeline. Redirect complete logs to authorized files.
Bash protocol frames are bounded to 16 MiB. Limits are not OS disk/RSS/network
quotas; child scripts can use other resources unless the host enforces them.

Background records survive calls/turns only while their target worker lives.
Starting more jobs prunes completed records; running jobs are not pruned to meet
the record target. Blocking commands remove their records after collection.
Checks of unknown/expired IDs fail. There is no durable completion wake: the agent
must check or use a separately authorized scheduling mechanism.

Interactive environment inheritance runs `/bin/bash -ic` once to capture its
environment (ten-second attempt), with inherited environment fallback. Commands
then run noninteractively with that captured environment; stdin is unavailable.
Disable inheritance for deterministic tests or when interactive initialization has
unwanted side effects. Python uses the selected Bash worker's application
interpreter, not a separate persistent Pynteract session.

Timeout and explicit interrupt send TERM then KILL to the command's process group.
Agent interruption signals each launched Bash worker to stop its tracked jobs.
EOF/ordinary shutdown also cleans tracked children; hard worker kill, machine
crash or independently detached descendants require separate inspection. Neither
cancellation nor process failure rolls back prior file writes or external side
effects. Verify actual state before retrying.

## Implementation and checks

- Channels/target resolution: `the_framework/execution/{process,launch}.py`.
- Bash agent/config/protocol/engine: `the_framework/plugins/bash/`.
- Host transport: `the_framework/deployment/host.py`; see [Deployment](../deployment.md).
- Tests: `tests/general/test_execution_process.py`, `test_bash_plugin.py`.

[Reference index](README.md) · [Bundled primitives](primitives.md)
