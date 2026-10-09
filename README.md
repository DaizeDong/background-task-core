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

The console identifies missing checks, unhealthy state and gaps in execution evidence.

**Check status and findings are reported separately.** Each panel distinguishes unchecked, measured zero and unavailable/error states. An unconfigured source renders NOT CHECKED and remains in the self-check denominator, so skipped coverage cannot receive a complete pass.

**Each control has an explicit authority.** The closed table in `maint.py` defines page maintenance verbs. `scripts/task_console/README.md` lists environment variables, and tests reconcile that contract with code in both directions because external launchers depend on it. Representations that must be duplicated require consistency checks.

**Checks require failure cases and negative controls.** Fault injection establishes that a check rejects invalid state; negative controls establish that valid state passes. Tests retain previously missed failure conditions to prevent regressions.

## What it is (and isn't)

`scripts/task_console/server.py` serves a single page on `127.0.0.1`, generates a token per start and never persists it. `console_ingest.py` reads the Windows Operational event log separately, so page loads do not perform ingestion.

The primary product is a local server with an auxiliary [automation-management skill](skills/automation-management/SKILL.md) for declaration, review and registration. It reads Windows Task Scheduler through PowerShell and `pywin32` and refuses to start on other platforms. Collection freshness and history coverage are described under Limitations.

## What the page can do

The page's maintenance verbs are defined in `maint.py`. Conversation operations have their own authenticated routes and use [convo-chain](https://github.com/DaizeDong/convo-chain) for transcript rules and file transactions. The console owns the routes, request checks and panel; the library owns parsing, export, fork, rename, migration and confirmed deletion. A suggested Chinese name for a session comes from `llmcall` and is only filled into the rename box; saving it is still a normal rename. See [the console README](scripts/task_console/README.md) for the supported controls and limits.

Task creation and migration use the declaration, review and registration APIs described in [task registration](docs/task-registration.md). The web action table does not provide an unrestricted Scheduler shortcut. Registration records authority and recovery evidence before changing task definitions; the console keeps its existing review step for outgoing actions.

Navigation groups information into work, automation and resources. Work, result and activity projections reuse the same owner records. Pipelines and execution details sit under automation; conversations and model calls support work records. Technical exceptions remain in diagnostics and do not become human approvals. See [the work platform design](docs/work-platform.md) for data ownership and integration limits.

Pipeline evidence remains scoped: a zero-difference sync receipt does not prove capability parity, and a fresh backup artifact does not prove every step ran. The UI adds no scheduler, state store, notification transport or agent runner. Existing task-control authority and outgoing-action reviews remain in force.

Outgoing actions retain a two-step review flow.

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

Selected sources come from environment settings; their defaults and unavailable states are listed in the [environment table](scripts/task_console/README.md). llmcall resources and executable discovery retain their own documented defaults. An unconfigured source remains visibly unchecked.

## Config

Set `TASK_CONSOLE_CONFIG` to the PRIVATE companion root. To switch configurations, select and dot-source the other companion's reviewed settings and recheck explicit resource overrides. [CONFIG.md](CONFIG.md) defines discovery, native environment settings, initialization and read-only diagnosis. `tools/init_config.py` writes a reviewed `settings.ps1` in an initialized PRIVATE companion; `tools/verify_config.py --json` checks local server/history prerequisites. It does not run ingestion or prove optional actions. Database overrides still require source-owned PRIVATE versioned admission.

## Where the details live

These references define runtime interfaces, configuration and maintenance constraints.

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

The database and durable export live in a private companion resolved by `guards/tools/datadir.py`. If no companion resolves, the console reports UNINITIALISED with setup instructions. Writes never fall back to this tool repository.

<a id="storage-retention"></a>

[Storage contract and cleanup](docs/storage.md) distinguish protected runtime and recovery state from completed development artifacts. `storage.contract.json` sets review budgets; cleanup never truncates core history to meet them.

## Tests

```bash
python -m pytest tests/ -q
```

The tests target Windows. CI runs the same suite on `windows-latest` and fails if the collected count falls below the floor observed in a passing run.

## Limitations

The console reports only the Windows machine on which it runs. It does not poll or send alerts. Run history comes from a circular event log whose retention depends on configured size and event volume. Events missed within that window cannot be backfilled later; older history is limited to records retained in the durable export since ingestion began. Reported source and ingestion freshness do not establish complete history.

## Languages

English (`README.md`) · 中文 (`README_CN.md`)

## Roadmap · Contributing · License

See [ROADMAP.md](ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md) · [LICENSE](LICENSE) (MIT).

The deviations from the house repository spec, and the reasons for each, are recorded in [docs/2026-09-22-spec-adaptation.md](docs/2026-09-22-spec-adaptation.md).
