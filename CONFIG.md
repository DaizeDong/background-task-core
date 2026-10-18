# Task Console configuration

Task Console is configured only through environment variables. The owner launcher sets them from the private companion's `data/launcher-binding.json` ([synthetic example](launcher-binding.json.example)): its `runtime` map holds the registration roots and its `environment` map holds the `TASK_CONSOLE_*` values. The auxiliary [automation-management skill](skills/automation-management/SKILL.md) describes safe use of the declaration and registration interfaces.

## Storage selection

`TASK_CONSOLE_DB` selects the exact database file. It is used as given, except that a path inside this source tree is refused. Without it, the `fleet_guards` companion resolver selects the companion root (first existing of `TASK_CONSOLE_DATA_DIR`, `TASK_CONSOLE_CONFIG`, the sibling `task-console-config`) and the database is `<root>/data/task-console/console.sqlite3`. Nothing is copied, initialized or migrated by an override, and resolution makes no Git or network calls.

The ingester appends the normalized run-event export to `run-events/YYYY-MM.jsonl` beside the database, one file per month of the event time. The database itself is local state and is not versioned; the monthly files are.

## Variables

| Variable | Meaning | When unset |
| --- | --- | --- |
| `TASK_CONSOLE_DB` | the SQLite file the ingester writes and the page reads | the companion's `data/task-console/console.sqlite3`; no companion reports UNINITIALISED |
| `TASK_CONSOLE_DATA_DIR`, `TASK_CONSOLE_CONFIG` | companion root (or its `data` child) for the resolver | the sibling `task-console-config` convention |
| `TASK_CONSOLE_RUNTIME_CONFIG`, `TASK_CONSOLE_PRIVATE_ROOT`, `TASK_CONSOLE_STATE_ROOT`, `TASK_CONSOLE_VAULT_ROOT` | the reviewed Controller configuration, declaration root, journal/receipt root and encrypted snapshot root | task controls, registration and deletion of managed tasks are unavailable |
| `TASK_CONSOLE_CATEGORIES` | the private category map | `categories.json` in the resolved companion; missing files are reported as unconfigured |
| `TASK_CONSOLE_HEALTH` | the health watch list (`task-health.json` shape) | the health-coverage column reads NOT CHECKED |
| `TASK_CONSOLE_ALLOWLIST` | a PowerShell file with a `$TaskNames = @(...)` backup allow-list | the backup-coverage check reads NOT CHECKED |
| `TASK_CONSOLE_HISTORY` | the health monitor's poll-observation log | observation columns read NOT CHECKED; `ingest` reports the health half as skipped |
| `TASK_CONSOLE_STATUS_SNAPSHOT` | captured component observations | component health is unchecked |
| `TASK_CONSOLE_CATALOG_SNAPSHOT` | captured component catalog | catalog inventory is unchecked |
| `TASK_CONSOLE_REMINDER_CLI`, `TASK_CONSOLE_REMINDER_DB` | the reminder owner's `reminder.py` and its existing database | work records report unconnected |
| `TASK_CONSOLE_ACTION_WORKSPACE` | private directory for agent workspaces | automatic todo actions are unavailable |
| `TASK_CONSOLE_AGENT_TASK_ID` | registered `owner/task` id of the agent tick; an accepted agent action asks the Scheduler to run it at once | the agent offer is disabled |
| `TASK_CONSOLE_DELETE_FOLLOWUP` | the private follow-up hook a task delete runs after the Scheduler step ([protocol](docs/task-control.md)) | deletion still happens, the result is `partial` and says what was not cleaned |
| `TASK_CONSOLE_DELETED_ARCHIVE` | private directory that keeps an unmanaged task's XML and receipt before deletion | deleting an unmanaged task is blocked in its preview |
| `TASK_CONSOLE_DELETE_WITHOUT_AUTHORITY` | `1` lets a process without Controller settings delete tasks as unmanaged | such a process refuses every delete |
| `TASK_CONSOLE_READ_ONLY` | `1` disables every write route | guarded writes are available |

Each optional capability reports its own unavailable state. Category writes refuse a path inside this source tree.

## Initialize and switch

Create or clone the private companion and install `fleet-guards>=0.2.1` (`pip install -e "$(git config --get fleet.guards)"`). Switch by pointing the launcher at the other companion's binding. Run `python -m task_console runtime-status` with the four runtime arguments to check the authority, and `python -m task_console ingest` to create or update the database.

## Recovery

The registration journals, receipts, pointer and vault under the companion's `data/task-console/registration/` and `vault/` are protected together. The database can be rebuilt from the retained health log and the current event-log window with `python -m task_console ingest --backfill`; events older than that window survive only in the monthly export, which omits raw return codes, task settings history and ingest attempts.
