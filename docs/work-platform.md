# Work platform

The console answers four questions: what needs a decision, what is being worked on,
what was delivered, and which automations are enabled. Backend names are not navigation categories.

Primary views:

- Workbench: explicit decision coverage, current agent work, recent agent deliveries,
  tracked commitments, and a compact source status strip.
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
with layer `library` and no endpoint: it is listed, reports whether it imports and
which version, and is never discovered or routed.

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
The script loader preloads a window of four assets while preserving sequential
execution. Downloads overlap without allowing dependent modules to run before
their prerequisites succeed; preload links and script tags use the same URL.
A failed script download gets one retry after 150 ms. Syntax and runtime
errors are not replayed, and repeated download failures keep the same visible
core or optional-panel failure boundary.
