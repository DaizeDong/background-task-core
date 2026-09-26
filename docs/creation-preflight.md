# Creation preflight

The automation-management skill compares intent before adding a schedule. The registration
backend independently checks action identity so a renamed declaration cannot create the same
command again. This does not depend on the web console.

`creation-check --task-id ID` takes the same four runtime/configuration roots as
`registration-plan`. It reads current declared tasks and returns `reuse`, `update` or `create`,
matching task IDs, enabled/running state and an inventory revision. No payload runs, task is
registered, or task is enabled. Disabled matches remain disabled.

The comparison preserves command argument contents and compares the executable, working
directory and principal. Frequency is intentionally excluded: changing the cadence of the
same action should update its existing definition. Different commands or targets require
semantic review by the skill; this check cannot prove arbitrary scripts equivalent. It covers
declared tasks, not every unmanaged Scheduler entry.

Creating a missing task via registration rechecks the declared inventory under the existing
authority lock. An equivalent action raises `equivalent_task_exists` before publication.
Unknown reads refuse creation. Existing migration/control/retirement keeps its original
authority path and is not mistaken for a new task. This guard does not merge scripts, change
cadences, retire existing jobs or add another scheduler.
