# task-console

A loopback-only console for one Windows machine: its scheduled tasks, the git repositories under one root, and whatever skill, memory and transcript directories you point it at.

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

`scripts/task_console/server.py` serves a single page on `127.0.0.1`, mints a token per start and never writes it down. `console_ingest.py` pays the cost of reading the Windows Operational event log out of band, so page loads do not; that log is a circular buffer of roughly five days, so anything not ingested inside that window is gone rather than late, and the console says so rather than showing a confident number that stopped moving.

It is **not** a Claude Code skill or plugin, and it ships no `SKILL.md`: it is a server you run. It is **not** a monitoring service, since nothing polls and nothing alerts. It is **not** portable: it reads the Windows Task Scheduler through PowerShell and `pywin32`, and `server.py` refuses to start anywhere else on purpose.

The page can act, and the shape of the constraint does not drift: it cannot **create** a task, because creating one correctly means registering it in several places and a button that skipped them would manufacture exactly the untracked task that procedure exists to prevent. It can retire one, which is the opposite operation and safe to automate precisely because it is subtractive. Anything that leaves the machine is two steps, never one.

## Install

Windows, Python 3.12 or newer.

```bash
git clone --recursive https://github.com/DaizeDong/task-console
cd task-console
pip install pytest pywin32
git config core.hooksPath .githooks    # arms the guards; local config, so it cannot be committed
```

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

## Where the data lives

Nothing real is stored in this repository. The console's database and its durable export live in a private companion repository resolved at runtime by the shared resolver in `guards/tools/datadir.py`. If no companion resolves, the console reports UNINITIALISED with instructions; it never falls back to writing inside this repository, because an in-repo fallback is not a convenience, it is the leak.

## Tests

```bash
python -m pytest tests/ -q
```

They target Windows, because the thing under test is Windows. CI runs the same suite on `windows-latest` and fails if the collected count drops below a floor observed on a green run.

## Limitations

One machine, the one it runs on. Windows only. Nothing polls and nothing alerts, so the console reports what is true when you open it. The event log it derives run history from holds roughly five days, so history outside that window exists only in the durable export, and only for the period since ingestion started.

## Languages

English (`README.md`) · 中文 (`README_CN.md`)

## Roadmap · Contributing · License

See [ROADMAP.md](ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md) · [LICENSE](LICENSE) (MIT).

The deviations from the house repository spec, and the reasons for each, are recorded in [docs/2026-09-22-spec-adaptation.md](docs/2026-09-22-spec-adaptation.md).
