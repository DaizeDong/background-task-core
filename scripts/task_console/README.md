# task-console

A local, operable maintenance console for one machine. Scheduled tasks, artifact freshness, git
repositories, skills, the memory pool, plugins, disk, saved conversations, and the ledger of a
headless judgment primitive, on one screen, with a button on every row that needs one.

It exists because a report that only tells you something is dead makes you go and find it
yourself. This lets you act on what you just read.

Every panel obeys the same rule: **"nothing wrong" and "never checked" are different outputs.**
A source that cannot be read says so, in its own colour, instead of rendering an empty green
table. The self-check strip at the top exists to make that visible for the page as a whole:
it lists every path the console tried, whether it got there, and how fresh it was.

## Run it

```
python server.py                 # opens http://127.0.0.1:8787/ in your browser
python server.py --port 9000     # different port
python server.py --no-browser    # start the server, open it yourself
```

Windows only. It reads the Windows Task Scheduler; there is nothing to read anywhere else.

## What is on the page

| Panel | Reads | You can |
|---|---|---|
| Self-check | every configured path | see which sources are `ok` / `stale` / `missing` / `unset` |
| Artifact freshness | the health manifest plus each declared artifact | see five states, worst first |
| Repositories | every git repo under one root | spot unpushed commits and dirty trees; run `fetch` |
| Tasks | the Windows Task Scheduler | run, stop, enable, disable, retire |
| Skills | a skills directory | archive one out of the way, or restore it |
| Memory pool | a memory directory | see index headroom and broken links; archive an entry |
| Plugins | `claude plugin list` | enable or disable one |
| Disk | a plugin cache and a session directory | delete abandoned clone staging directories |
| Calls | the LLM-call primitive's append-only ledger | see today's and this week's usage, which rung answered, how long the degraded stretches were, and reorder the fallback chain |

Five states, not two, for freshness. `LastTaskResult` is an HRESULT, not an exit code: one value
means "currently running" and another means "has never run". Treating non-zero as failure marks
healthy tasks red, so `up` / `grace` / `down` / `never` / `running` / `paused` / `unknown` stay
separate, and `unknown` is drawn so it can never be mistaken for `up`.

The call view answers "today" by the calendar day, not by a rolling 24 hours. Asked at eight in
the morning, a rolling window folds in yesterday afternoon, so the number is larger, looks
healthier, and matches nobody's idea of today.

Records written before the ledger carried timestamps cannot be placed in any window at all. They
are reported as excluded, with their count, rather than quietly counted as in-window or silently
dropped: "no calls today" and "a hundred thousand calls that cannot be dated" are different
findings and the page draws them differently.

Served, failed and skipped are three separate columns for one rung. A rung is skipped when that
call's chain did not contain it, which is neither a success nor a failure; before it was counted on
its own it was indistinguishable from a rung that simply never got its turn.

Everything this module returns ends up inside a JSON response, so none of it may hold a value
`json.dumps` cannot take. That is asserted for every public return at once rather than function by
function, because the one that got through was a `Path` on a resolution step that answered `None`
until the day the companion repo was configured: a check written per function only ever covers the
functions someone thought of, and this one was invisible on any machine where the thing worked out
to nothing. The test deliberately configures the companion first, since that is the only state in
which the bug exists.

Who made the call is recorded as a bare script name, never a full path. The ledger sits outside
every repository, but this page serves it over HTTP, and a full path carries a home directory for
no gain in what it tells you. Three states stay separate: a name, "could not be inferred" (an
embedded call with no main script), and "older than this field" (every record written before it
existed). The last two look identical once merged, and merging them turns "the feature just landed"
into "inference is failing".

Degradation is clustered, not spread out. An average dilutes one bad afternoon into a harmless
looking fraction, so the page also draws consecutive runs: how many calls in a row landed on the
same rung.

