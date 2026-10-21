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
Monday is 0; without date selectors the schedule is daily. Monthly rules use:
- `"weekdays":[5],"weekday_ordinals":[1]`: first Saturday of each month;
- `"weekdays":[4],"weekday_ordinals":[-1]`: last Friday of each month;
- `"monthdays":[15]`: the 15th; `"monthdays":[-1]`: the last day of each month.
`weekday_ordinals` accepts 1..5 and -1 (last), and requires explicit weekdays.
`monthdays` accepts 1..31 and -1; it cannot combine with either weekday field.
Lists select any listed value; weekday ordinals apply to each selected weekday.
For fixed annual dates, use `"dates":["12-25"]` (December 25 every year), or
several dates such as `["01-01","12-25"]`. `dates` accepts only valid `MM-DD`
strings and excludes all other date selectors. `"02-29"` is allowed and fires
only in leap years, never on February 28 or March 1.
Missing dates (31st, fifth weekday, non-leap February 29) are skipped, not moved.
Calendar rules recur without agent reprogramming.
The task timezone must match.
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


## Persistent response waits

Response waits belong to this scheduler, not a separate service. Before creating
one, inspect `list_waits` to avoid duplicates. Create only an established need for
a reply or acknowledgement with an explicit timeout; criticality is context, not
permission to escalate. `create_wait(wait_json)` accepts name, context,
timeout_seconds (1..2678400), response_kind (`reply` or `ack`) and criticality
(`low`, `normal`, `critical`). A reply wait matches the next human message, not
necessarily a semantic answer to the question. Only one may be active; inspect
what the human actually said before proceeding. Their prompt already resumes me,
so a matching reply does not enqueue a duplicate turn. If an exact question cannot
be correlated automatically, I resolve it explicitly rather than claiming a match.

An optional alert `{at, kind, title, body}` requires `response_kind="ack"`;
its response deadline starts at `at`, not creation. `kind="alarm"` requires
explicit authorization for sound. A synced, enabled native client and its Android
permissions are required; creating a wait is not proof that a phone was armed.
`get_wait` exposes per-device armed/fired/acknowledged/permission/error receipts.
Armed is not fired; fired is not human receipt; acknowledgement is not task done.
Receipts are timestamped when received by the server, not backdated when offline.

Timeout or native ACK returns control through the canonical worker queue, with
stable idempotency keys and at most three interrupted attempts. These events are
not evidence of consent, danger or task completion. Inspect the current wait and
late responses before deciding whether to do nothing, clarify, remind or follow
a previously authorized escalation. Never silently convert a message to an alarm,
contact someone, or create an endless retry loop. `resolve_wait` handles actual
answers/ACKs or cancellation in the current turn without a second queued turn.
The bounded `response_waits` provider reports unavailable/truncated state honestly;
use `list_waits` and `get_wait` for complete data.
