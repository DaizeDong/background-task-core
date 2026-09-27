# Roadmap

Current: **v0.1.0**

## v0.1.0 (current)

Feature names only. Why each one behaves the way it does lives in `docs/changing-this.md`, which is the single home for those invariants, and what changed lives in `CHANGELOG.md`.

- A loopback-only single page server with a per start token, a host allowlist, and task names re-enumerated against the live system before any verb runs.
- Panels over scheduled tasks, artifact freshness, git repositories under one root, skills, the memory pool, plugins, disk, saved conversations, and the LLM call ledger.
- A self check strip that names every configured path, whether it was reached, and how fresh it was, and counts unconfigured sources in its own denominator.
- Out of band ingestion of the Windows Task Scheduler Operational channel into a SQLite read model, plus a durable export for the history that channel drops.
- Maintenance verbs in one closed table: run, stop, enable, disable, retire a task; archive or restore a skill; archive a memory entry; enable or disable a plugin; delete abandoned clone staging directories; fetch a repository.
- A test suite that targets Windows, with poisoning and negative controls recorded per gate, run on `windows-latest` in CI under a collected count floor.

## Planned

- **Bring the storage and memory thresholds back to the server.** The backend ships percentages and several places in the page each apply their own threshold. That is one rule written more than once, and the drift will render normally in every copy.
- **Split `console.html`.** It is past four thousand lines and holds the CSS, the markup and the page script together. The audit proposed it and it has not been done.
- **A run history that does not depend on the event channel.** Everything outside the channel's roughly five day window exists only because ingestion happened to run, which makes the durable export the system of record for a period nobody declared.
- **Finish the audit in `docs/cleanup-plan.md`.** Its top section records which items landed; what remains is listed there rather than copied here.
