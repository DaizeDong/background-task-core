# Tasks that ship with a skill

A skill that needs scheduled tasks declares them in its own repository, and one command installs them through the registration authority. Nobody edits the authority's desired declarations by hand for a skill's tasks, and a machine gets exactly the tasks of the skills it has.

The invariant: every task a skill owns is declared once, in `automation/tasks.json` of that skill. Machine choices (enabled, a different time, a console category) live once, in the skill's private companion. The authority's desired file, generated launcher, projections and receipt are derived from those two files by `install-skill`.

## The manifest: `<skill root>/automation/tasks.json`

Public, so it holds no machine value: paths go through `${SKILL_ROOT}`, `${COMPANION_ROOT}` or a Windows `%NAME%` reference, never a home directory, account or SID.

| Field | Required | Meaning |
| --- | --- | --- |
| `schemaVersion` | yes | `1` |
| `skill` | yes | The skill's repository name. It names the companion (`<SKILL>_CONFIG`, hyphens become underscores) and marks ownership. |
| `tasks[].id` | yes | Stable full task ID `component/name`. A task that already exists keeps its current ID, even when the component part is not the skill's name. A retired ID is never reused. |
| `tasks[].name` | yes | The Task Scheduler name, unique on the machine. |
| `tasks[].purpose` | yes | One sentence; it becomes the console description unless the companion overrides it. |
| `tasks[].enabled` | yes | `true` to create the task enabled, `false` for an opt-in task. |
| `tasks[].trigger` | yes | `{"type": "daily", "at": "HH:MM"}`, `{"type": "weekly", "at": "HH:MM", "days": ["mon", ...]}`, `{"type": "monthly", "at": "HH:MM", "days": [1, ...]}` (days of the month, every month), `{"type": "interval", "minutes": N}` (or `seconds`), `{"type": "logon"}`, or a list of these. |
| `tasks[].timezone` | no | `local` (default: wall-clock time on this machine, across DST) or `UTC`. |
| `tasks[].argv` | yes | Command and arguments. `argv[0]` is a `%NAME%` reference or `${...}` placeholder followed by a path. |
| `tasks[].cwd` | no | Working directory, default `${SKILL_ROOT}`. |
| `tasks[].kind` | no | `oneshot` (default), `daemon` or `dispatcher`. |
| `tasks[].run_level` | no | `LeastPrivilege` (default) or `HighestAvailable`. The task always runs as the installing user with an interactive token. |
| `tasks[].power` | no | `disallow_start_on_batteries`, `stop_on_batteries`, `wake_to_run`; each defaults to `false`. |
| `tasks[].timeout_seconds` | yes | Scheduler execution limit; `0` for none. |
| `tasks[].concurrency` | no | `IgnoreNew` (default), `Parallel`, `Queue` or `StopExisting`. |
| `tasks[].backup` | no | Default `true`: the task is in the `TaskNames` projection, so this repository's `BackgroundTaskCoreXmlExport` task (`windows/export-task-xml.ps1`) exports its XML to the companion's `data/task-exports` daily. |
| `tasks[].checks` | no | `[{"id", "required", "health": {...}}]`. `health` is the task-health row (`label`, `max_age_hours`, `artifact`, `ok_exit_codes`, ...); `${...}` in it becomes a `~/` path. |

Synthetic example:

```json
{
  "schemaVersion": 1,
  "skill": "acme-skill",
  "tasks": [
    {
      "id": "acme-skill/acmesync",
      "name": "AcmeSync",
      "purpose": "Nightly sync of the example inbox.",
      "enabled": true,
      "trigger": {"type": "daily", "at": "03:15"},
      "argv": ["%ProgramData%\\miniconda3\\pythonw.exe", "${SKILL_ROOT}/scripts/sync.py", "--config", "${COMPANION_ROOT}"],
      "timeout_seconds": 900,
      "checks": [{"id": "output", "required": true,
                  "health": {"label": "acme sync (daily 03:15)", "max_age_hours": 26,
                             "artifact": "${COMPANION_ROOT}/data/last-ok.stamp"}}]
    }
  ]
}
```

