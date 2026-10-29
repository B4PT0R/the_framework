# Persistent work and scheduling

[Documentation](../README.md) / [Reference](README.md)

Goals, tasks, wakes and response waits solve different problems. Choose by the
state you need to preserve, not by which tool happens to produce a notification.
Both plugins are optional and session-scoped; neither grants permission for the
work described in its payload.

| Mechanism | Owner | Persists until / resumes by |
| --- | --- | --- |
| Goal | `AGENTIC` | Explicit completion/cancellation; active goals request low-priority continuations |
| Tracked task | `SCHEDULER` | Explicit task resolution; optional reminder times do not complete it |
| Wake | `SCHEDULER` | One-shot, interval or calendar schedule; submits a future worker prompt |
| Response wait | `SCHEDULER` | Reply, acknowledgement, timeout or cancellation; records outcome and delivery |
| In-turn `agentic.wait` | Worker | Elapsed time, steering or interruption; no durable deadline |
| Process job | Bash/execution worker | Process completion/cancellation; not a scheduled agent continuation |

For process jobs see [Execution](execution.md); for the canonical command queue
see [Hosting](hosting.md) and [Conversation transports](conversation.md).

## Agentic goals

Install `the_framework.plugins.agentic.AGENTIC`. It contributes
`AgenticExtension`, a session service and bridge topic `agentic`; its default
store is `agentic.json` beside the canonical session. Override
`BuildContext` plugin option `agentic.state_path` to choose another path. State
is private application data, not source code or a second conversation log.

Only **one open goal** may exist per session: active or suspended. A goal has an
objective, authorized scope, 1–30 success criteria, 1–100 ordered subtasks,
revision, summary, outcome and continuation bookkeeping. Task IDs survive
reordering; a task's status is `pending`, `in_progress`, `completed` or `skipped`.

| Worker tool | Contract |
| --- | --- |
| `create_goal(goal_json)` | JSON object with `objective`, `scope`, `success_criteria` string list and `tasks` title list |
| `get_goal(goal_id)` | Full record, including evidence and closed goals |
| `list_goals()` | Compact last 50 records, total and truncation indicator |
| `update_goal_progress(goal_id, expected_revision, progress_json)` | Only `summary` and/or complete ordered `tasks` list; preserve existing IDs, omit ID for new tasks |
| `suspend_goal(goal_id, expected_revision, reason)` | Stop continuations without discarding work |
| `revise_goal(goal_id, expected_revision, user_instruction, changes_json)` | Explicit human correction/resume/cancel; changes limited to objective/scope/criteria/status |
| `complete_goal(goal_id, expected_revision, evidence)` | Active goal only; every task completed or explicitly skipped with evidence |

A progress update cannot expand scope. Completed/skipped tasks require nonempty
evidence (up to 8000 characters each); `complete_goal` also requires overall
verification evidence. These checks validate structure and nonempty evidence,
not whether the claimed tests actually ran. Authorization, truthful reporting
and verification still belong to the agent/application. Likewise, a nonempty
`user_instruction` is an audit field, not independent proof of human consent.

This offline service example performs real temporary persistence and admission
checks. Its delivery callback only collects commands; **nothing enters a live
conversation queue** and no model runs:

```python
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from the_framework.plugins.agentic.service import AgenticService

async def goal_example():
    with TemporaryDirectory() as directory:
        delivered = []
        async def deliver(command):
            delivered.append(command)

        service = AgenticService(Path(directory) / "agentic.json", deliver,
                                 retry_interval=0)
        goal = await service.handle("create", {
            "objective": "Document the release procedure",
            "scope": "Documentation only; do not deploy",
            "success_criteria": ["Procedure checked against release code"],
            "tasks": ["Inspect and document the release flow"],
        })
        await service.dispatch()
        command = delivered[-1]
        assert command.admission == "agentic"
        assert (await service.handle("admit", {}, command_id=command.id))["allowed"]
        goal = await service.handle("suspend", {
            "goal_id": goal.id, "expected_revision": goal.revision,
            "reason": "Need the release owner's decision; source inspection is saved.",
        })
        assert goal.status == "suspended"
        assert not (await service.handle("admit", {}, command_id=command.id))["allowed"]
        # Reopen without allowing background dispatch from this example.
        reopened = AgenticService(service.path, deliver, enabled=lambda: False)
        await reopened.start()
        try:
            saved = await reopened.handle("get", {"goal_id": goal.id})
            assert saved.status == "suspended"
            assert saved.scope == "Documentation only; do not deploy"
        finally:
            await reopened.stop()

asyncio.run(goal_example())
```

