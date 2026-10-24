# Creating a scheduled task

How a new Windows scheduled task gets onto a machine that runs this authority. The manifest
contract is [skill-tasks.md](skill-tasks.md); the producer contract is
[task-registration.md](task-registration.md). This page is the procedure around them and the
settings catalog. Vendor tasks that came with installed software are out of scope.

## Invariants

1. Every task is declared once: a skill's task in that skill's `automation/tasks.json`, machine
   choices in its private companion's `automation/tasks.local.json`. A task that belongs to no
   skill is declared in the authority's private bootstrap `desired.json`. There is no second task
   list anywhere; the backup allow-list (`projections/TaskNames.ps1`) and the health watch list are
   projections of the registration.
2. Nothing registers a task except this authority. No `Register-ScheduledTask`, no `schtasks`, no
   hand-written `.vbs`; the authority generates the hidden launcher.
3. A task's health artifact is written only on its success path. A file the failure path also
   writes cannot prove success and must not be declared as the artifact.
4. "Clean" and "checked nothing" are different outputs. Any check that enumerates tasks fails when
   it enumerates zero.
5. Retirement goes through the authority, which also removes the task from every projection.

## Procedure

1. **Name.** Unique under the Task Scheduler root, prefixed by its family so the console groups it.
   `Get-ScheduledTask -TaskName <name> -ErrorAction SilentlyContinue` returns nothing.
2. **Choose settings.** Pick every field from the catalog below; "use the default" is not an
   answer, because several defaults are traps on a laptop.