A fresh artifact does **not** clear a bad exit code by default. Log-shaped artifacts are usually
written on the crash path too, so a task can fail for days while its artifact stays fresh. Only an
artifact declared `artifact_written_only_on_success` earns that power.

## Feed the history first

The run-history panels read a database that nothing fills on its own. Until `console_ingest.py`
has run, those panels are empty, and they say so rather than drawing a flat line.

```
python console_ingest.py --days 60      # first time: pull in what the log still holds
python console_ingest.py --skip-runlog  # the cheap half: health observations only
python console_ingest.py                # the full pass, including run events
```

**Running it on a schedule is not tuning, it is the whole point.** The Windows Operational log is
a circular buffer: measured here at roughly 635 events an hour against 64 MB, which is about five
days before the oldest records are overwritten. Anything not ingested inside that window is gone,
not late, and no later run can recover it.

Drive the two halves separately. Folding them into one caller has been tried and it killed that
caller on every run for dozens of consecutive runs: the full pass hands `runlog.ps1` a 900 second
budget, so any caller whose own execution limit is lower than that will be cut off before the inner
budget is ever reachable, forever, without either number looking wrong on its own.

How often to run each half is a property of the machine and is deliberately not stated here: this
file has carried a wrong claim about it twice, in opposite directions. To find out whether ingest
is currently keeping up, read the ingest freshness the page computes from the data's own
timestamps, not a sentence anyone wrote down.

Reading that log costs around 109 seconds end to end, which is why it is paid once an hour by
something nobody is waiting on rather than on a page load. The ingester is the only writer, takes
`BEGIN IMMEDIATE`, and on a source it cannot read it records a failed run and exits non-zero
instead of writing a partial pass: a half-finished ingest and a successful one would otherwise
produce the same empty-looking chart weeks later.

## Configure it

Copy `categories.example.json` to `~/.task-console/categories.json` and replace the synthetic
names with your own tasks. Everything else is optional.

