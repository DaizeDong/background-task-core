---
name: automation-management
description: Use when creating, changing, consolidating or retiring recurring background jobs, Windows scheduled tasks, maintenance routines or plugin-installed automations.
---

# Manage recurring automation at its source

Use the existing registration authority and the producing skill. A console row is a view of
the job, not its owner. Todos and appointment reminders use `schedule-reminder` instead.

## Before creating

Read the current task inventory, including disabled/retired entries, and inspect the actual
entrypoints of relevant candidates. Compare purpose, target/account, inputs, outputs, delivery
destination, frequency and execution identity. Names and descriptions alone are insufficient.
State the decision: **reuse**, **extend**, **create**, or **retire a proven duplicate**.

- The same action for the same target reuses the existing task ID. A new request or a new name
  does not require another schedule. Extend its configuration or due-step list when needed.
- Different accounts, occurrences, deadlines, privileges or deliberate worker capacity may
  require independent execution. Keep their identities; do not merge by title similarity.
- Related maintenance can share a due dispatcher only if each step retains its own frequency,
  result, timeout and retry. Keep failure detection independent of the runner it monitors.
- Preserve paused/retired state. Reuse does not mean silently enabling a disabled task.

Prepare the private declaration through the owner's supported proposal workflow, then run
`python -m task_console creation-check --task-id <ID>` with the installed runtime's explicit
`--runtime-config`, `--private-root`, `--state-root` and `--vault-root` bindings. This read-only
check returns existing action IDs and the inventory revision. Its coverage is declared tasks;
also review relevant unmanaged jobs before adoption. Follow the local artifact resolver rather
than copying a generation path into a new permanent launcher.

Registration rejects a new identity for an equivalent command, working directory and principal.
Use the returned existing ID. An unknown/unreadable inventory is not an empty inventory. Do not
bypass the authority with direct Scheduler commands or by renaming the job.

## Preserve this in the producing skill

Record the purpose/target identity, owner, cadence, change predicate, evidence of success and
retirement condition. Before its installer creates a task, repeat the inventory/reuse check.
Persist source request IDs and retirement/replacement mappings so retries and reinstallations
reuse existing work. Do deterministic checks without a model; invoke reasoning only for new
input or unresolved content decisions through the current llmcall interface.

Apply approved changes through registration, preserve before-images, and read back the actual
definition and enabled state. Verify owner results separately from successful registration.
Report what was reused or changed and its ID; never describe an enqueued job as completed.

Native configuration and storage readiness are documented in [CONFIG.md](../../CONFIG.md).
A source plan or local configuration check does not prove Scheduler adoption or execution.
