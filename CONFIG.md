# Task Console configuration

Task Console is software with native environment settings. The auxiliary
[automation-management skill](skills/automation-management/SKILL.md) describes safe use of its
declaration and registration interfaces. [config.contract.json](config.contract.json) records
settings applicability; an unconfigured server can render unavailable sources but is not ready
to report usable history.

## Storage selection

`TASK_CONSOLE_DB` selects the exact database leaf. It is used as given, except that a path inside
this payload's own source tree is refused. Point it into the PRIVATE companion; keeping DATA out of
public repositories is enforced at commit and push time by the fleet-guards hooks, not at runtime.

Without that override, the companion root is selected in this order:

1. `TASK_CONSOLE_CONFIG`.
2. `TASK_CONSOLE_DATA_DIR`; a final `data` component denotes the child of the companion root.
3. The fleet_guards sibling `task-console-config` convention.

Use these direct companion locations for setup and recovery.

Explicit roots remain selected even when absent. The database always occupies the canonical
`<root>/data/task-console/console.sqlite3`; this avoids changing layout based on whether `data/`
already exists. Overrides do not copy, initialize or migrate databases. Source checkouts and
installed wheels both use the installed `fleet_guards.runtime` package API.
Missing dependencies report unavailable and block writers. There is no ancestor or vendored
resolver search. Resolution makes no Git or network calls: it is a path decision plus the
own-source-tree refusal.

## Native settings and resources

The complete environment schema and each optional panel's absent-source behavior live in the
[environment table](scripts/task_console/README.md#configure-it). Core server requirements are
Windows, a supported Python interpreter and the imports declared by `pyproject.toml`. Usable
history also requires the resolved database with the expected schema and separately operated
ingestion. The doctor tests those local prerequisites without launching the server or ingester.

Work records additionally need both `TASK_CONSOLE_REMINDER_CLI` and `TASK_CONSOLE_REMINDER_DB`;
agent actions need `TASK_CONSOLE_ACTION_WORKSPACE`. Task control uses the reviewed Controller
configuration, declaration root, state root and vault. Each capability reports its own unavailable
state; the basic doctor's success is not authorization or proof for these optional actions.

`TASK_CONSOLE_CATEGORIES` selects an explicit category file; otherwise the console uses
`categories.json` in its resolved PRIVATE companion. Missing selection or files are reported as
unconfigured. Category writes refuse a path inside this source tree; no Git check runs.

The llmcall ledger and chain are separate producer resources. Their explicit console overrides
and producer settings are listed in the environment table. There is no native home-directory
fallback for these resources; missing selection is reported instead of creating a new tree.
Chain writes refuse a path inside this source tree; no Git check runs. Switching the console
companion does not relocate llmcall files, and selected executables can still use PATH.

## Initialize and switch

Create or clone the PRIVATE companion. Companion discovery requires the `fleet-guards>=0.2.1`
runtime API, installed with `pip install -e "$(git config --get fleet.guards)"`. A missing
runtime API fails visibly; installed packages do not search checkout ancestors.
From this source root:

```powershell
$env:TASK_CONSOLE_CONFIG = '<private-companion>'
python tools/init_config.py --out $env:TASK_CONSOLE_CONFIG
. (Join-Path $env:TASK_CONSOLE_CONFIG 'settings.ps1')
python tools/verify_config.py --json
```

The initializer writes a native `settings.ps1` template that derives the selected root and canonical
database path from its own directory when dot-sourced, and enables read-only mode. The template
bytes are independent of the companion location. Existing settings are preserved byte for byte. It creates no
database, task, registration, event or server. Review the template and add the optional resource
paths before dot-sourcing it. An empty or missing database keeps the doctor NOT READY.

Switch by selecting and dot-sourcing the other companion's reviewed settings in the process
that will run the console. Recheck the optional explicit overrides too. Doctor output identifies
its scope as `local-server-and-history`; it does not prove recent ingestion, task execution,
work-owner connectivity or external delivery. It uses an immutable read-only database probe and
refuses active WAL/journal state instead of creating SQLite sidecars.

## Recovery and retention

Keep the main database versionable, while taking consistent recovery snapshots through the
documented [storage workflow](docs/storage.md). SQLite owns the WAL, SHM and journal lifecycle;
their exact transient declarations preserve pending transaction recovery obligations. A backup
must use the online API or a cold consistent snapshot. Backfill cannot reconstruct all historical
settings and events. Restore only after preserving current state and stopping the writer.

The [storage contract](storage.contract.json) owns retention and budgets. A failed capacity or
nested-boundary check requires review; it never permits truncating core history to meet a budget.