| Variable | What it points at | If unset |
|---|---|---|
| `TASK_CONSOLE_CATEGORIES` | your category map | `~/.task-console/categories.json`; if that is missing, every task lands in one group called uncategorized, and the page says so |
| `TASK_CONSOLE_STATUS_SNAPSHOT` | captured version 1 component observations | no default; unset means component health is unchecked |
| `TASK_CONSOLE_REMINDER_CLI` | absolute path to the reminder owner's `reminder.py` supporting `work-feed` | no default; work records report unconnected; invoked with the console Python interpreter |
| `TASK_CONSOLE_REMINDER_DB` | absolute path to the existing reminder database | no default; work records and reviewed task links report unavailable; work-feed reads never initialize or migrate it |
| `TASK_CONSOLE_ACTION_WORKSPACE` | absolute private directory for agent workspaces | no default; automatic todo actions remain unavailable |
| `TASK_CONSOLE_AGENT_TASK_ID` | registered owner/task identifier for the agent queue tick | no default; the console cannot wake the agent queue |
| `TASK_CONSOLE_READ_ONLY` | set to `1` to disable mutations | unset leaves configured guarded actions available |
| `TASK_CONSOLE_RUNTIME_CONFIG` | absolute reviewed Controller configuration path | no default; task controls require configuration |
| `TASK_CONSOLE_PRIVATE_ROOT` | absolute private declaration directory | no default; task controls require configuration |
| `TASK_CONSOLE_STATE_ROOT` | absolute registration journal and receipt directory | no default; task controls require configuration |
| `TASK_CONSOLE_VAULT_ROOT` | absolute encrypted snapshot directory | no default; task controls require configuration |
| `TASK_CONSOLE_CATALOG_SNAPSHOT` | captured SMITH catalog schema_version 1 | no default; unset means catalog inventory is unchecked |
| `TASK_CONSOLE_HEALTH` | a health watch list (`task-health.json` shape) | no default; unset means the health-coverage column reads NOT CHECKED |
| `TASK_CONSOLE_ALLOWLIST` | a PowerShell file containing a `$TaskNames = @(...)` backup allow-list | no default; unset means the backup-coverage check reports NOT CHECKED rather than passing |
| `TASK_CONSOLE_HISTORY` | a poll-observation log | unset means the observation columns read NOT CHECKED |
| `TASK_CONSOLE_SKILLS` | a skills directory | unset means the skills panel reads NOT CHECKED |
| `TASK_CONSOLE_SKILL_ARCHIVE` | where archived skills go | unset means the archive button is not offered at all |
| `TASK_CONSOLE_MEMORY` | a memory directory | unset means the memory panel reads NOT CHECKED |
| `TASK_CONSOLE_MEMORY_ARCHIVER` | the existing archiver script | unset means the archive button is not offered |
| `TASK_CONSOLE_REPOS` | a directory holding git repos | unset means the repository panel reads NOT CHECKED |
| `TASK_CONSOLE_VISIBILITY` | a JSON object keyed by `owner/repo` (lowercase), each value `PUBLIC` or `PRIVATE`, or an object with a `visibility` field | unset means every repo's visibility reads unknown and no badge is drawn, which is deliberately hard to tell apart from a repo the table has no row for, so the panel also reports the table it loaded and how many rows matched |
| `TASK_CONSOLE_CODEX` | a second agent CLI's home directory, holding its instruction file, config, session store, logs and cache | no default; unset means that whole half of the maintenance view reads NOT CHECKED, which is deliberately not the same as reading zero bytes |
| `TASK_CONSOLE_PLUGIN_CACHE` | a plugin cache directory | unset means the cache row reads NOT CHECKED and nothing can be deleted |
| `TASK_CONSOLE_SESSIONS` | a session transcript directory | unset means that row reads NOT CHECKED |
| `TASK_CONSOLE_CLAUDE` | the CLI used for plugin actions | falls back to PATH; not found means the plugin panel reads NOT CHECKED |
| `TASK_CONSOLE_ALLOWED_HOSTS` | extra Host header values to accept | only the three loopback spellings are accepted |
| `TASK_CONSOLE_IDENTITIES` | a table of `login\|display name\|commit email`, one per line, `#` for comments, saying which identity each repository owner should be committed under | unset means the account-match column reads NOT CHECKED for every repository, which is deliberately not the same as saying they match; no address from this file is ever rendered, only the account name and the verdict |
| `TASK_CONSOLE_CONVO_CACHE` | where the conversation index caches its scan | unset means the first scan of every page load walks every transcript again; correctness is unaffected, the page is just slower |
| `TASK_CONSOLE_DB` | the SQLite file the ingester writes and the page reads | falls back to the companion repo's `data/task-console/console.sqlite3`, and if no companion resolves it reports UNINITIALISED with setup instructions rather than falling back into this repo |
| `TASK_CONSOLE_LLMCALL_LEDGER` | the append-only JSONL ledger the LLM-call primitive writes, one line per call | falls back to `~/.llmcall/ledger.jsonl`; a missing file makes the call view read NOT CHECKED, which is deliberately not the same as reading zero calls |
| `TASK_CONSOLE_LLMCALL_CHAIN` | the file the call view writes a fallback-chain order into | falls back to `~/.llmcall/chain.txt`. This is the one path on this console that writes into another program's configuration, and it is shadowed by the `LLMCALL_CHAIN` environment variable: when that variable is set, the page says so in as many words instead of reporting a save that changes nothing |
| `TASK_CONSOLE_LLMCALL_BODIES` | the directory holding recorded prompt and reply bodies, one file per day | unset means the console mirrors whatever discovery order the call primitive itself uses to find its private companion directory, and adds a `bodies/` subdirectory to it. That order is defined by the primitive, not here, so it is not restated here either: two copies of an order drift, and the copy someone reads is not necessarily the copy that runs. The resolution steps actually walked, and their verdicts, are printed in the page when a body cannot be found. Body recording is off by default, and the three ways to have no bodies (never turned on, companion not initialised, entry past its retention) are reported as three different sentences rather than one empty box |
| `TASK_CONSOLE_POWERSHELL` | the powershell.exe that task commands run through | falls back to the pinned `System32\WindowsPowerShell\v1.0\powershell.exe`, and only to a bare `powershell.exe` off PATH when that file is not there |