3. **Declare and install.**
   - A skill's task: write `automation/tasks.json`, then run
     `python -m background_task_core install-skill --skill-root <skill> --dry-run`, read the
     declarations it would write, and run it again without `--dry-run` (with the four root
     arguments from the launcher binding's `runtime` section). It writes the desired and runtime
     config, generates the launcher, runs creation-check, registration-plan and apply per task,
     and retires tasks removed from the manifest. A second run reports `unchanged`.
   - A task outside any skill: declare it in the bootstrap `desired.json` (a `components` task and
     a `bindings.tasks` binding; `argv`, `cwd` and `source_root` as `%NAME%` paths only) with a
     `launchers/<name>.vbs` entry in `runtime-config.json`, then run
     `registration-plan --task-id <id>` and `apply --request <plan> --expected-revision <input_revision>`.
   - An existing task without an ownership receipt (created by hand), or a managed task changed
     outside the authority (`scheduler_ownership_changed`), is adopted with
     `registration-plan --replace-identity task-definition:<sha256> --reason <why>`.
   - The authority refuses to rewrite a running task. Stop a daemon first; stopping ends only
     `wscript`, so end the payload process by its PID, apply, then `control --name <name> --verb run`,
     all before its next trigger.
   - Run the registration CLI from this repository's source (`PYTHONPATH=<checkout>/scripts`) with
     the installed generation's Python 3.11: from Python 3.12 on, Windows `st_ino` values differ,
     so another interpreter computes file identities that match no receipt.
4. **Health.** Give the task a `checks[].health` row in the manifest (`label`, `max_age_hours`,
   `artifact`, `artifact_max_age_hours`, `ok_exit_codes` when exit codes encode a verdict). A task
   watched by something else declares `watched_elsewhere` with the reason; an exception is
   declared, never the result of a name the checker did not match.
5. **Run once.** `apply` returns `status=committed`; `Get-ScheduledTask` shows the generated
   launcher action; `control --name <name> --verb run` runs it, and `LastTaskResult` plus the
   artifact show the result.
6. **Checker.** `windows/task-registry-check.ps1` asserts that every active self-installed task is
   in the scheduler, the backup allow-list and the health list, and that `.log` artifacts declare
   their semantics. The health monitor runs it every round. It does not check settings drift yet;
   the catalog below is applied by hand.

Merging tasks: the surviving task keeps one of the original task IDs, the others are retired.
Retiring: the console's delete flow (`/api/task/delete/plan`, then `/apply`) retires through the
authority and cleans the category, health list and exported XML. It requires the previous
registration transaction to be committed.

Generated launchers wait for the payload, so exit code and duration are the payload's. A process
the payload starts detached is outside that wait.

## Settings catalog

Defaults measured from `(New-ScheduledTaskSettingsSet)` on Windows 11.

### Defaults that bite

| Setting | Default | Set to | Why |
|---|---|---|---|
| `DisallowStartIfOnBatteries` | True | False (`-AllowStartIfOnBatteries`) | On battery the task refuses to start |
| `StopIfGoingOnBatteries` | True | False (`-DontStopIfGoingOnBatteries`) | Unplugging kills a running task |
| `StartWhenAvailable` | False | True | A trigger missed while asleep or off is skipped for good |
| `ExecutionTimeLimit` | PT72H | the task's real duration | 72 hours is no limit; `PT0S` also means unlimited |
| `MultipleInstances` | IgnoreNew | usually keep | Scope is one task, not across tasks; `Queue` serializes, `Parallel` runs side by side |
| `Priority` | 7 | 7 or 8 | 7 is already below normal |
| `IdleSettings.StopOnIdleEnd` | True | False | Touching the machine can kill the task even with `RunOnlyIfIdle=False` |

### Other settings

`AllowDemandStart` (True), `AllowHardTerminate` (True), `Compatibility` (3), `DeleteExpiredTaskAfter`
(null), `Enabled` (True), `Hidden` (False), `RunOnlyIfIdle` (False), `RunOnlyIfNetworkAvailable`
(False), `WakeToRun` (False), `RestartCount` (0), `RestartInterval` (null),
`DisallowStartOnRemoteAppSession` (False), `UseUnifiedSchedulingEngine` (True), `volatile` (False).
`IdleSettings`: `IdleDuration` (PT10M), `WaitTimeout` (PT1H), `RestartOnIdle` (False).
`MaintenanceSettings` is null by default (`Period`, `Deadline`, `Exclusive`).

The cmdlet parameters have inverted names: the CIM properties `AllowDemandStart`,
`AllowHardTerminate`, `DisallowStartIfOnBatteries`, `StopIfGoingOnBatteries`, `StopOnIdleEnd`,
`Enabled` are set by `-DisallowDemandStart`, `-DisallowHardTerminate`, `-AllowStartIfOnBatteries`,
`-DontStopIfGoingOnBatteries`, `-DontStopOnIdleEnd`, `-Disable`. Compare read-back values by CIM
property name. `UseUnifiedSchedulingEngine` and `volatile` have no cmdlet parameter.

### Triggers

`New-ScheduledTaskTrigger` covers `-Once`, `-Daily`, `-Weekly`, `-AtStartup`, `-AtLogOn`. The CIM
layer also has `MonthlyTrigger`, `MonthlyDOWTrigger`, `IdleTrigger`, `EventTrigger`,
`RegistrationTrigger` and `SessionStateChangeTrigger`, reachable only through `New-CimInstance` or
XML. Every trigger has its own `ExecutionTimeLimit`, separate from the task's. A `Repetition` with a
`Duration` stops repeating for good when the duration ends; for endless repetition leave
`Duration` unset.

### Principal

`RunLevel`: `Limited` (default) or `Highest`. `LogonType`: `None`, `Password`, `S4U`, `Interactive`,
`Group`, `ServiceAccount`, `InteractiveOrPassword`. Tasks on one machine do not share one SID and
integrity level; a lock shared across an elevated and a normal task has to allow for that.

## Concurrency between tasks

Heavy tasks that may overlap share a gate of N named `Global\` mutexes used as slots, not a
`System.Threading.Semaphore` (a hard-killed holder leaks a semaphore slot for good; a killed mutex
holder hands ownership to the next waiter as abandoned). Contract:

1. Normalize the name's case and reject any name without the `Global\` prefix: a different case or
   a `Local\` name silently creates a separate object, which looks like no gate at all.
2. Never wait without a bound, and keep the bound below the task's `ExecutionTimeLimit`.
3. Treat an abandoned mutex as acquired.
4. Hold the slot for the payload's whole lifetime, not just the launcher's.
5. The task decides what happens when no slot is free (skip this run or back off); a skip is
   logged as a skip, never a silent exit 0.