### Continuations and recovery

The service persists a pending command ID before submitting a `PromptRequest`
with `priority=100`, `admission="agentic"` and developer-kind
`goal_continuation`. Retries reuse that pending ID. The worker checks admission
when work executes; suspension/closure or disabled binding makes stale queued
work inert. Guarded prompts cannot steer an active turn. Normal human requests
have priority over this low-priority continuation.

The server integrates the service with the existing runtime submit/observe path,
not a second executor. Three continuation command failures, three continuation
turns without a changed progress checkpoint, or three failed delivery attempts
suspend the goal. Repeating identical summary/task content does not count as
progress. A suspended goal occupies the open slot and requires an actual human
instruction to resume/correct/cancel. Restart/retry deduplication is **not** an
exactly-once guarantee for external tool effects: inspect what actually happened
before retrying a command that might have written or sent something.

State-load errors fail closed; they do not replace unreadable work with an empty
writable store. Persistence failure marks the service unavailable until reload,
because a cancelled thread await may leave a write in progress. The plugin's
optional System integration pauses dispatch while waiting for a voice restart
resume. No wake needs to be created to keep an active goal running.

### Context and immediate turn controls

`current_goal` is a refreshed bounded projection: absent when there is no open
goal, explicitly unavailable on bridge failure. It omits completed-task evidence
and truncates large records with a lookup hint. Use `get_goal` for full scope,
criteria and evidence before acting on a truncated projection.

`turn_timing` projects application-provided turn measurements, if any.
`context_occupancy` is text-only: last prepared request's local token estimate,
configured budget, occupied percentage, automatic threshold and pending manual
compaction flag. It is not backend billing or voice context usage. Before a
positive estimate/budget exists it reports unavailable, not zero; it does not
resample providers to measure the new request being assembled.

- `end_turn()` requests graceful completion after the current tool batch.
- `wait(timeout_seconds=None)` keeps IPC responsive while waiting for activity;
  defaults to 30 seconds and accepts 0 through configurable `max_wait_seconds`
  (default 3600). It returns elapsed/steering/interruption information, not a
  durable scheduler record.
- `compact()` requests the existing canonical compaction before the next model
  step, after all tool-batch results are recorded. Repeated requests coalesce.
  Success clears the flag; errors propagate. The request can remain pending
  across turn/interruption boundaries in the same worker, not a worker restart.
  Preserve required state first; an acknowledgement is not completed compaction.
  See [Context](context.md).

## Scheduler ownership and configuration

Install `the_framework.plugins.scheduler.SCHEDULER`. Its session service owns
`scheduler.json` beside the session, overridable with plugin option
`scheduler.state_path`. The version-1 document holds wakes, tasks and waits;
older wake-only documents can omit task/wait lists. Unreadable state is an
unavailable service, not an empty agenda to overwrite.

`SchedulerConfig` defaults:

| Field | Default / validation | Used for |
| --- | --- | --- |
| `timezone` | `UTC`; IANA name validated with `ZoneInfo` | New tasks, queries and daily agenda, not automatic user geolocation |
| `agenda_limit` | 12; range 1–30 | Bounded live agenda |
| `max_wakes` | 64; range 1–256 | Creation limit including disabled retained wakes |
| `min_interval_minutes` | 1; positive integer | Minimum fixed wake interval |

The server service owns clock polling and recurrence. Merely declaring a plugin
does not create reminders. Disabling the agent binding hides its tools/providers,
**not** the installed scheduler clock/service; explicitly update/delete unwanted
wakes rather than treating unbinding as cancellation. Service stop/reset pauses
polling; persistent state remains for restart. Timely agent execution requires a
running server and available worker; a native client may execute pre-synced alerts
independently, subject to its platform constraints.

