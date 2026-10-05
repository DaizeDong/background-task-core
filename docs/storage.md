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

An existing scheduled backup can call
`python tools/backup_console_db.py --scheduled --source-db <launcher-selected-database>`.
The caller must obtain that path from `Read-TaskConsoleLauncherBinding` and pass its
`Environment['TASK_CONSOLE_DB']` value. The helper supplies that exact value to the console's domain
resolver; an unrelated ambient DB setting cannot silently select a different database. Only the
reviewed `<private-data>/task-console/console.sqlite3` layout is supported. Other layouts, a missing
selected database, or unproven PRIVATE visibility fail before capture.

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

Current registration also depends on protected input files in its configured vault. A backup that
excludes every `.cred` file cannot restore that authority. The existing configuration-backup caller
can export the required closure with `tools/export_registration_recovery.py --private-root
<declaration-root> --state-root <registration-root> --vault-root <configured-vault> --output
<absolute-temporary-zip>`. Obtain all three roots from the same authoritative launcher-binding parser,
including any complete runtime overrides. The exporter proves the private declaration repository,
uses the existing authority lock, validates the current pointer/receipt digest, and includes every
protected reference required by that receipt and pending or uncleaned journals. Cleaned terminal
journals do not pull historical before-images into this current snapshot. Missing state or references
fail before replacing an existing archive; there is no initialized-state skip or repository fallback.

The caller stores the ZIP at `secrets/task-console-current/registration-recovery.zip` in its private
configuration export root; that backup owner declares this artifact in its own storage contract.
`manifest.json` has `schema_version: 1`, the selected generation/domain,
source roots, document and protected-reference member paths/byte counts/SHA256 hashes, pending journal
IDs, `missing_references: 0`, and `machine_bound: true`. The archive contains `state/current.json`,
the selected `state/receipts/<generation>.json`, unresolved `state/journals/<transaction>.json`, and
only the referenced `vault/task-console-<domain>-<object>.cred` files. Source readback and ZIP-byte
verification protect capture consistency. They prove ciphertext fidelity, not decrypted reference
identity. Stable inputs produce the same archive bytes.
Each input is bounded at 8 MiB and aggregate member input at 128 MiB; these capture limits never
authorize deletion or truncation of the original state.

Restore requires the matching private declarations and runtime plus the original Windows account
and machine capable of decrypting DPAPI. Verify archive member hashes, stage the state/vault members
separately, and verify current input decoding before any explicitly reviewed live replacement. The
exporter neither registers tasks nor restores filesystem ownership receipts. Cross-machine recovery
and historical rollback are not established by this current ciphertext archive. Keep one current ZIP
in private Git; older versions remain in private Git history.

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
