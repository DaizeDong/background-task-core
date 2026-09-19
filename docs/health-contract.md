# Component observations and health, version 1

`task_console.health.evaluate_task(spec, observations, now)` is the pure evaluator.
It reads no files and runs no commands. `now` is Unix seconds or an ISO timestamp
with a timezone. Task kind is `oneshot` or `daemon`. A bounded running oneshot may
have missing outputs until its deadline; an overdue one stays execution `running`
until a collector has termination evidence. Daemons still require observations.

The evaluator accepts a compiled T11 `TaskSpec` dataclass or its JSON dictionary.
Nested `checks[].legacy` policy is consumed directly. For a structured T11
`watched_elsewhere` binding, supply `observations.external_checks[observation_ref]`
as a captured object with the exact bound `component`, `check_id`, a health `state`
and an actual workload `observer`. The reference is a lookup key only; no file or
command is loaded from it. A textual exemption, mismatched identity, absent
observer or launcher observation cannot establish external check coverage.

The JSON response uses `schemaVersion: 1`, `execution`, `health`, `checks`,
`current_run`, `last_success`, `last_success_run_id`, `coverage`, `reason_codes`,
`verdict` and the compatibility `state`. Each declared check retains its `id` and
`check_id`. A missing or ambiguous check stays in the expected count. A zero
denominator is explicitly `coverage.state: zero`, never healthy. Report-level
subcheck counts are accepted only when all declared named checks have observations;
they cannot compensate for a missing named check.

`execution.raw_exit_code` and `execution.code_domain` retain the observation.
`execution.exit_code` is normalized through `rcnorm`. Both `ok_codes` and
`ok_exit_codes` are accepted. Code 2 has no universal health meaning; allowed
codes belong to the task declaration. Busy/deferred must be observed explicitly.
Detached execution codes retain `code_subject: launcher`; a stale workload
observer leaves execution unknown, even when the launcher exited successfully.

An observer is an object with `kind: process|heartbeat`, `subject: workload`,
`observed_at`, `alive` and optional `max_age_seconds`, `check_id`, and
`termination_confirmed`. Collectors must observe the actual workload. A detached
launcher or a textual `watched_elsewhere` exemption alone gives unknown health.
The evaluator does not implement a new process scanner or accept executable
observation instructions. The collector remains constrained by the existing
allowlist and private bindings.

Checks may carry `run_id`, `observed_at`, `checked`, `expected` and `state`.
Mismatched run IDs cannot prove the current run. Future timestamps beyond the
six-minute compatibility clock tolerance are unknown. A fresh log only proves
activity unless explicitly declared success-only. Raw `last_run` version 1
receipts are returned as `last_run_v1` without being rewritten. The evaluator
preserves `last_success` while a new run is active. Updating last-success only
after successful completion remains the producer's responsibility; this reader
never deletes or writes receipts.

## Controller adapter

The controller captures existing component read responses into a private snapshot:

```python
from tools.make_fixtures import health_case
case = health_case()
snapshot = {"schemaVersion": 1, "expected": 1, "tasks": [case]}
```


The runnable fixture generators are `health_case()`, `legacy_health_snapshot()` and
`catalog_snapshot()` in `tools/make_fixtures.py`. Real snapshots belong in private
storage. Snapshot fields containing commands or paths grant no authority.

From a checkout, add `scripts` to `PYTHONPATH` and run:

```powershell
python -m task_console.component_status --input $PrivateSnapshot
```

Or feed JSON on stdin. `--now` fixes the evaluation clock for tests. Exit 0 means
the JSON envelope was evaluated, including unhealthy, unknown and zero coverage.
Exit 2 means reader/schema failure, written as JSON to stderr. There are no
notifications, payload execution, maintenance actions or state writes in this CLI.
The package also declares the `task-console-status` entrypoint for the controller's
normal installation process; no installation is needed for the checkout command.

The monitor compatibility input uses `schemaVersion: 1`, `declarations`, `rows`
and `artifact_observations`. Rows use existing freshness scheduler fields
`state`, `last_rc`, `last_run`, `next_run`, `missed_runs`; timestamps are epoch
seconds. Artifact observations map existing declared paths to `{mtime, reason}`.
The CLI only looks up those captured observations; it does not stat paths found
inside the report. The result retains legacy `tasks` and `summary` fields.

CONFIG owns the PowerShell monitor integration. Capture scheduler/artifact facts
within its existing observation scope, call this CLI, check the reader exit code,
then display or log the returned `state`, `verdict`, `reason_codes`, and coverage.
Do not repeat thresholds in PowerShell. Existing text-history ingestion remains
unchanged. No task IDs are treated as migrated, and no authority epoch is created.

## Console and catalog

The launcher may set `TASK_CONSOLE_STATUS_SNAPSHOT` to the private component
snapshot and `TASK_CONSOLE_CATALOG_SNAPSHOT` to a captured SMITH catalog. The latter
consumes `skill_smith.catalog`'s documented `schema_version: 1` envelope with
`records`, `coverage`, `problems` and typed entrypoints. It never imports SMITH or
rescans business repositories. A component snapshot can also embed `catalog`.

`GET /api/components` and `/api/maint`'s additive `components` field use this reader.
Existing maintenance fields and action dispatch are preserved. Catalog entries
show source/entrypoint relationships, kind, client, independent status dimensions,
dependencies and synchronization observations. An unchecked synchronization state
is unknown. Authentication stays on each MCP/App binding; file presence and plugin
enablement never imply authentication.
Existing task-table `watched` percentages still describe registration. Current
observation/check coverage is reported separately by the evaluator, so a declared
external monitoring relationship cannot turn missing workload evidence green.

Disk and memory readers now publish backend verdicts. The frontend presents those
verdicts, including warnings when a source is missing. Existing numeric fields and
operations remain available.

## Static assets

`console.html` holds the DOM, token meta bootstrap and local asset links. `app.js`
loads classic script modules sequentially from its explicit dependency list.
They retain the existing shared page state and load `events.js` last, preserving
the order of document listeners. This uses no framework, bundler, evaluation of
downloaded strings, or duplicated panel implementations. Scripts, styles and
panels are package data.

The server serves an exact static allowlist with JavaScript/CSS MIME types.
Host validation precedes all reads; lexical membership precedes filesystem
resolution, and resolved targets must remain inside `static`. The bootstrap token
appears only in the HTML response, never in static files or URLs. A missing or
failed module produces a visible alert and stops loader readiness. Loopback,
token, action whitelist and two-phase repository publish controls remain in the
existing server/action paths.