## Tracked tasks

`create_task(task_json)` accepts an extensible JSON object string; creation fields
are `title`, `tags`, `priority`, `next_action`, `notes`, `timezone`, `due_at`,
`planned_at`, `reminder_at`, `remind_before_minutes`, `overdue_after_minutes`,
and `recurrence`. `priority` is 0–3 (3 highest). No reminder is implicit.

- `due_at`: real deadline, offset datetime or ISO date. A date-only deadline is
  valid throughout that local day and becomes overdue at the next local midnight.
- `planned_at`: intended action time, not a changed deadline.
- `reminder_at`: one explicit notification time.
- `remind_before_minutes`: offsets from planned time, otherwise deadline;
  requires an anchor. `[60, 0]` means an hour before and at the anchor.
- `overdue_after_minutes`: one overdue notification, requiring a deadline.

All timed values require UTC offsets; naive datetimes are rejected. Recurring
calendar times instead use an IANA timezone and unambiguous `HH:MM` local rule.

This example calls the real scheduler service in an isolated directory. Polling
is inactive and its callback rejects unexpected delivery. It checks persisted
snoozing without changing the deadline, then reopens the store:

```python
from datetime import datetime, timedelta, timezone
from the_framework.plugins.scheduler.service import SchedulerService

async def task_example():
    with TemporaryDirectory() as directory:
        async def no_delivery(command):
            raise AssertionError("this example must not dispatch notifications")

        path = Path(directory) / "scheduler.json"
        service = SchedulerService(path, no_delivery)
        await service.start(active=False)
        try:
            now = datetime.now(timezone.utc)
            due = (now + timedelta(days=2)).isoformat()
            task = await service.handle("task_create", {
                "task_id": "release-review", "task": {
                    "title": "Review release notes", "timezone": "UTC",
                    "due_at": due, "reminder_at": due, "tags": ["release"],
                },
            })
            snoozed = await service.handle("task_resolve", {
                "task_id": task["id"], "expected_revision": task["revision"],
                "action": "snooze", "at": (now + timedelta(days=1)).isoformat(),
            })
            assert snoozed["due_at"] == task["due_at"]
            assert snoozed["revision"] == task["revision"] + 1
            assert snoozed["status"] == "active"
        finally:
            await service.stop()
        reopened = SchedulerService(path, no_delivery)
        await reopened.start(active=False)
        try:
            saved = await reopened.handle("task_get", {"task_id": "release-review"})
            assert saved["reminder_at"] == snoozed["reminder_at"]
            assert saved["due_at"] == task["due_at"]
        finally:
            await reopened.stop()

asyncio.run(task_example())
```

The model-facing tools route to these service methods through bridge topic
`scheduler`, adding IDs and configured timezone defaults. The example bypasses
that transport and makes no claim about inference or human confirmation.

### Query, edit and resolve

`get_task(task_id)` returns full state, revision, occurrence and recent history.
`find_tasks(query_json)` returns compact rows and total/offset/limit; query fields
are status (`active` default, `done`, `cancelled`, `all`), tags, text,
`from_date`, `to_date`, timezone, overdue, undated, limit (1–100), offset.
Tags match as a normalized subset; text searches title/notes/next action.
Inclusive date filters match any deadline, planned time or next reminder.

`daily_tasks` carries overdue tasks, past/today actions or reminders and delivery
errors, bounded by `agenda_limit`, with active/relevant counts and truncation.
It is not the task database. Bridge errors report unavailable, not no tasks.

`update_task(task_id, changes_json, expected_revision)` preserves omitted fields;
null clears nullable fields. Resolution also requires the current revision:

| `resolve_task` action | Effect |
| --- | --- |
| `done` / `cancel` | Close a one-off task; for recurrence, resolve just this occurrence by default |
| `skip` | Advance a recurring occurrence without completion |
| `postpone` | Move planned time **and any existing deadline** to future offset `at` |
| `snooze` | Move reminder to future `at`, preserving deadline |
| `done` / `cancel`, `scope="series"` | Close a recurring series |

