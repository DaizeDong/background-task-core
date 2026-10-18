# Storage and completed-work retirement

`storage.contract.json` describes paths relative to the private companion repository root.
Runtime paths therefore begin with `data/`; exact root Git configuration and the companion guide
have separate owners. The shared checker must receive the entire PRIVATE worktree, never its
`data/` subdirectory. Its byte limits are review thresholds. They never authorize truncating
current state to reach a size target. The aggregate working-data review threshold
is 256 MiB, excluding Git administration. The operating allowances are 128 MiB for the
database, 48 MiB for the event export, 32 MiB for recovery and 8 MiB for registration,
with remaining room for coordination, configuration and retained historical artifacts.
Current protected operating state can exceed the former 64 MiB development threshold.
All retained retired bytes continue to count until actual reviewed removal. Exceeding
either the aggregate or an artifact limit requires retention or representation review;
it never authorizes automatic truncation of database or recovery state.

The native cleanup and database-backup commands still accept `<private-data>`, the companion's
`data/` directory. Their retirement records remain relative to that directory. They do not load
`storage.contract.json`; changing the shared contract's path basis does not change their runtime
paths or their separate protection rules.

Legacy development artifacts use explicit top-level namespaces, so a date in a task declaration
cannot also classify it as retired work. The legacy scopes partition an 8 MiB review budget:
chain-inspection work has 4 MiB and the other four scopes have 1 MiB each.
These shares are bounded review thresholds, not estimates of required storage. Classification alone
does not establish completed work or authorize deletion: the private retirement record must still
name exact reviewed roots. Unrecognized paths remain undeclared and require a separate review of
their producer, references and recovery needs; historical cleanup does not justify a broader source
contract.

Legacy profile copies under `data/profile-rollback/legacy-*/` and historical one-click release
workspaces under `data/releases/*-one-click/` are separately classified as retired development
artifacts. Preserve any unique useful code, required inputs and bounded conclusions before a
reviewed retirement. Dated storage follow-up observations have the same source, result and writer
checks; a terminal or superseded status does not establish that a lock has no holder. None of these
scopes admits a new runtime output location or permits deletion by age. Unrecognized profile,
release, helper and recovery siblings remain undeclared. The native cleaner conservatively refuses
the entire profile-rollback namespace even when the shared contract marks legacy copies retired.

Private `tools/release/` and `tools/delete_followup/` source files are bounded reconciliation inputs.
Their useful code and tests must be preserved in the appropriate source owner before retirement;
they are not core runtime history. The delete helper's exact `config.json` remains core while its
configured consumer needs it. This classification does not authorize running either helper.

Keep the launcher binding, database and required SQLite side files, durable run-event export,
registration recovery state, and referenced task declarations. The exact
`data/task-console/README.md` documents the current layout. The ingest success stamp is
rebuildable freshness metadata; its age never certifies present success. Other logs and copied
runtime source receive no console-wide core owner. The database is not fully reproducible
with `--backfill`: historical task configurations and ingest attempts are retained there, and the OS
event log rotates. The JSONL export preserves normalized events, but omits `rc_raw` and does not contain
the other database tables. No complete JSONL-to-database restore command is currently provided.

Refresh the versioned database recovery snapshot before backing up or committing private state:
`python tools/backup_console_db.py --data-root <private-data>`.
It reads a consistent SQLite transaction through the online backup API,
and compares integrity, schema and every table count before publishing. It replaces one
`<private-data>/task-console/recovery/current.zip` and its fixed `current.json` receipt. The archive
contains the standalone database and its own receipt, so WAL/SHM files and live locks are not copied.
The contract protects only the exact current ZIP and external JSON receipt as core recovery
artifacts. `snapshot.lock` is separately rebuildable coordination state: its presence is not
ownership evidence, and any retirement requires proving the OS lock has no holder. Staged snapshots
and additional archives remain undeclared until separately reviewed.
The OS lock rejects overlapping captures. A failed refresh exits nonzero and must stop the enclosing
backup. This command provides an explicit refresh entry; scheduling depends on the installation's
existing backup integration.

Use the same command with `--verify` to decompress and check the saved database without refreshing it.
It checks archive contents, database digest, integrity, schema, table counts and the external receipt.
For recovery, verify first and extract `console.sqlite3` into a separate private staging directory.
Replacing the live database is a separate operation requiring its writer to be stopped and the
existing database/side files preserved. A snapshot covers its recorded capture time; later writes
require another refresh. Keep one current snapshot in private Git, with older versions in Git history.

