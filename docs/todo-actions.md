# Todo actions

The work feed now includes recommendations from schedule-reminder. A click submits an existing
registered task or creates one durable Agent work order. The todo shows the returned progress and
result summary, with links between the source todo and work record. Actual work and model calls
remain in the existing owner, Agent runner and llmcall infrastructure.

## Installation bindings

Install matching console and schedule-reminder revisions together, including fleet-guards and
llmcall. The owner CLI and scheduled Agent drainer must use the same schema 5 database, private
data root and run directory. The following are explicit deployment bindings:

| Console setting | Purpose |
|---|---|
| `TASK_CONSOLE_REMINDER_CLI` | Absolute path to the compatible owner CLI |
| `TASK_CONSOLE_REMINDER_DB` | Existing owner database |
| `TASK_CONSOLE_ACTION_WORKSPACE` | Dedicated output directory inside the owner's private companion |
| `TASK_CONSOLE_AGENT_TASK_ID` | Exact registered ID of the existing Agent queue drainer |
| `TASK_CONSOLE_SESSIONS` | Trusted Claude project transcript root for exact session context |
| Controller runtime settings | Existing runtime configuration, private, state and vault roots |

The child CLI receives the database as `--db` and the workspace as `SCHEDULE_ACTION_WORKSPACE`.
Its private companion resolver and `AGENT_CENTER_RUNS` must agree with the scheduled drainer's
environment. Missing execution bindings hide execution offers and state why.

Before upgrading, back up the owner database using SQLite's backup API and verify the backup with
`PRAGMA integrity_check`. Record the current launcher/task bindings. Upgrade a copy first with the
candidate owner's `init`; compare item and event counts and exercise read-only work-feed. Keep
writers and the drainer quiescent during the actual additive upgrade. Switch the owner/drainer and
console together through the existing installation and task Controller interfaces. Do not replace
a modified live checkout. Verify the selected executable revisions, database, run/output roots and
registered drainer target before enabling actions. Restoring the old database is safe only before
new work has been accepted; otherwise retain the new records and fix forward.

## HTTP contract

Authenticated same-origin `POST /api/work/action` and `/api/work/stop` accept exactly
`{item_id, action_id, revision, request_id}`. For stop, `action_id` is the current receipt ID.
The browser never submits commands, task names or filesystem paths. GET requests do not enqueue
work, invoke a model or upgrade a database. `TASK_CONSOLE_READ_ONLY=1` disables action submission.

The owner reserves and deduplicates requests transactionally. The console only dispatches the
owner-selected exact task ID through the existing Controller. A failed queue wake preserves the
queued work. Uncertain task dispatch stays unconfirmed and is never automatically replayed.
The original todo remains open until its owner records completion separately.

Old conversations supply bounded reference context only when an exact UUID is recorded. This does
not resume a provider-native session. Existing todos without exact links can still use their own
content for Agent work. Similar titles never authorize a task or session association.

## Validation

Use generated todos and intercept scheduler, model and notification effects. Exercise concurrent
requests, stale revisions, lost replies, stop during preparation, receipt reload and terminal result
projection. Browser acceptance should cover keyboard controls, narrow screens and both themes.
An isolated test's simulated completion is UI/transport evidence; it does not prove a real Agent
has satisfied the todo. Final live acceptance uses one explicitly selected real todo after activation.