`postpone`/`snooze` require occurrence scope and future `at`; other actions reject
`at`. A recurrence advances to the next occurrence after both now and the resolved
deadline, without generating a backlog. An unresolved overdue occurrence remains
active. Records retain 32 recent history entries, not an unlimited audit trail.
The service caps total stored tasks at 2000. Reminders coalesce missed times into
a pending batch; up to three failed worker attempts per batch are recorded, after
which `last_error` remains visible. Successful delivery never resolves the task.

## Wakes

`create_wake` requires name/prompt and exactly one schedule:

| Schedule arguments | Meaning |
| --- | --- |
| `at` | One offset datetime; a past instant is due immediately, not interpreted as a future recurrence |
| `interval_minutes`, optional `first_at` | Fixed interval; first run defaults to now plus interval |
| `calendar_json` | JSON calendar object with local wall-clock rules below |

`first_at` is valid only with intervals. Default delivery is `message`;
`alarm` requires explicit sound authorization and a capable native client.
`list_wakes` includes disabled one-shots. `update_wake` changes label, prompt,
delivery, enabled state or audio; `reschedule_wake` replaces the schedule and
enables it; `run_wake_now` makes it due without deleting its definition;
`delete_wake` permanently removes it. Search/list before creating an equivalent.

A wake submits a stable occurrence ID to the canonical worker. One-shots are
disabled after completion; repeating wakes advance beyond now, skipping missed
runs. An interrupted worker command creates a new delivery attempt for the same
due time; ordinary failure records an error and advances. Unlike bounded task
and response-wait attempts, wake interruption attempts and ambiguous transport
resubmissions are **not subject to the same three-attempt cap**. Deleting or
changing a wake cannot retract a prompt already accepted by the worker; recheck
current conversation/state before acting on it. Delivery is not human receipt.

## Calendar rules

Tasks' `recurrence` and wakes' decoded `calendar_json` use the same validator and
next-occurrence calculation. Require `time` (`HH:MM`, no offset) and `timezone`
(IANA). Monday is 0; no date selector means every day.

| Optional selectors | Example meaning |
| --- | --- |
| `"weekdays":[0,1,2,3,4]` | Weekdays |
| `"weekdays":[5],"weekday_ordinals":[1]` | First Saturday each month |
| `"weekdays":[4],"weekday_ordinals":[-1]` | Last Friday each month |
| `"monthdays":[15]` / `[-1]` | 15th / last day of each month |
| `"dates":["12-25","01-01"]` | Annual paired month/day dates |
| `"dates":["02-29"]` | Leap day, only in leap years |

`weekday_ordinals` requires explicit weekdays and accepts 1–5 or -1;
`monthdays` accepts 1–31 or -1 and excludes weekday fields. `dates` excludes all
other date selectors. Lists are unions; duplicate values collapse. Missing dates
(31st, fifth weekday, non-leap February 29) are skipped, not clamped. The timezone
must match a recurring task's timezone; its first timed deadline is computed if
omitted. Calendar rules preserve local wall time: spring gaps move forward by
the gap, autumn repeated times fire once at the first occurrence.

These deterministic historical instants test rule calculation only; they do not
schedule live notifications or depend on today's date:

```python
from the_framework.plugins.scheduler.calendar import next_calendar

spring = next_calendar(
    {"time": "02:30", "timezone": "Europe/Paris"},
    datetime(2026, 3, 28, 23, tzinfo=timezone.utc),
)
assert spring == datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)  # 03:30 local
leap = next_calendar(
    {"time": "10:00", "timezone": "UTC", "dates": ["02-29"]},
    datetime(2025, 1, 1, tzinfo=timezone.utc),
)
assert leap == datetime(2028, 2, 29, 10, tzinfo=timezone.utc)
```

## Persistent response waits

`create_wait(wait_json)` records name, context, `timeout_seconds` (1–2678400),
`response_kind` (`reply` default or `ack`) and criticality (`low`, `normal`,
`critical`). There may be only one active next-reply wait. It does not block the
current turn. Timeout means a deadline passed, not consent, danger or task done.

- `reply` matches the next accepted human prompt or canonical Realtime user
  message, **not a semantic answer**. Inspect the actual text before continuing.
  The human message already resumes the agent, so no duplicate turn is queued.
