# task-console

A loopback-only console for one Windows machine: its scheduled tasks, the git repositories under one root, and whatever skill, memory and transcript directories you point it at.

It shows agent work, recorded results, tracked commitments and local automation in one place. Work records come from the existing reminder owner; Windows Task Scheduler supplies triggers and task-console retains declarations, registration and guarded controls.

The workbench separates human decisions from technical diagnostics. Every source reports its coverage: an unavailable source does not render as an empty successful one, and a completion summary does not stand in for execution evidence.

[![Local Console](https://img.shields.io/badge/Local-Console-orange?style=flat)](scripts/task_console/server.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-green?style=flat)](scripts/task_console/server.py)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](#languages)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.0-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

---

## ⭐ Read this first, the design philosophy

It exists to answer one question honestly: **is anything broken that currently looks fine?** Three commitments follow from that, and they matter more than any panel does.

**Unchecked and zero are different outputs.** Every panel reports whether it was able to check, separately from what it found. A source nobody configured renders NOT CHECKED, never an empty green table, and the self check counts it in its own denominator, so a run that skipped a source cannot print full marks.

**No list is kept twice.** The verbs the page can perform live in one closed table in `maint.py`, and that table is the authority. Every environment variable it reads is listed in `scripts/task_console/README.md`, and a test reconciles that table against the code in both directions, because the launcher that sets those variables lives outside this repository. Where a second copy cannot be deleted, a gate holds the two together; where it can, it is deleted.

**A gate is trusted only after it has been shown to fail.** Poisoning proves the check can go red, and a negative control proves it is not red about everything. The tests here carry the record of the poisonings that did not go red the first time, and those notes are the valuable part.

## What it is (and isn't)

`scripts/task_console/server.py` serves a single page on `127.0.0.1`, mints a token per start and never writes it down. `console_ingest.py` pays the cost of reading the Windows Operational event log out of band, so page loads do not; that log is a circular buffer with retention determined by its configured size and event volume, so anything not ingested inside that window is gone rather than late, and the console says so rather than showing a confident number that stopped moving.

It is **not** a Claude Code skill or plugin, and it ships no `SKILL.md`: it is a server you run. It is **not** a monitoring service, since nothing polls and nothing alerts. It is **not** portable: it reads the Windows Task Scheduler through PowerShell and `pywin32`, and `server.py` refuses to start anywhere else on purpose.

## What the page can do

The page's maintenance verbs are defined in `maint.py`. Conversation operations have their own authenticated routes and use [convo-chain](https://github.com/DaizeDong/convo-chain) for transcript rules and file transactions. The console owns the routes, request checks and panel; the library owns parsing, export, fork, rename and migration. See [the console README](scripts/task_console/README.md) for the supported controls and limits.

Task creation and migration use the declaration, review and registration APIs described in [task registration](docs/task-registration.md). The web action table does not provide an unrestricted Scheduler shortcut. Registration records authority and recovery evidence before changing task definitions; the console keeps its existing review step for outgoing actions.

Navigation groups information into work, automation and resources. Work, result and activity projections reuse the same owner records. Pipelines and execution details sit under automation; conversations and model calls support work records. Technical exceptions remain in diagnostics and do not become human approvals. See [the work platform design](docs/work-platform.md) for data ownership and integration limits.

Pipeline evidence remains scoped: a zero-difference sync receipt does not prove capability parity, and a fresh backup artifact does not prove every step ran. The UI adds no scheduler, state store, notification transport or agent runner. Existing task-control authority and outgoing-action reviews remain in force.

Anything that leaves the machine is two steps, never one.

## Install

Windows, Python 3.12 or newer.

```bash
git clone --recursive https://github.com/DaizeDong/task-console
cd task-console
pip install pytest pywin32 PyYAML
git config core.hooksPath .githooks    # arms the guards; local config, so it cannot be committed
```

The console also imports three pinned libraries at module top: `convo-chain`, `fleet-guards` and `llmcall` (see `pyproject.toml`). `llmcall` is a private repository, so a public clone cannot install it; `.github/workflows/tests.yml` is the one place that spells out the exact revisions CI installs and how it reads the private one.

`--recursive` matters. The gates live in submodules, and a plain clone leaves those directories present and empty, which is an unguarded repository rather than a clean one. If you already cloned without it, run `git submodule update --init --recursive`.

## Quick start

```bash
python scripts/task_console/server.py --port 8787
```

Every path it reads comes from a `TASK_CONSOLE_*` environment variable. The tool ships **no defaults outside its own namespace**, so an unconfigured console starts, serves, and tells you it checked nothing.

## Where the details live

Each list has exactly one home, and it is not this file. Follow a pointer when the question it answers is the one you have.

| Read this | When you are asking |
| --- | --- |
| [scripts/task_console/README.md](scripts/task_console/README.md) | What is on the page, and every environment variable it reads |
| `scripts/task_console/maint.py` | Which verbs the page can perform against the machine |
| `scripts/task_console/server.py` | Which routes exist, and the three controls that make the write endpoints safe |
| [docs/changing-this.md](docs/changing-this.md) | What a change must not break, and the traps this repository has already fallen into |
| [docs/cleanup-plan.md](docs/cleanup-plan.md) | What one full audit found, and how much of it was carried out |

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

Nothing real is stored in this repository. The console's database and its durable export live in a private companion repository resolved at runtime by the shared resolver in `guards/tools/datadir.py`. If no companion resolves, the console reports UNINITIALISED with instructions; it never falls back to writing inside this repository, because an in-repo fallback is not a convenience, it is the leak.

## Tests

```bash
python -m pytest tests/ -q
```

They target Windows, because the thing under test is Windows. CI runs the same suite on `windows-latest` and fails if the collected count drops below a floor observed on a green run.

## Limitations

One machine, the one it runs on. Windows only. Nothing polls and nothing alerts, so the console reports what is true when you open it. The event log it derives run history from is a circular buffer whose retention depends on its configured size and event volume, so history outside that window exists only in the durable export, and only for the period since ingestion started.

## Languages

English (`README.md`) · 中文 (`README_CN.md`)

## Roadmap · Contributing · License

See [ROADMAP.md](ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md) · [LICENSE](LICENSE) (MIT).

The deviations from the house repository spec, and the reasons for each, are recorded in [docs/2026-09-22-spec-adaptation.md](docs/2026-09-22-spec-adaptation.md).