## Variables

`${SKILL_ROOT}` is written into the declaration as `%USERPROFILE%\<path of the skill root under the profile>`; a skill root outside the user profile is refused. `${COMPANION_ROOT}` is written as `%<SKILL>_CONFIG%`, so `acme-skill` uses `%ACME_SKILL_CONFIG%`. Every `%NAME%` the declaration ends up with must be set in the process, user or machine environment when `install-skill` runs, and `<SKILL>_CONFIG` must name the same directory as the resolved companion; otherwise the command refuses with `variable_unset` or `companion_variable_mismatch`. The Scheduler expands the references when the task starts, so the registered task and its launcher name no machine path.

## The companion override: `<companion root>/automation/tasks.local.json`

Optional and private. It is declared in the skill's storage contract like any other companion file.

```json
{"schemaVersion": 1, "tasks": {"acme-skill/acmesync": {"enabled": false, "trigger": {"type": "daily", "at": "04:40"}, "category": "maintenance"}}}
```

Allowed per task: `enabled`, `trigger`, `timezone`, `timeout_seconds`, `power`, `run_level`, `category` (an existing console category ID; default is the task's current category, else `uncategorized`) and `description`. A key for a task the manifest does not declare refuses.

## The command

```text
python -m background_task_core install-skill --skill-root <abs path> [--companion-root <abs path>]
    [--enable keep|hold|declared] [--dry-run]
    --runtime-config CONFIG --private-root PRIVATE --state-root STATE --vault-root SECRETS
```

The four runtime arguments are the installed runtime's, from the companion's `data/launcher-binding.json` `runtime` block, as for every other authority command. On the installed generation the call is `<runtime.Python> -I -B <runtime.InstalledRoot>\launch.py -m background_task_core install-skill ...`. Without `--companion-root` the companion is found by `fleet_guards.runtime.companion_resolver`.

What it does, in order:

1. Reads the manifest (no manifest: reports `"manifest": false` and changes nothing) and the companion override, and builds one component task entry and one private binding per task. The entry's `entrypoint` is `skill:<skill>`, which is how later runs know the task belongs to this skill.
2. Refuses if a registration journal is pending. Writes the entries and bindings into the authority's desired file, gives each task a generated launcher `launchers/<Name>.vbs` in the runtime configuration, and drops the task's machine override so the binding is the single source.
3. For each task: a missing task gets a creation check first (an equivalent declared action under another ID refuses with `equivalent_task_exists`); then `registration-plan` and `apply`, with `--migrate` until the task is declaration-owned. A task whose live definition, launcher and projections already match is left alone. Any refusal is reported for that task and the others continue.
4. Retires, through the authority, every task this skill installed earlier that the manifest no longer lists. The declaration stays in the desired file with its tombstone, so the ID cannot come back.

Enabled state: `keep` (default) never silently enables or pauses an existing task; a new task gets the companion's `enabled`, else the manifest's. A companion `enabled` is an owner decision and is applied to existing tasks too. `hold` registers everything disabled (a new machine during the parallel period). `declared` applies the companion or manifest value to every task (activation after review).

`--dry-run` writes nothing: it compiles the merged declarations the way registration does and prints each task's declaration (without the account SID), its launcher, and whether it would be created, updated, checked or retired.

Output is one JSON object; each task row has `task_id`, `name`, `enabled`, `action` (`created`, `updated`, `unchanged`, `enabled`, `disabled`, `retired` or `refused`) and, when refused, `code`. Exit 0 when nothing was refused, 3 when a task was refused, 2 when the input was rejected before anything changed. Running it again after success reports every task `unchanged`.

## Restore

A new machine runs it from the owner's restore procedure after the repositories are cloned: `install-skill --enable hold` for every cloned repository that has `automation/tasks.json`, then again with `--enable declared` when the owner activates tasks after review. These tasks need no entry in a separate per-task restore table; the manifest is their declaration.

## Limits

The account is the installing user. Registration state lives in the shared private companion, so a second machine whose receipt names definitions it does not have refuses those tasks with `scheduler_ownership_changed` until its own authority is set up; the refusal is per task.
