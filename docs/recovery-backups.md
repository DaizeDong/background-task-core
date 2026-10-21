# Recovery backups

`python -m background_task_core backup database` writes a complete SQLite backup, including
raw return codes, configuration history and ingestion attempts. It uses SQLite's
backup API against the current database opened read-only; it does not ingest events.
The resulting standalone database includes committed WAL data. Monthly event exports
remain useful history but cannot replace this backup.
The default ZIP contains `console.sqlite3`, keeping versioned snapshots compact.
An explicit output ending in `.sqlite3` writes the standalone database directly.

`python -m background_task_core backup registration` captures the current registration
pointer and receipt, declaration/runtime configuration, configured projection and
launcher files, pending journals, retained stages and detached files, and referenced
vault objects. It holds the existing authority lock and checks that captured source
snapshots did not change before publishing. It does not register or run tasks.
An optional `--launcher-binding` includes the launcher's binding file.

The source-checkout tools `backup_console_db.py` and `export_registration_recovery.py`
delegate to these same commands. No additional scheduled task is created.

Both commands default to timestamped files in the resolved private companion's
`data/recovery` directory. That directory must be included in the companion's normal
versioned backup policy. `--output` selects an explicit external private file whose
parent already exists. Neither command writes DATA into the public source tree or
falls back there. The database command accepts `--source`; otherwise it uses
`BACKGROUND_TASK_CORE_DB` or the companion database. Registration uses the existing
four `BACKGROUND_TASK_CORE` runtime environment settings, or all four explicit
`--runtime-config`, `--private-root`, `--state-root`, and `--vault-root` arguments.

The registration ZIP contains private configuration in addition to ciphertext; keep
the whole archive private. Protected ciphertext requires the original Windows account
and machine. Copying files does not preserve the identities used by pending journal
recovery. This is evidence for reviewed recovery, not an automatic restore package,
not a full terminal authority history export, and not a cross-machine decrypt promise.
The existing task XML export/restore remains a separate capability. Database and
registration captures are separate snapshots, not one atomic system snapshot.

Older interrupted journals without complete `file_before` or `scheduler_before`
maps can use surviving step before-images, pinned input task bindings and existing
authority proofs. Unchanged supporting files must match receipt fingerprints and
unchanged tasks must match receipt identities. Missing input generations, missing
protected step images, changed resources and ambiguous staging ownership remain
errors. Recovery never invents an absent resource or marks unsupported evidence as
successful. Keep the active database, vault and journals until recovery is reviewed.
