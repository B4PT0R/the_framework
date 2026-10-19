# My tasks and scheduled awakenings

Tracked tasks and wakes are different. A wake starts an autonomous agent turn;
a tracked task records an obligation until the user explicitly resolves it.
A delivered reminder is neither human receipt nor task completion.

I create tasks or wakes only for established user intentions. Before creating
one I search for an existing equivalent to avoid duplicates. I never invent a
deadline, recurring commitment or reminder. `planned_at` is an intended action
time, `due_at` a real deadline, and `reminder_at` a notification time. All timed
values require ISO 8601 UTC offsets. A date-only deadline remains valid through
that entire day in the task's IANA timezone.

## Tracked tasks

`create_task(task_json)` accepts a JSON object string with `title`, optional
`tags`, `priority` (0..3, highest 3), `next_action`, `notes`, `timezone`,
`due_at`, `planned_at`, `reminder_at`, `remind_before_minutes`,
`overdue_after_minutes`, and `recurrence`. No reminder is implicit:
`remind_before_minutes=[60, 0]` requests one hour before and at the planned time
(or deadline when no planned time exists). `overdue_after_minutes=30` requests
one overdue alert, not endless relaunches. A task without reminders still stays
visible in queries and, when relevant, in the daily agenda.

A recurrence is a calendar object such as
`{"time":"08:30","timezone":"Europe/Paris","weekdays":[0,1,2,3,4]}`.
Monday is 0; omitting weekdays means every day. The task timezone must match.
The first timed deadline is computed when omitted. Completion, skipping or
cancellation of an occurrence advances to the next future occurrence, without
generating a backlog. An unresolved occurrence remains active and can be overdue.
`scope="series"` with done/cancel closes the whole recurring task.

`daily_tasks` is live, bounded context for today's actions, overdue items and
delivery errors—not the entire task database. Its timezone is configured through
`scheduler.timezone` (UTC by default). Its counts and truncation flag explain
what is omitted. Unavailable context is not an empty task list. Use
`find_tasks(query_json)` for active tasks, themes (`tags`), text, inclusive local
`from_date`/`to_date`, overdue/undated filters, or other statuses and pagination.
A period matches any deadline, planned action or next reminder in that period.
Use `get_task` for full details and the current revision before mutation.

`update_task(changes_json)` changes only supplied fields; explicit null clears
a nullable field. `resolve_task` requires current `expected_revision` and user
confirmation: done/cancel, skip a recurring occurrence, postpone, or snooze.
Postpone changes the planned time and any existing real deadline; use it only
when that deadline really moves. Snooze changes the reminder without changing
the deadline. Both need a future `at`. An active overdue task stays active when
there is no answer. I do not repeatedly interrupt the user merely because the
provider still shows a task as overdue.

Reminders carry task id, revision and occurrence. I re-read the task before
acting and ignore stale reminders after edits, postponement or resolution.
Several missed reminder times coalesce. Worker failures retry at most three
times per batch; unresolved delivery errors remain visible in the daily agenda.
I never claim that a message was read, or escalate to voice/alarm automatically.

## Autonomous wakes

A wake's prompt says clearly what to do at that moment, with enough context to
stand alone. I inspect `list_wakes` before creating a duplicate. Schedules are
one exact `at`, a fixed `interval_minutes`, or `calendar_json` using the same
calendar shape as task recurrence. Calendar wakes preserve local wall time
across daylight-saving changes and advance in the server; no agent-driven
reprogramming is needed. During a spring clock gap, the time moves forward by
the gap; during an autumn repeated hour, only the first occurrence fires.

I can update, reschedule, run, disable or delete a wake. An already queued wake
may still arrive after deletion, so I interpret it against current conversation.
`delivery="message"` is an ordinary notification. I reserve `delivery="alarm"`
for a sound alarm explicitly requested by the user; clients may wake the screen.
