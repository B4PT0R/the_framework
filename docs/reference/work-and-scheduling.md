# Persistent work and scheduling

[Documentation](../README.md) / [Reference](README.md)

Use this to distinguish goals, tracked tasks, timed wake-ups and response deadlines. These are optional plugin features, not permissions for an agent to act.

## Agentic: persistent work and turn controls

`AGENTIC` owns persistent goals and the agent's immediate `wait` / `end_turn`
controls. Its `current_goal` provider exposes one open goal per conversation,
including suspended work; unavailable state is explicit. `turn_timing` projects
optional application-owned turn measurements. Idle waits are cancellable within
one turn, not durable scheduler deadlines.

Its text-only `context_occupancy` provider reports the last prepared request's
local input estimate, configured budget, occupied percentage and automatic
compaction threshold. The estimate can lag the context being assembled; before
the first estimate it is unavailable, not zero. It never resamples providers or
persists telemetry in the conversation. It is not the voice engine's context.
`agentic.compact` requests the existing canonical compaction at the next model
step, after the entire current tool batch is recorded, then continues the same
turn. Repeated requests coalesce, execution failures propagate, and the request
is cleared only on success. It remains pending across a turn boundary or
interruption in the same worker, but is not durable across worker restarts.
Prefer deliberate compaction at a substantially different task's start when most
previous detail is irrelevant, after preserving necessary state; a topic change
alone is not a mandate to compact. No new compaction backend or queue is added.

A goal records the authorized objective, scope, success criteria and an evolving
ordered todo. Progress tools preserve existing task identities, require evidence
for completed/skipped tasks and use revision checks. The agent may suspend itself
with a reason; resumption, correction and cancellation require an explicit human
instruction. Completion requires verification evidence and resolved subtasks.

The session service stores `agentic.json` beside the canonical session (override
`agentic.state_path` through `BuildContext`). Its durable outbox submits low-priority
continuations to the existing worker, with stable command IDs across retries and
restarts. `PromptRequest.admission` names a bridge capability whose `admit` method
must return `{"allowed": true}` at execution time; stale continuations fail closed.
Guarded prompts never steer existing turns, and human prompts take priority.
Suspension, closure or a disabled binding stops dispatch and invalidates queued
work. Three stagnant turns or failed continuation/delivery attempts suspend the
loop. External side effects still require inspection before retries: this is not
an exactly-once guarantee for tools. No goal grants additional permissions.

## Scheduler: tasks, wake-ups and response deadlines

`SCHEDULER` keeps tracked tasks, autonomous wakes and response waits in its session-owned
`scheduler.json`. Existing wake-only documents remain readable. Tasks never
complete merely because a reminder command finishes. They retain active/done/
cancelled state, a revision, a distinct recurring occurrence and 32 recent
history entries. Edits and resolution require `expected_revision`; obsolete
queued reminders are revalidated by the agent before acting.

Agent configuration `scheduler.timezone` is an IANA name (default `UTC`), used
for the daily agenda, query dates and newly created tasks; `agenda_limit`
(default 12, maximum 30) bounds the live `daily_tasks` provider. That context
reports relevant/active counts and truncation, not a complete task list. Bridge
failure is reported as unavailable, never as an empty agenda. Existing agent
configurations need not change; set the timezone explicitly for local-day views.

The tools `create_task(task_json)`, `get_task(task_id)`,
`find_tasks(query_json)`, `update_task(task_id, changes_json, expected_revision)`
and `resolve_task(task_id, action, expected_revision, at=None, scope="occurrence")`
use JSON object strings for extensible payloads compatible with strict tool
schemas. For example, the JSON passed to `create_task` can be:

```json
{
  "title": "Drop off the completed dossier",
  "timezone": "Europe/Paris",
  "tags": ["admin"],
  "planned_at": "2026-10-19T13:00:00+02:00",
  "reminder_at": "2026-10-19T13:00:00+02:00"
}
```

This creates no real deadline. `due_at` optionally records a deadline (an offset
ISO datetime or a date valid through local midnight), while `planned_at` records
an intended action time. Optional fields include `priority` (0..3), `next_action`,
`notes`, `remind_before_minutes` (relative to planned time, otherwise deadline),
`overdue_after_minutes` (one overdue alert) and `recurrence`. No reminders are
implicit. `find_tasks` filters status, tags, text, inclusive from_date/to_date,
timezone, overdue and undated, with limit/offset pagination. A date range matches
any deadline, planned time or next reminder. Full details come from `get_task`.

Resolution actions are done/cancel, skip for recurring occurrences, postpone
(move planned time and any existing deadline), or snooze (defer the reminder
without moving the deadline). The latter two require a future offset `at`.
Updates preserve omitted fields; null clears nullable fields. A recurring task
stays active until its occurrence is explicitly resolved; done/cancel/skip
advance to the next future occurrence without a missed-occurrence backlog.
Done/cancel with `scope="series"` closes the whole series. Repeated worker
failures are limited to three attempts per reminder batch and remain visible
as an error, not a completed task.

`recurrence` and wake tool `calendar_json` share the calendar shape
`{"time":"08:30","timezone":"Europe/Paris","weekdays":[0,1,2,3,4]}`.
Monday is 0; without date selectors the schedule is daily. Monthly and annual selectors:

| Selector | Meaning |
| --- | --- |
| `"weekdays":[5],"weekday_ordinals":[1]` | First Saturday of every month |
| `"weekdays":[4],"weekday_ordinals":[-1]` | Last Friday of every month |
| `"monthdays":[15]` | The 15th of every month |
| `"monthdays":[-1]` | Last day of every month |
| `"dates":["12-25"]` | December 25 every year |
| `"dates":["01-01","12-25"]` | January 1 and December 25 every year |
| `"dates":["02-29"]` | February 29 in leap years only |

`weekday_ordinals` requires explicit `weekdays` and accepts 1..5 or -1 (last).
`monthdays` accepts 1..31 or -1 and cannot combine with weekday selectors.
`dates` is a nonempty list of valid `MM-DD` strings (including `02-29`),
exclusive of `weekdays`, `weekday_ordinals` and `monthdays`. Each date recurs
annually, not once in a specified year; multiple dates are paired month/day
values, not a Cartesian product of months and days.
Lists are unions; ordinals apply separately to each selected weekday. An absent
31st, fifth weekday or non-leap February 29 is skipped, never clamped.
Duplicate selectors fire only once. For example, the first
Saturday rule is `{"time":"10:00","timezone":"Europe/Paris","weekdays":[5],"weekday_ordinals":[1]}`.
The same validated calculation serves creation, rescheduling, completion and
persistent reload of wakes and tasks. The timezone must match the recurring
task. Its first timed deadline is computed if omitted. Calendar wakes advance
in the server, preserving local wall time across daylight-saving changes;
nonexistent spring times move forward by the gap and autumn folds fire once
at the first occurrence. One-shot `at` and fixed `interval_minutes` wakes
remain available. No frontend or mobile notification delivery guarantee is
introduced by task tracking.

---

[Reference index](README.md) · [Documentation paths](../README.md)