The tool defaults only into its own namespace. Pointing it at whatever else a machine keeps its
watch-list and allow-list in is the launcher's job, and the launcher belongs on that machine.

That last row is the important one. **Unknown and pass are never rendered the same.** A console
that showed a green backup-coverage column because it had nothing to compare against would be
worse than one that showed nothing at all.

A task that appears in no category still shows up, in a group called uncategorized. It is never
dropped: the task nobody categorised is the one nobody is watching.

## What it will not do

It cannot create a task. Creating one correctly means naming it so the backup drift gate can see
it, choosing its settings deliberately, generating its launcher, and registering it in three
places. A button that skipped those steps would manufacture exactly the untracked task that
procedure exists to prevent.

It can **retire** one, which is the opposite operation and is safe to automate precisely because
it is subtractive: disable, write the reason into the description, drop it from the backup
allow-list, drop it from the health manifest. All three, or none. A task that is only disabled is
worse than one that was never touched, because an exported task definition does not record the
disabled state: a backup that still lists it will faithfully reinstall it, enabled.

Pushing is the one thing here that leaves the machine, so it is deliberately **not** one click.
It is two steps: a read-only plan first (which files, which ref, whether that remote is public or
private), and the confirm step must hand back the very file list the plan showed, or the whole
thing is refused. A stray click opens a plan and nothing else. It never uses `--no-verify`, it
never stages with `git add -A`, and whatever the hooks say comes back verbatim.

The full list of what this page can do is the action table in `maint.py`; that table is the
authority and this file does not keep a second copy of it.

## Why it is locked down

It can change system state, so it has several controls, none of them decorative.

(This line used to say "three controls" while the list below had five. A count in prose next to
a list that anyone can append to is a fact with two homes and no reconciliation -- the list grew,
the number did not. So the number is gone: the list is the only place that says how many.)

1. **Binds 127.0.0.1 only.** Nothing off this machine can reach it.
2. **Every `/api/` call needs a token** minted fresh at startup and never written to disk. Without
   it, any web page you had open could POST to `http://127.0.0.1:<port>/api/act` and disable your
   backup task. Being on localhost does not prevent that; a token does.
3. **The verb list is closed** (`enable` / `disable` / `run` / `stop`), the task must be at the
   root path, and the server re-enumerates the live task list and checks membership before acting
   rather than trusting the name it received. The name reaches PowerShell through an environment
   variable, never interpolated into a command string.
4. **A Host header allowlist.** Binding to loopback stops the network; it does not stop DNS
   rebinding, and rebinding is the attack that matters here, because the token is substituted into
   the page at `/`: anything that can make a same-origin request to `/` simply reads it out of the
   HTML. Only the three loopback spellings are accepted, a missing Host is rejected, and `"*"` is a
   separate branch rather than an entry in the list, so no hostname can be spelled in a way that
   turns the check off. The class default is an empty set, so a handler that never went through
   startup refuses everything: forgetting to configure it and deliberately allowing everything must
   not be the same state.
5. **Maintenance actions live on a separate closed table** from the task verbs. One shared table
   would mean that adding a skill action silently widens the task surface, and nobody reviewing the
   diff would notice. Every argument passes a narrow character class, and any path must resolve to
   a direct child of its configured root; failing either refuses the whole action rather than
   sanitising the input, because sanitised input looks safe without anyone knowing what was removed.

Some tasks are owned by SYSTEM or registered at `RunLevel=Highest` and cannot be touched from a
normal user session. The console says so instead of reporting a bare access-denied.

## Files

