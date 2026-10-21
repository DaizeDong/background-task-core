# background-task-core

A loopback-only console for one Windows machine: its scheduled tasks, their run history and health, shared work records, and optional conversation, repository and resource components.

Windows Task Scheduler supplies triggers; background-task-core keeps the task declarations, the registration authority and the guarded controls. Every source reports its coverage: an unavailable source does not render as an empty successful one, and a completion summary does not stand in for execution evidence.

[![Local Console](https://img.shields.io/badge/Local-Console-orange?style=flat)](scripts/background_task_core/server.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-green?style=flat)](scripts/background_task_core/server.py)
[![Languages](https://img.shields.io/badge/Languages-EN%20%2F%20CN-blue?style=flat)](#languages)
[![Roadmap](https://img.shields.io/badge/Roadmap-v0.1.0-purple?style=flat)](ROADMAP.md)

[English](README.md) | [中文版](README_CN.md)

---

## Design

**Check status and findings are reported separately.** Each view distinguishes unchecked, measured zero and unavailable states. An unconfigured source renders NOT CHECKED, so skipped coverage never reads as a pass.

**Each control has one authority.** Task controls, deletion and registration all go through the registration authority in `lifecycle.py` and `registry.py`. The page has no Scheduler shortcut.

## Layout

The task core in `scripts/background_task_core/` keeps these responsibilities:

| Module | Owns |
| --- | --- |
| `registry.py` | declaration contracts, the backup allowlist parser, component compilation, the plan compiler, registration transactions, the creation check |
| `scheduler.py` | task-name rules, the Task Scheduler adapter, task XML rendering and normalisation, the COM and DPAPI transports |
| `store.py` | authority storage: file resources, journals, the protected vault, authority history, the runtime |
| `lifecycle.py` | control actions, adoption, reconciliation, export and restore, task deletion, repair orders, the passive observation pass |
| `runs.py` | exit-code normalisation, health verdicts, freshness, the health log, the event log reader, the console database and its ingester, the component status reader |
| `integrations.py` | the read boundary, the shared work service (schedule-reminder) and its actions, linked work items, the adapter registry |
| `server.py` + `console.html` + `static/` | the HTTP server and the page |
| `__main__.py` | the command line |

Optional components have separate providers (`convos`, `llmstats`, `repos`, `maint`, `memops`, `codexinfo`, `sysinfo`, `selfcheck`) and panel scripts under `static/panels/`. `integrations.ADAPTERS` connects each reader to its endpoint and view. `component_routes.py` owns parameterized reads and component actions, including the `convo-chain` library. Providers load on request; missing component data does not prevent task-core startup.

## What the page shows

The page groups work records and model calls, automation and run history, conversations, repositories, skills/plugins/memory, storage, integrations and diagnostics. Views load their data when opened, use compact icon controls and share page refresh and operation feedback.

Conversations support search, continuous paging, rename, physical move, confirmed deletion and branch inspection. Markdown export includes a selected message range, with optional tool calls and thinking; page JSON export remains a separate snapshot of loaded data. Task rows retain enable, disable, run, stop, repair and deletion through the current authority. See [task control](docs/task-control.md) and [the work platform](docs/work-platform.md).

## Install

Windows, Python 3.11 or newer.

```bash
git clone https://github.com/DaizeDong/background-task-core
cd background-task-core
pip install -e . -e "$(git config --get fleet.guards)"
git config core.hooksPath .githooks
```

Dependencies (see `pyproject.toml`): `fleet-guards`, `llmcall`, `convo-chain`, `pywin32`.

## Run

```bash
python -m background_task_core.server --port 8787            # the page, bound to 127.0.0.1
python -m background_task_core ingest [--skip-runlog]       # fill the console database
python -m background_task_core status --configured          # component status reader
python -m background_task_core observations --periodic ...  # one passive health collection pass
python -m background_task_core backup database              # complete private SQLite snapshot
python -m background_task_core backup registration          # private registration recovery evidence
python -m background_task_core install-skill --skill-root ... # create/update/retire the tasks a skill declares
python -m background_task_core <authority command> ...      # plan, registration-plan, creation-check, apply, retire, control, ...
```

The server generates a token at each start and never writes it to disk. Ingestion runs out of band, so page loads never read the Windows event log.

### Windows entrypoints

`windows/` holds the PowerShell side. Every script finds its machine values through the private companion named by `BACKGROUND_TASK_CORE_CONFIG` (its `data/launcher-binding.json`) and refuses when that variable is unset.

| Script | Does |
| --- | --- |
| `background-task-core.ps1` | Starts the installed console as a hidden process and opens it (`-Tab`, `-NoBrowser`, `-Stop`, `-Status`) |
| `install-background-task-core-shortcut.ps1` | Desktop and Start-menu shortcuts to that launcher, with the companion's `data/assets/icon.ico` (`-Remove` deletes them) |
| `task-health-monitor.ps1` | The hourly health pass run by `TaskHealthMonitor` |
| `console-runlog-ingest.ps1` | The daily full ingest run by `RefreshConsoleRunlog` |
| `task-registry-check.ps1` | Task-creation conformance: every active task is in the backup allow-list projection and the health watch list; the monitor runs it |
| `background-task-core-binding.ps1`, `lib/task-health-observations.ps1` | Shared helpers the others dot-source |

This repository's own two tasks are declared in [automation/tasks.json](automation/tasks.json) and installed with `install-skill` like any skill's.

## Tasks that ship with a skill

A skill declares its scheduled tasks in `automation/tasks.json` (public, variables only) and keeps machine choices in its private companion's `automation/tasks.local.json`. `install-skill` turns both into authority declarations and creates, updates or retires exactly that skill's tasks; re-running it changes nothing. Contract and example: [docs/skill-tasks.md](docs/skill-tasks.md).

## Configuration

All settings are environment variables; the owner launcher sets them from the private companion's `data/launcher-binding.json` ([synthetic example](launcher-binding.json.example)). [CONFIG.md](CONFIG.md) lists every variable and what happens when it is unset.

## Read-only declaration producer

`background_task_core.registry.plan(request)` and `python -m background_task_core plan` compile component declarations in the `.console.json` shape (the format of the authority's desired file), private bindings, machine overrides and a legacy snapshot into field comparisons and generated content, without writing files or touching the Scheduler.

```text
python -m background_task_core plan --component examples/console/.console.json --bindings examples/console/bindings.example.json --machine examples/console/machine.console.example.json --baseline examples/console/baseline.example.json
```

Real requests and returned plans contain machine data and belong in private storage. Regenerate the public examples with `python tools/make_fixtures.py`.

## Where the data lives

The database and the run-event export live in a private companion resolved by `fleet_guards.runtime`. The database (`data/background-task-core/console.sqlite3`), the DPAPI vault and other files every run rewrites are local state there and are not versioned; the versioned history is the monthly text export `data/background-task-core/run-events/YYYY-MM.jsonl`. On-demand [recovery backups](docs/recovery-backups.md) go into the companion's versioned `data/recovery/`. If no companion resolves, the console reports UNINITIALISED; writes never fall back to this repository.

## Limitations

The console reports only the Windows machine it runs on and sends no alerts. Run history comes from a circular event log; events that rotate out before ingestion are lost. The monthly export omits raw return codes, task settings history and ingest attempts, so it is not a complete database restore.

## Languages

English (`README.md`) · 中文 (`README_CN.md`)

## Roadmap · Contributing · License

See [ROADMAP.md](ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md) · [LICENSE](LICENSE) (MIT). Before changing anything, read [docs/changing-this.md](docs/changing-this.md). Deviations from the house repository spec are recorded in [docs/2026-09-22-spec-adaptation.md](docs/2026-09-22-spec-adaptation.md).
