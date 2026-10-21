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

| `BACKGROUND_TASK_CORE_SKILLS` | a skills directory | unset means the skills panel reads NOT CHECKED |
| `BACKGROUND_TASK_CORE_SKILL_ARCHIVE` | where archived skills go | unset means the archive button is not offered at all |
| `BACKGROUND_TASK_CORE_MEMORY` | a memory directory | unset means the memory panel reads NOT CHECKED |
| `BACKGROUND_TASK_CORE_MEMORY_ARCHIVER` | the existing archiver script | unset means the archive button is not offered |
| `BACKGROUND_TASK_CORE_REPOS` | a directory holding git repos | unset means the repository panel reads NOT CHECKED |
| `BACKGROUND_TASK_CORE_VISIBILITY` | a JSON object keyed by `owner/repo` (lowercase), each value `PUBLIC` or `PRIVATE` | unset means every repo's visibility reads unknown and no badge is drawn, which is deliberately hard to tell apart from a repo the table has no row for, so the panel also reports the table it loaded and how many rows matched |
| `BACKGROUND_TASK_CORE_CODEX` | a second agent CLI's home directory, holding its instruction file, config, session store, logs and cache | no default; unset means that whole half of the maintenance view reads NOT CHECKED, which is deliberately not the same as reading zero bytes |
| `BACKGROUND_TASK_CORE_PLUGIN_CACHE` | a plugin cache directory | unset means the cache row reads NOT CHECKED and nothing can be deleted |
| `BACKGROUND_TASK_CORE_SESSIONS` | a session transcript directory; also the `root` the console passes to the `convo-chain` library for the conversation chain | unset means that row reads NOT CHECKED, the chain reads NOT CHECKED, and export and fork refuse with `unavailable` |
| `BACKGROUND_TASK_CORE_CLAUDE` | the CLI used for plugin actions | falls back to PATH; not found means the plugin panel reads NOT CHECKED |
| `BACKGROUND_TASK_CORE_IDENTITIES` | a table of `login\|display name\|commit email`, one per line, `#` for comments, saying which identity each repository owner should be committed under | unset means the account-match column reads NOT CHECKED for every repository, which is deliberately not the same as saying they match; no address from this file is ever rendered, only the account name and the verdict |
| `BACKGROUND_TASK_CORE_CONVO_CACHE` | `data/user-state/convo-cache.json` in the PRIVATE companion; a path inside this source tree, or an unreadable cache, is reported as `summary.cacheError` | unset means the first scan of every page load walks every transcript again; correctness is unaffected, the page is just slower |
| `BACKGROUND_TASK_CORE_LLMCALL_LEDGER` | the append-only JSONL ledger the LLM-call primitive writes, one line per call | uses `LLMCALL_LEDGER` when set (empty, `0`, `off` or `false` disables it); otherwise `runtime/ledger.jsonl` under the llmcall data directory that the installed llmcall's own `fleet_guards` companion resolver selects. No configured root or missing file is reported as uninitialized or NOT CHECKED, not zero calls |
| `BACKGROUND_TASK_CORE_LLMCALL_CHAIN` | the file the call view writes a fallback-chain order into | uses `chain.txt` under the same producer runtime root as above. Missing configuration blocks a write. `LLMCALL_CHAIN` still overrides the file's order, and the page reports that override |

Each optional capability reports its own unavailable state. Category writes refuse a path inside this source tree.

## Initialize and switch

Create or clone the private companion and install `fleet-guards>=0.2.1` (`pip install -e "$(git config --get fleet.guards)"`). Switch by pointing the launcher at the other companion's binding. Run `python -m background_task_core runtime-status` with the four runtime arguments to check the authority, and `python -m background_task_core ingest` to create or update the database.

## Recovery

The registration journals, receipts and pointer under the companion's `data/background-task-core/registration/` are versioned there. The `vault/` objects they reference are DPAPI ciphertext bound to the Windows user and machine, so the companion keeps them as local, unversioned state together with `registration/locks/`, `registration/stages/`, `runlog-ingest-ok.txt` and the refreshed status snapshot; a journal whose vault object is missing fails closed. The database can be rebuilt from the retained health log and the current event-log window with `python -m background_task_core ingest --backfill`; events older than that window survive only in the monthly export, which omits raw return codes, task settings history and ingest attempts.
