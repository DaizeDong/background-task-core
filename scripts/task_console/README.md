# task-console

A local, operable maintenance console for one machine. Scheduled tasks, artifact freshness, git
repositories, skills, the memory pool, plugins, and disk, on one screen, with a button on every
row that needs one.

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

## Configure it

Copy `categories.example.json` to `~/.task-console/categories.json` and replace the synthetic
names with your own tasks. Everything else is optional.

| Variable | What it points at | If unset |
|---|---|---|
| `TASK_CONSOLE_CATEGORIES` | your category map | `~/.task-console/categories.json`; if that is missing, every task lands in one group called uncategorized, and the page says so |
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
