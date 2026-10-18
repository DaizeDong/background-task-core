# Roadmap

Current: **v0.1.0**

## v0.1.0 (current)

The current feature set includes the original extraction and subsequent source changes.
[docs/changing-this.md](docs/changing-this.md) defines maintenance invariants;
[CHANGELOG.md](CHANGELOG.md) retains their history.

- A loopback-only single page server with a per start token, a host allowlist, and task names re-enumerated against the live system before any verb runs.
- Panels over scheduled tasks, artifact freshness, git repositories under one root, skills, the memory pool, plugins, disk, saved conversations, and the LLM call ledger.
- A self check strip that names every configured path, whether it was reached, and how fresh it was, and counts unconfigured sources in its own denominator.
- Out of band ingestion of the Windows Task Scheduler Operational channel into a SQLite read model, plus a durable export for the history that channel drops.
- Maintenance verbs in one closed table: run, stop, enable, disable, retire a task; archive or restore a skill; archive a memory entry; enable or disable a plugin; delete abandoned clone staging directories; fetch a repository.

The following capabilities were added after the initial extraction:

- Split page markup from static CSS and JavaScript.
- Add declaration planning, reviewed registration and work-platform projections over owner records.
- Bind database/export writes to the pinned source contract and PRIVATE versioned storage.
- Add a read-only local configuration doctor.

Nothing here establishes installed service parity, current ingestion, external delivery or
publication. Historical audit and release records remain in [CHANGELOG.md](CHANGELOG.md); configuration scope is in [CONFIG.md](CONFIG.md).

## Planned

- **Define history retention independently of the event channel.** The historical measurement found a roughly five-day event window; actual retention depends on configured size and event volume. Older records survive only when ingestion captured them in the durable export.
