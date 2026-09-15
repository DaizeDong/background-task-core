# task-console

A local, loopback-only console for looking at a Windows machine: its scheduled tasks, the git
repositories under one root, and whatever skill, memory and transcript directories you point it at.

It exists to answer one question honestly: **is anything broken that currently looks fine?** Every
panel reports whether it was able to check, separately from what it found, because an unchecked
source that renders like a passing one is the failure this tool is built to refuse.

## What it does

`scripts/task_console/server.py` serves a single page on `127.0.0.1`, mints a token per start and
never writes it down. `console_ingest.py` pays the cost of reading the Windows Operational event log
out of band, so page loads do not; that log is a circular buffer of roughly five days, so anything
not ingested inside that window is gone rather than late, and the console says so rather than
showing a confident number that stopped moving.

The page can act, and every verb it has is listed in one closed table in `maint.py`. **That table
is the authority; this file does not keep a second copy of it**, because a prose list of
capabilities drifts the moment a verb is added and the drift is silent — the reader trusts the
list, and the list is where the promise lives.

The shape of the constraint, which does not drift: it cannot **create** a task, because creating
one correctly means registering it in several places and a button that skipped them would
manufacture exactly the untracked task that procedure exists to prevent. It can retire one, which
is the opposite operation and safe to automate precisely because it is subtractive. Anything that
leaves the machine is two steps, never one.

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

## Licence

MIT. See `LICENSE`.
