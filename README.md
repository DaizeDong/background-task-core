# task-console

Manage local Windows tasks through declarations, adoption, registration, control and observations. Windows Task Scheduler provides triggers. The loopback web console also shows configured repositories, skills, memory and transcripts, distinguishing what was checked from what was found.

It exists to answer one question honestly: **is anything broken that currently looks fine?** Every
panel reports whether it was able to check, separately from what it found, because an unchecked
source that renders like a passing one is the failure this tool is built to refuse.

## What it does

`scripts/task_console/server.py` serves a single page on `127.0.0.1`, mints a token per start and
never writes it down. `console_ingest.py` pays the cost of reading the Windows Operational event log
out of band, so page loads do not; that log is a circular buffer with retention determined by its configured size and event volume, so anything
not ingested inside that window is gone rather than late, and the console says so rather than
showing a confident number that stopped moving.

The page can act, and every verb it has is listed in one closed table in `maint.py`. **That table
is the authority; this file does not keep a second copy of it**, because a prose list of
capabilities drifts the moment a verb is added and the drift is silent — the reader trusts the
list, and the list is where the promise lives.

Task creation and migration use the declaration, review and registration APIs described in [task registration](docs/task-registration.md). The web action table does not provide an unrestricted Scheduler shortcut. Registration records authority and recovery evidence before changing task definitions; the console keeps its existing review step for outgoing actions.

The review overview groups outstanding checks by affected object and links to task or repository details. The pipeline view separates process steps from execution evidence: a zero-difference sync receipt does not prove capability parity, and a fresh backup artifact does not prove every step ran. Both views reuse the configured component snapshot; they introduce no scheduler or state store. Source search, type filters and expandable evidence keep large catalogs usable. Historical statistics and route settings remain available on their respective pages.

## Running it

```
python scripts/task_console/server.py --port 8787
```

Every path it reads comes from a `TASK_CONSOLE_*` environment variable. The tool ships **no defaults
outside its own namespace**: a source you did not configure reads NOT CHECKED, and the self-check
counts it in its own denominator, so a check that skipped a source cannot print full marks.

`scripts/task_console/README.md` lists every variable, what it points at, and what happens when it
is unset. That table is reconciled against the code by a test in both directions, because the
launcher that sets those variables lives outside this repo and a variable missing from the table is
a variable missing from every launcher anyone writes from it.

## Read-only declaration producer

The `task_console.compiler.plan(request)` API and `task-console plan` JSON CLI compile
component `.console.json` declarations, explicit private bindings, machine overrides and
a caller-supplied legacy snapshot. They return field comparisons and generated content
without writing files, querying Scheduler or starting tasks. Existing task definitions remain authoritative until the reviewed adoption or migration transaction transfers the selected responsibility; planning alone does not transfer authority.

After installing the package into an isolated runtime, run the synthetic example:

```text
python -m task_console plan --component examples/console/.console.json --bindings examples/console/bindings.example.json --machine examples/console/machine.console.example.json --baseline examples/console/baseline.example.json
```

From a source checkout, the same entrypoint is `python -m scripts.task_console`.
The existing `python scripts/task_console/server.py` entrypoint is unchanged.
See `scripts/task_console/README.md` for the producer input contract and parity limits.
Real requests and returned plans contain machine data and belong in private storage.
Regenerate the public examples with `python tools/make_fixtures.py`.

## Where the data lives

Nothing real is stored in this repo. The console's database and its durable export live in a private
companion repository resolved at runtime by the shared resolver in `guards/tools/datadir.py`. If no
companion resolves, the console reports UNINITIALISED with instructions; it never falls back to
writing inside this repo, because an in-repo fallback is not a convenience, it is the leak.

## Tests

```
python -m pytest tests/ -q
```

They target Windows, because the thing under test is Windows. Gates in this repo are expected to be
poisoned before they are trusted: a check that has never been shown to fail is not evidence.

## Changing it

`docs/changing-this.md` is the one to read first. It carries no list of routes, modules or
capabilities, deliberately: a list like that drifts, and once drifted it is indistinguishable from
an accurate one. What it carries instead is the set of invariants a change must not break, and the
traps this repo has already fallen into, with the symptom each one presents as. Both survive the
next panel being added; a list does not.

## Licence

MIT. See `LICENSE`.
