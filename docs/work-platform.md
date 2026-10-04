# Work platform

The console answers four questions: what needs a decision, what is being worked on,
what was delivered, and which automations are enabled. Backend names are not navigation categories.

Primary views:

- Workbench: in order of urgency, a technical-trouble summary strip, interrupted agent work,
  tracked commitments beside current agent work, recent agent deliveries, and a compact source
  status strip. The decision strip is shown only when the projection carries decisions (an
  approval source is connected); it is not a permanent placeholder.
- Work records: searchable work, results and activity. Conversations and model calls are
  supporting records, not proof that a work item was completed.
- Automations: readable enable/disable controls; schedules, execution diagnostics and
  pipeline receipts are subordinate views of those automations.
- Resources: capabilities/configuration, repositories, and storage.
- Diagnostics: technical exceptions and source coverage. A failure does not create a human decision.

## Data ownership

`schedule-reminder` owns the read-only `work-feed` contract: items, recent events,
source roles, persisted execution state and summary. It opens an existing database in
read-only mode, without initialization or migration. The console calls the owner CLI;
it does not query reminder tables. `task-console` remains the owner of task control,
registration, health conclusions and task/pipeline receipts. `llmcall` remains the sole
model execution interface. There is no new scheduler, work database or agent runner.

Library dependencies are not producers. `convo-chain` (the conversation chain),
`llmcall` and `fleet_guards` are pinned in `pyproject.toml` and imported in process
by fixed name; the console borrows their rules, not their records. `convo-chain`
owns the transcript semantics (index, chain, node, export, fork and the "typed by a
person" rule) and reads no environment variable: the console passes
`TASK_CONSOLE_SESSIONS` as `root` and keeps authentication, host and shape checks
on its own routes. Observed producers such as the reminder `work-feed` are the
opposite case: the console calls their CLI or reads their snapshot and never
imports their code.

Each item has a stable ID and a source-defined role: `agent_work`, `tracked_item`, or
`signal`. Email, radar and demand feeds are information inputs; pending feed entries
are not presented as unfinished agent work. Unknown sources remain tracked items,
without asserting agent ownership. Events join only by their persisted item ID.
Task links are not inferred; existing reviewed linkage remains authoritative.

Coverage distinguishes unavailable sources, older schemas, truncated results and empty
connected sources. An old completion note is labeled a recorded result, not verified
execution. The current feed does not establish live process liveness, executable
validation, human decision requests or automatic remediation. Those capabilities are
explicitly unavailable until an owner provides receipts. No repair or retry is started
just because an observation failed.

A task repair order is the one exception in shape, not in principle: it is filed only when a
person clicks repair on one task row, never by an observation. `task_repair.py` adds no runner of
its own. `POST /api/task/repair {name, note, request_id}` re-checks the name against the live task
payload, then calls the reminder owner's `ensure` verb (title `修复计划任务：<title>（<name>）`, source
`task-console-repair`, an idempotency key derived from the task name and request ID, and a
description that carries the console's known facts as reference data plus fixed limits: diagnose
and propose only, change code only in a clone inside the work folder, never touch the live task,
its registration, XML, launcher, backup or any working copy, never delete, push, send or publish,
and write `report.md`). It then reads the owner's work feed for that item, requires an enabled
`agent` offer, and submits it through `work_actions.submit` with the feed's revision, which wakes
the existing drainer. A missing or disabled offer is reported as `agent_offer_unavailable`, not as
success. The source is deliberately not one of the owner's signal sources, which receive no agent
offer. The target task is marked on the description's first line
(`task-console-repair/v1 {"task": "<name>"}`) and in `ext.x_task_console_repair`; the reviewed
linkage key `ext.task_console.task_id` is never written. A second click for a task that already has
an active order returns that order (dispatching it once if it never was). Because every browser tab
sends its own request ID and the owner's similarity check is waived with `--distinct-reason`, the
lookup, `ensure` and dispatch for one task name run under one per-task lock in the server process,
so two concurrent submits cannot both see "no order" and file two. `POST
/api/task/repair/preview {name}` shows the facts, limits and any active order without writing, and
`GET /api/task/repairs` maps task names to their orders for the task rows; an unreadable feed is
reported as unavailable, not as an empty map.

The reply's top-level `uncertain` carries the dispatch receipt's own flag on both the new-order and
the existing-order path, because the page reads only the top level: a receipt that may already be
queued stays a warning that keeps the request, and a definite refusal releases it. A failed receipt
that does not say either way counts as uncertain.

