# Background Task Core configuration

Background Task Core is configured only through environment variables. The owner launcher sets them from the private companion's `data/launcher-binding.json` ([synthetic example](launcher-binding.json.example)): its `runtime` map holds the registration roots and its `environment` map holds the `BACKGROUND_TASK_CORE_*` values. The auxiliary [automation-management skill](skills/automation-management/SKILL.md) describes safe use of the declaration and registration interfaces.

## Storage selection

`BACKGROUND_TASK_CORE_DB` selects the exact database file. It is used as given, except that a path inside this source tree is refused. Without it, the `fleet_guards` companion resolver selects the companion root (first existing of `BACKGROUND_TASK_CORE_DATA_DIR`, `BACKGROUND_TASK_CORE_CONFIG`, the sibling `background-task-core-config`) and the database is `<root>/data/background-task-core/console.sqlite3`. Nothing is copied, initialized or migrated by an override, and resolution makes no Git or network calls.

The ingester appends the normalized run-event export to `run-events/YYYY-MM.jsonl` beside the database, one file per month of the event time. The database itself is local state and is not versioned; the monthly files are.

## Variables

| Variable | Meaning | When unset |
| --- | --- | --- |
| `BACKGROUND_TASK_CORE_DB` | the SQLite file the ingester writes and the page reads | the companion's `data/background-task-core/console.sqlite3`; no companion reports UNINITIALISED |
| `BACKGROUND_TASK_CORE_DATA_DIR`, `BACKGROUND_TASK_CORE_CONFIG` | companion root (or its `data` child) for the resolver | the sibling `background-task-core-config` convention |
| `BACKGROUND_TASK_CORE_RUNTIME_CONFIG`, `BACKGROUND_TASK_CORE_PRIVATE_ROOT`, `BACKGROUND_TASK_CORE_STATE_ROOT`, `BACKGROUND_TASK_CORE_VAULT_ROOT` | the reviewed Controller configuration, declaration root, journal/receipt root and encrypted snapshot root | task controls, registration and deletion of managed tasks are unavailable |
| `BACKGROUND_TASK_CORE_CATEGORIES` | the private category map | `categories.json` in the resolved companion; missing files are reported as unconfigured |
| `BACKGROUND_TASK_CORE_HEALTH` | the health watch list (`task-health.json` shape) | the health-coverage column reads NOT CHECKED |
| `BACKGROUND_TASK_CORE_ALLOWLIST` | a PowerShell file with a `$TaskNames = @(...)` backup allow-list | the backup-coverage check reads NOT CHECKED |
| `BACKGROUND_TASK_CORE_HISTORY` | the health monitor's poll-observation log | observation columns read NOT CHECKED; `ingest` reports the health half as skipped |
| `BACKGROUND_TASK_CORE_STATUS_SNAPSHOT` | captured component observations | component health is unchecked |
| `BACKGROUND_TASK_CORE_CATALOG_SNAPSHOT` | captured component catalog | catalog inventory is unchecked |
| `BACKGROUND_TASK_CORE_REMINDER_CLI`, `BACKGROUND_TASK_CORE_REMINDER_DB` | the reminder owner's `reminder.py` and its existing database | work records report unconnected |
| `BACKGROUND_TASK_CORE_ACTION_WORKSPACE` | private directory for agent workspaces | automatic todo actions are unavailable |
| `BACKGROUND_TASK_CORE_AGENT_TASK_ID` | registered `owner/task` id of the agent tick; an accepted agent action asks the Scheduler to run it at once | the agent offer is disabled |
| `BACKGROUND_TASK_CORE_DELETE_FOLLOWUP` | the private follow-up hook a task delete runs after the Scheduler step ([protocol](docs/task-control.md)) | deletion still happens, the result is `partial` and says what was not cleaned |
| `BACKGROUND_TASK_CORE_DELETED_ARCHIVE` | private directory that keeps an unmanaged task's XML and receipt before deletion | deleting an unmanaged task is blocked in its preview |
| `BACKGROUND_TASK_CORE_DELETE_WITHOUT_AUTHORITY` | `1` lets a process without Controller settings delete tasks as unmanaged | such a process refuses every delete |
| `BACKGROUND_TASK_CORE_READ_ONLY` | `1` disables every write route | guarded writes are available |

Each optional capability reports its own unavailable state. Category writes refuse a path inside this source tree.

## Initialize and switch

Create or clone the private companion and install `fleet-guards>=0.2.1` (`pip install -e "$(git config --get fleet.guards)"`). Switch by pointing the launcher at the other companion's binding. Run `python -m background_task_core runtime-status` with the four runtime arguments to check the authority, and `python -m background_task_core ingest` to create or update the database.

## Recovery

The registration journals, receipts, pointer and vault under the companion's `data/background-task-core/registration/` and `vault/` are protected together. The database can be rebuilt from the retained health log and the current event-log window with `python -m background_task_core ingest --backfill`; events older than that window survive only in the monthly export, which omits raw return codes, task settings history and ingest attempts.