| File | What it is |
|---|---|
| `server.py` | the HTTP layer: routing, auth, host allowlist, the two write endpoints |
| `console.html` | the whole page: one file, still no build step |
| `vendor/tabler/` | Tabler v1.5.0 (MIT), the dashboard shell. Vendored, not a CDN link |
| `collect.ps1` / `act.ps1` / `runlog.ps1` | the Windows side |
| `evtlog.py` | the fast event log reader (EvtQuery), with the PowerShell path as fallback |
| `freshness.py` | five-state artifact freshness, a pure function of timestamps |
| `selfcheck.py` | every configured source, probed and reported one by one |
| `repos.py` | git repository scan, concurrent, one timeout per repo |
| `maint.py` | the closed maintenance action table and its argument gate |
| `memops.py` | memory pool diagnosis; archiving is delegated, not reimplemented |
| `sysinfo.py` | disk, cache size, abandoned clone staging directories |
| `llmstats.py` | the LLM-call ledger: windowing, per-rung reconstruction, consecutive runs, and the chain config file |
| `retire.py` | the three-place deregistration, planned first and then written |
| `console_store.py` / `console_ingest.py` / `history.py` / `timeline.py` | the run history layer |

## Third-party assets

The page shell (sidebar, top bar, cards, badges) is [Tabler](https://github.com/tabler/tabler)
v1.5.0, MIT licensed. The CSS and JS are vendored under `vendor/tabler/` together with the
upstream `LICENSE`, and served by `server.py` from `/vendor/`.

They are vendored rather than loaded from a CDN on purpose: this console is the thing you open
when something is already broken, and that is the worst moment to depend on the network. The
cost is about 776 KB in the repository, paid once.

Tabler is calibrated for calm, low-density panels, which is the opposite of what a 38 by 19
task grid needs, so the density is pulled back through Tabler's own `--tblr-*` custom
properties (12px body text, halved table cell padding) rather than by overriding its rules.
Tuning through the documented variables is what keeps a version bump from silently undoing it.

The colour palette stays the one this page already had. Tabler's surface variables are pointed
at it, not the other way around: that palette was measured for contrast in both themes on a
dense table, and two palettes in one page always disagree somewhere with no way to tell which
one is right.

`/vendor/` is served without a token, because a `<link>` tag cannot send one and there is
nothing secret in there. That makes "cannot escape the vendor directory" the only control on
that route, so it is tested directly, including percent-encoded traversal.

## Data boundary

This directory ships in a public repo and holds no real state. No snapshot is cached to disk, the
category map is read from a path outside the repo, and `categories.example.json` contains only
synthetic names. Real task names are real-run data and belong in the private machine config.

The same rule covers identifiers, not just data. The name of a private repository, or a
conventional path under the operator's home that would reveal one, must not appear in this
directory either: that is a cross-repo link, and the PII gate blocks a commit carrying one. Note
that the verdict is not fixed by the text itself. The same line can be clean for months and become
a leak the day the private thing it names starts existing, so the question is never whether a
string looks sensitive, but whether it currently points at something real and private.

## Changing it

Read `../../docs/changing-this.md` before editing anything here. It is the invariants a change
must not break and the traps this repo has already fallen into, with the symptom each presents as.
There is no list of routes or modules in it on purpose: a list drifts, and a drifted list reads
exactly like an accurate one.

## Declaration producer (schemaVersion 1)

`task_console.compiler.plan(request)` is a pure function over JSON data. It accepts
`schemaVersion`, `components` (a list of manifests or private installation wrappers),
`bindings`, `machine`, and `baseline`.
The CLI accepts that envelope on stdin or through `plan --request FILE`. Alternatively,
pass all four of `--component FILE` (repeatable), `--bindings FILE`, `--machine FILE`, and
`--baseline FILE`. There is no implicit discovery, configuration path or output directory.

The generated examples in `examples/console` form a complete synthetic request. Their
generator is `tools/make_fixtures.py`; `--out DIRECTORY` writes the example files
directly to that directory. The data-boundary manifest registers each example as FIXTURE.
`installations.request.example.json` is a separate complete request for two installations
of the same public component in different scopes.

| Input | Required fields and meaning |
| --- | --- |
| Component manifest | `schemaVersion: 1`, stable `component`, logical read entrypoint `read`, and `tasks[]`. Each task declares local `id`, `kind` (`oneshot`, `daemon`, `dispatcher`), logical `entrypoint`, recommended `timeout_seconds`, `concurrency_key`, and `checks[]` containing unique `id` and boolean `required`. Optional `schedule_hint` is advice only. |
| Bindings | `schemaVersion: 1`, `tasks` keyed by `component/id`. Each binding explicitly supplies `name`, `enabled`, absolute-executable `argv[]`, absolute `cwd` and `source_root`, `timezone`, `trigger`, opaque nonempty `principal` and `power` objects, effective `timeout_seconds` (0 preserves no limit), `concurrency` with Scheduler `policy`, `backup`, category ID `category`, and `checks[]`. |
| Bound check | `id` plus exactly one of `legacy` (the original health-row fields, excluding identity fields) or `watched_elsewhere` (`component`, `check_id`, `observation_ref`). An omitted check binding remains visible as `unbound`. External observation references are reported, never fetched or treated as proof of health. |
| Machine | `schemaVersion: 1`, nonnegative `authority_epoch`, empty `migrated_tasks`, `overrides` keyed by `component/id`, and `categories[]` (`id`, `name`, optional `desc`). Overrides replace individual binding fields; nested objects/lists are replaced whole. Nonempty migrated sets are rejected in this producer phase. |
| Baseline | Optional `tasks` Scheduler snapshot array, `task_names` containing the literal PowerShell source, parsed `task_health`, and parsed `categories`. Missing/null sources remain NOT CHECKED. This must be an explicit snapshot; the compiler never reads the live Scheduler. |

For installed components, the caller supplies a private wrapper in `components`:

```json
{"namespace": "acme-user", "manifest": {"schemaVersion": 1, "component": "acme-maintenance", "read": "acme-status", "tasks": []}}
```

The manifest above illustrates the wrapper; use actual task declarations as in the
generated request. The public manifest is embedded unchanged. The same request supplies
this mapping alongside `bindings.tasks`:

```json
{"installations": {"acme-user": {"component": "acme-maintenance", "identity": {
  "marketplace": "acme-market", "scope": "user", "client": "acme-client",
  "source_id": "acme-source-user", "metadata": {"selected_version": "1.0"}
}}}}
```

`namespace` uses the same slug syntax as component IDs. Each namespace selects exactly
one wrapper and one mapping; their public `component` IDs must agree. The caller obtains
the identity descriptor from its catalog. `marketplace`, `scope`, `client`, and `source_id`
must be nonempty strings; other JSON metadata is preserved as structured data. No plugin
discovery, cache selection, identity inference or second plugin parser runs here.
The identity tuple excludes revision metadata: two mappings of the same identity fail
even if their selected versions differ. Missing, duplicate, unused or mismatched mappings,
or mixing wrapped and unwrapped declarations of the same component, fail closed.

Wrapped task IDs are `namespace/component/id`; use that exact key for `bindings.tasks`
and `machine.overrides`. There is no fallback to an unqualified binding. All check coverage,
projection ownership and Scheduler proposals use the full task ID. `TaskSpec.component`
retains the public ID, with separate `installation_namespace` and structured
`installation_identity` fields. These are null for legacy unwrapped inputs, whose
`component/id` keys remain supported. Identity metadata is not inserted into generated
legacy file content. The request wrapper, mapping and returned plan belong in private
storage; public manifest IDs never need an installation-specific edit.

Compiled `recommended_timeout_seconds` retains the public declaration's `timeout_seconds`.
Compiled `timeout_seconds` is the effective private binding/machine override, including 0
for unlimited. Changing a recommendation does not propose a Scheduler timeout change.

Supported triggers are `interval` (positive `minutes` or `seconds`), `daily` (`at`),
`weekly` (`at`, `days` using `mon` through `sun`), `logon`, or `xml` (`xml`, `owner`, `reason`).
A binding can supply a list of triggers; an empty list explicitly means no triggers.
Additional trigger fields are retained. XML is checked for well-formedness and retained
as supplied; DTD/entity declarations are rejected. Use optional binding `xml_passthrough`
with `xml`, `owner`, and `reason` for complete task settings that cannot be represented
losslessly. Supply the same object in the baseline for comparison. The optional
`credential_ref` is a reference only; callers must keep credentials out of requests.

Baseline Scheduler rows use `name`, `enabled`, `argv`, `cwd`, `timezone`, `trigger`,
`principal`, `power`, `timeout_seconds`, `concurrency`, and optional `xml_passthrough`.
Supply complete normalized snapshots of the current settings, including opaque fields.
`tasks: []` means the caller checked the complete Scheduler scope and observed no tasks.
A declared task absent from any supplied complete array produces `status: different`, an
`absent_tasks` count and a blocked `scheduler-proposal` with `operation: create`,
`before: null`, `after: TaskSpec`, and `reason_code: task_absent_requires_review`.
This is a read-only proposal: the producer always returns `applicable: false` and legacy
authority. Missing/null snapshots remain `not-checked` without create proposals; missing
fields on an existing row remain individually unchecked. `compared_tasks` counts existing
tasks with all comparison fields supplied, excluding absent tasks.
Unknown OS task names produce adoption proposals
without compiled bindings or create operations. A proposed false-to-true `enabled` change
is explicitly blocked for review. Disabled tasks remain in projections when their explicit
backup/category/check bindings require it.

The plan reports `baseline_revision` (SHA-256 of canonical baseline JSON), `input_revision`
(the complete canonical request), `task_specs`, `changes`, `parity`, `check_coverage`, and
`adopt_proposals`. Its `generated_files` contain UTF-8 content and SHA-256 digests of those
bytes, with ownership task IDs. These are **declared-task projections**, marked
`replacement_safe: false`; they can omit unmanaged legacy entries and must not replace
live files. The compiler writes no side files itself. Persist returned plans only in
private storage chosen by the caller.

Health parity compares all rows as a multiset and normalizes the two existing exit-code
key spellings through `freshness.declared_ok_codes`. Each generated row keeps its own
`task_id` and `check_id`. Coverage counts matching declarations; `evaluated: 0` explicitly
states that no health checks ran. Categories retain order because the legacy reader uses
first membership. Allowlist parity preserves the legacy empty/absent/malformed return
convention; an unreadable or empty list is never reported as a checked empty set.
The shared literal parser rejects direct assignments and mutations through ordinary or
braced TaskNames references, including scope prefixes, comments, backtick line
continuations and indexed updates. Unrelated names, comments and quoted text remain
excluded. Here-string closing markers at the start of a line may be followed by expression
code; scanning resumes after the marker. This is a literal-data reader, not execution or
a general PowerShell evaluator.

CLI exit 0 means a valid plan envelope, including drift and NOT CHECKED results. Exit 2
means invalid arguments, unreadable input, invalid JSON or a contract error. Diagnostics
identify fields without echoing input values. The package entrypoint exposes this producer;
the source-tree web server and retirement APIs keep their existing launch/import forms.
Registration, scheduler mutation, observation collection, runtime launchers and authority
cutover are outside this interface.

T12 adds a separate, explicitly injected registration transaction API and the
apply/retire/recover CLI entrypoints. It does not change the read-only plan above.
See [the registration contract](../../docs/task-registration.md) for required
private inputs, transport guarantees, recovery semantics and integration gaps.
No production runtime adapters or automatic authority cutover are installed.