On the page (`static/task-operations.js`) the request ID is generated once per submission and kept
in session storage with the note until the owner answers definitely, so a retry after an uncertain
reply replays the same request rather than filing a second order. The dialog blocks a new
submission only when the task's active order has already been handed to the Agent; an order that
was created but never dispatched (the execution service was not connected, or the last reply was
uncertain) keeps the submit button enabled, labelled as re-submitting that order, so the backend's
dispatch-once path is reachable from the page. A submission is reported as accepted, never as done. The row chip shows where the order stands, not whether the task is
healthy; an agent that finished has produced a proposal, which the chip calls 修复方案已出. Not yet
read, unreadable, read with no orders, and read with orders are four different displays.

## Frontend composition

`work-model.js` provides pure selectors over the owner feed; `workbench.js` composes
small work/result/activity/source projections in multiple views. `navigation.js` owns
view grouping and legacy links. `actions.js` owns visible action availability and the
read-only preview boundary. Existing resource and diagnostic panels retain their
domain behavior; no module is required to own a navigation section.

Keep the current palette (light #EEF1F2 / #FFFFFF, ink #141C22, accent #0E6B75;
existing semantic dark tokens), Microsoft YaHei UI for interface text, and monospace
only for identifiers. Use aligned rows with a clear title, state, time and action.
Avoid explanatory hero blocks, repeated counters and cards for every implementation module.
Functional content starts expanded. Details remain available through explicit record links.

Actions use text labels and normal contrast. Read-only previews expose a visible mode
and explain why mutation controls are unavailable before a click. Busy and inapplicable
states are separate. The backend still enforces all existing authority and confirmation
rules. UI capability hints never grant authority.

## Acceptance

- Failed/blocked/stalled records never populate human decisions automatically.
- Signal feed volume never inflates the count of active agent work.
- Missing sources never become a green zero; timestamps and result coverage remain visible.
- Refresh, filters, legacy links, task controls, themes and exports work across view groups.
- Preview does not send mutations. Synthetic action tests verify the existing control path.
- Owner reads cannot create or migrate a database; malformed/older sources fail visibly.
- Real machine records and browser captures stay outside public repositories.

## Console interfaces

`integrations.py` registers the console's read adapters: their endpoint, owner,
purpose, dependencies and existing action entrypoints. `/api/integrations` and
the **Console integrations** view expose that registry. The client plugin
inventory remains a separate adapter; installing a client plugin does not
register a console adapter.

The framework owns HTTP authentication, navigation and read isolation. Shared
work and task services own records and guarded operations. Business producers
write through the work owner's API. The console never executes a command or
opens a path supplied by a source record. To add an adapter, declare it in
`ADAPTERS`, supply a bounded read function, and exercise authentication,
unconfigured, empty, failed and recovery states. Parameterized reads and writes
remain explicit guarded routes. A library dependency gets an informational row
with layer `library` and no endpoint: it is listed with the installed version, and
is never discovered or routed. The row is not a health check. `convo-chain` is
imported at module level by the server, the session list and the work-context
reader, so without it the console does not start at all; the precondition lives
at install time (the runtime lock must carry its wheel), not in this row.

`source_reads.py` shares concurrent reads per adapter, with at most eight
callers, and retains observation metadata only. Independent sources in an
aggregate use up to four request-scoped workers, released when the request
finishes. Groups accept leaf readers to avoid nested worker pools. Successful
payloads are not cached by time. Readers still own their I/O deadlines.
Mutations invalidate earlier in-flight reads, including uncertain
write responses. Aggregated resource sections isolate failures independently;
catalog failures do not replace task evidence.

The integration inventory's connection status describes a read, not business
health. Source counts are grouped by source identity across roles and states.
Signal sources and other record-origin labels are displayed separately; origin
labels are collapsed initially and do not represent installed plugins. Existing records
do not prove current producer installation or liveness. Without work service
access, source coverage is unavailable, not zero. Without business records,
the framework and other configured adapters remain usable.

The classic frontend still shares core state. `OPTIONAL_PANELS` explicitly
identifies panels with guarded entrypoints and no core initialization side
effects. Their asset failure leaves a local error and core navigation working;
a missing required module still stops initialization visibly. New panels must
meet that boundary before being marked optional.

Initial page reads run when the user opens that view. Explicit refresh repeats
its reads; navigating to another view does not scan unrelated resource stores.
The one exception is the sidebar badges: once per page load, after the opened
view's reads settle, the badge sources that have never been read (tasks,
repositories, system, memory and the model call ledger) are read once, so the
badges have a number before the owner opens each section. A source that was
read already, even unsuccessfully, is not read again; refreshing is the job of
the refresh button.
The script loader preloads a window of four assets while preserving sequential
execution. Downloads overlap without allowing dependent modules to run before
their prerequisites succeed; preload links and script tags use the same URL.
A failed script download gets one retry after 150 ms. Syntax and runtime
errors are not replayed, and repeated download failures keep the same visible
core or optional-panel failure boundary.
