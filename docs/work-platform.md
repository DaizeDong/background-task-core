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
