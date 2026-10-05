# Storage and completed-work retirement

`storage.contract.json` describes paths relative to the private companion's data directory.
Its byte limits are review thresholds. They never authorize truncating current state to reach a size target.

Keep the launcher binding, database and required SQLite side files, durable run-event export,
registration recovery state, and referenced task declarations. The database is not fully reproducible
with `--backfill`: historical task configurations and ingest attempts are retained there, and the OS
event log rotates. The JSONL export preserves normalized events, but omits `rc_raw` and does not contain
the other database tables. No complete JSONL-to-database restore command is currently provided.

Refresh the versioned database recovery snapshot before backing up or committing private state:
`python tools/backup_console_db.py --data-root <private-data>`.
It proves PRIVATE visibility, reads a consistent SQLite transaction through the online backup API,
and compares integrity, schema and every table count before publishing. It replaces one
`<private-data>/task-console/recovery/current.zip` and its fixed `current.json` receipt. The archive
contains the standalone database and its own receipt, so WAL/SHM files and live locks are not copied.
The OS lock rejects overlapping captures. A failed refresh exits nonzero and must stop the enclosing
backup. This command provides an explicit refresh entry; scheduling depends on the installation's
existing backup integration.

Use the same command with `--verify` to decompress and check the saved database without refreshing it.
It checks archive contents, database digest, integrity, schema, table counts and the external receipt.
For recovery, verify first and extract `console.sqlite3` into a separate private staging directory.
Replacing the live database is a separate operation requiring its writer to be stopped and the
existing database/side files preserved. A snapshot covers its recorded capture time; later writes
require another refresh. Keep one current snapshot in private Git, with older versions in Git history.

Create new temporary development work under `<private-data>/work/`. Before retiring a work directory,
commit its useful code, keep the current release handoff and one concise final result, and confirm that
its writers have stopped. Record exact paths in `<private-data>/storage-retention.json`, with
`schema_version: 1` and `retirements` entries containing `path`, `completed: true`,
`source_reconciled: true`, `active_writer: false`, `final_deliverable` and `final_sha256`.
Extra `protected_paths` can only add protection. Keep the record under 256 KiB and 64 entries.

Preview with `tools/cleanup_storage.ps1 -DataRoot <private-data>`. After reviewing the exact plan,
use the same command with `-Apply`. The planner proves PRIVATE repository visibility, protects core
paths, rejects links and changed final results, and fingerprints the candidates twice. The PowerShell
remover checks absolute containment and reparse points before every `Remove-Item -LiteralPath`.
Unlisted work is retained. A running process referring to a candidate blocks cleanup.

The tool prints a small receipt. Replace the previous receipt in `<private-data>/storage/` instead of
creating another dated archive. Raw diagnostic sampling ends with its session; after preserving the
conclusion and configuration, its completed raw observations can use the same explicit retirement path.