An existing scheduled backup can call
`python tools/backup_console_db.py --scheduled --source-db <launcher-selected-database>`.
The caller must obtain that path from `Read-TaskConsoleLauncherBinding` and pass its
`Environment['TASK_CONSOLE_DB']` value. The helper supplies that exact value to the console's domain
resolver; an unrelated ambient DB setting cannot silently select a different database. Only the
reviewed `<private-data>/task-console/console.sqlite3` layout is supported. Other layouts, a missing
selected database, or a data root inside this source tree fail before capture.

Scheduled publication requires the companion's `main` branch. It holds the snapshot lock through
capture, staging, normal-hook commit and push. Only `current.zip` and `current.json` are staged and
committed; other staged changes or unrelated unpublished commits block the operation. Unstaged
unrelated work stays untouched. An unchanged verified database reuses the current archive and creates
no commit. A failed push exits nonzero and leaves its local recovery commit available for the next
attempt. The next attempt may push only commits whose changed paths belong to that exact pair.

Scheduled stdout is one JSON receipt with `status: published` or `unchanged`; normal Git and hook output
goes to stderr without truncation. The caller must check the exit code before reading the receipt.
Calling scheduled mode without a bound source can only report `status: skipped, reason: uninitialized`
when the shared resolver finds no companion and there is no console configuration in the environment;
it never infers an existing default database for publication.

Current registration also depends on protected input files in its configured vault. The vault is `<companion>/data/task-console/vault/` (artifact `registration-vault`), selected by `runtime.VaultRoot` in `data/launcher-binding.json`; it holds only `task-console-<domain>-<object>.cred` files, tracked in the PRIVATE companion. They are DPAPI ciphertext: only the original Windows account on the original machine can decrypt them, so cloning the companion elsewhere does not restore registration authority.

Create new temporary development work under `<private-data>/work/`. Before retiring a work directory,
commit its useful code, keep the current release handoff and one concise final result, and confirm that
its writers have stopped. Record exact paths in `<private-data>/storage-retention.json`, with
`schema_version: 1` and `retirements` entries containing `path`, `completed: true`,
`source_reconciled: true`, `active_writer: false`, `final_deliverable` and `final_sha256`.
Extra `protected_paths` can only add protection. Keep the record under 256 KiB and 64 entries.

Preview with `tools/cleanup_storage.ps1 -DataRoot <private-data>`. After reviewing the exact plan,
use the same command with `-Apply`. The planner refuses a data root inside this source tree (a pure path check), protects core
paths, rejects links and changed final results, and fingerprints the candidates twice. The PowerShell
remover checks absolute containment and reparse points before every `Remove-Item -LiteralPath`.
Unlisted work is retained. A running process referring to a candidate blocks cleanup.

The tool prints a small receipt. Replace the previous receipt in `<private-data>/storage/` instead of
creating another dated archive. Raw diagnostic sampling ends with its session; after preserving the
conclusion and configuration, its completed raw observations can use the same explicit retirement path.

Native settings and discovery are specified in [CONFIG.md](../CONFIG.md). Writers resolve paths
without Git or network calls; the only runtime check is that a destination is not inside this source
tree. The exact WAL, SHM and journal leaves have transient
persistence because SQLite controls their lifetime, while their core retention protects pending
transactions until checkpoint or consistent recovery. Eligibility for Git does not make copying
or staging a live database safe; keep coordinated online/cold backup procedures.

Conversation metadata uses the exact `data/user-state/convo-cache.json` leaf. Its selected transcript
files remain the source of truth. Publication uses a unique staging leaf and atomic replacement.
A cache path inside this source tree is refused; a refused or unreadable cache does not stop transcript browsing: `summary.cached` is false and
`summary.cacheError` reports the failure. Failed staging files are retained for explicit review.

Unmanaged task deletion uses the existing `data/user-state/deleted-tasks` directory. Only its
separately declared timestamped XML definitions and JSON receipts are protected recovery state.
Each file is created exclusively. Both are flushed and checked before unregister; failure leaves the task registered and
preserves any partial archive. Unknown user-state siblings remain undeclared.