- `ack` expects an explicit acknowledgement. Optional native
  `alert={at, kind, title, body?, audio_src?}` requires ack mode; its timeout starts
  at `alert.at`, rather than wait creation. `kind` is message or alarm.
- `resolve_wait(wait_id, action, response=None)` permits answered, acknowledged
  or cancelled, with mode checks. The current turn handles explicit resolution;
  no extra result turn is queued. Use actual human evidence, never silence.
- `list_waits(active_only=True)` includes waiting records and pending outcome
  deliveries; `get_wait` provides complete context, outcome and device receipts.
  The live `response_waits` provider limits to 12 records, truncates long strings
  and omits detailed alerts/receipts. Empty output is absent, not an error.

The pure `WaitBook` below demonstrates that a reply can be unrelated to the
question, and a late reply does not turn a timeout into consent. Persistence and
worker delivery belong to the service, not this in-memory helper:

```python
from the_framework.plugins.scheduler.waits import WaitBook

clock = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
book = WaitBook()
spec = {"name": "Review response", "context": "Asked whether to revise a draft.",
        "timeout_seconds": 60, "response_kind": "reply"}
book.create("review", spec, clock)
reply = book.reply("Let us talk about something else.", clock + timedelta(seconds=10))
assert reply["status"] == "answered" and reply["delivery_state"] == "done"
book.create("second-review", spec, clock)
late = book.reply("Yes", clock + timedelta(seconds=60))
assert late["status"] == "timed_out"
assert late["late_response"]["value"] == "Yes"
assert len(book.due(clock + timedelta(minutes=2))) == 1
```

Timeout/native ACK outcomes return through stable result command IDs. Interrupted
result delivery retries at most three times; other worker failures become a
failed delivery. Ambiguous transport submissions can retry the same ID. Late
responses remain attached to the original outcome; they do not reopen it.
Recent settled outcomes are retained, with pruning of old outcomes during new
wait creation (30-day cutoff) and a 512-record cap. This is not an unlimited
response history.

### Native alerts and audio evidence

The service exposes read-only native-alert projections for application clients;
it does not itself open a phone notification channel. `native_alerts()` covers
known occurrences up to seven days ahead (bounded to 512); clients do not invent
recurrences. A product must supply authenticated sync/receipt routes, pairing,
client capabilities and OS permissions. Browser chat alone is not proof of
native alarm support. See the larger consumer pointers in [Examples](examples.md).

Wait receipts are per device: `armed`, `fired`, `acknowledged`,
`permission_denied`, `error`, `auto_stopped`, `audio_ready`, `audio_unavailable`.
Their first server-received timestamps are retained; offline events are not
backdated. `armed` is not fired, fired is not human receipt, acknowledgement is
not task completion. Acknowledgement after the deadline remains a late response.
`criticality` adds context, not permission to sound an alarm, contact someone,
open a microphone or escalate.

`audio_src` accepts a plain same-origin resource path only for alarms, not
external URLs, credential/query URLs or traversal. It identifies application-
prepared audio; the scheduler does not synthesize speech. A capable client must
preload/cache it: `audio_ready` confirms caching, not playback; missing audio may
fall back to that client's ringtone. Wake update `audio_src=null` leaves audio
unchanged and `""` restores the default sound. Never test audible delivery without
consent, and do not infer deep-sleep or offline guarantees from a created record.

## Verification and source map

The Python sequences are executed by `test_documented_agent_contracts.py` without
model calls, native alerts or real notifications. They cover goal admission and
reload, task snooze/reload, calendar edge cases and reply/timeout semantics.
Focused suites under `tests/general/`: `test_agentic_plugin.py`,
`test_agentic_worker.py`, `test_agentic_context_controls.py`,
`test_agentic_loop_control.py`, `test_agentic_turn_controls.py`, plus
`test_scheduler*.py` (tasks, waits, annual/calendar rules, audio and plugin).
Implementation ownership is `plugins/agentic/{agent,server,service}.py` and
`plugins/scheduler/{agent,server,service,tasks,waits,calendar}.py`.
