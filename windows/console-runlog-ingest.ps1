# Daily full ingest for the task console: the run-history half that the hourly pass skips.
#
# WHY THIS EXISTS AS ITS OWN TASK. task-health-monitor.ps1 runs the ingester every hour with
# --skip-runlog, so health observations stay current while RUN EVENTS never advance at all. The
# monitor's own comment points at a daily task named ConsoleIngestFull as the thing that backfills
# them; that task did not exist, and had not for as long as the comment had. Measured 2026-09-10:
# health ingested minutes ago, runlog 17 hours old and only ever advancing when someone ran the
# script by hand, with run_event covering 09-01..09-09 on 09-10.
#
# That gap is not recoverable later. The Windows TaskScheduler Operational log is a circular buffer,
# measured at roughly five days before the oldest records are overwritten, so anything not ingested
# inside that window is gone from every source there is.
#
# WHY IT IS NOT FOLDED BACK INTO THE HOURLY PASS. It used to be, and it killed that task on every
# run for 73 consecutive hours: the ingester passed a 900 second budget to runlog.ps1 while the
# monitor's own ExecutionTimeLimit was 600, so the inner budget was unreachable and the outer
# guillotine fired first, every time. Separate task, separate limit, and this one's limit is set
# above the inner budget on purpose.
#
# WHY IT SETS BACKGROUND_TASK_CORE_HISTORY. The ingester treats a missing health log as a SKIP and
# returns 1 for it, correctly refusing to call a partial pass a complete one. Without this line the
# task would exit 1 on every single run while doing its actual job perfectly, which is the fastest
# way to teach someone to ignore a red task.
# WHICH CODE RUNS. The installed background-task-core generation, `python -m background_task_core ingest`, selected
# through the same launcher binding as the console; the database path comes from that binding.
[CmdletBinding()]
param(
  [int]$RunDays = 60,
  [string]$LauncherBindingFile = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'background-task-core-binding.ps1')

# Refuse rather than run a pass that silently covers less than it claims to. A missing binding or
# interpreter must not exit 0: this task's whole value is that its absence is visible.
$configuration = Read-BackgroundTaskCoreLauncherBinding -BindingFile $LauncherBindingFile
$runtime = $configuration.Runtime
$resolved = Resolve-BackgroundTaskCoreBinding @runtime -ReadOnly
$env:BACKGROUND_TASK_CORE_DB = $configuration.Environment['BACKGROUND_TASK_CORE_DB']
$env:BACKGROUND_TASK_CORE_HISTORY = $configuration.Environment['BACKGROUND_TASK_CORE_HISTORY']
if (-not $env:BACKGROUND_TASK_CORE_HISTORY) { throw 'Launcher binding names no BACKGROUND_TASK_CORE_HISTORY; the ingest would skip the health log' }

& $resolved.python -I -B -X utf8 -m background_task_core ingest --run-days $RunDays
$rc = $LASTEXITCODE

# Pass the ingester's own verdict through unchanged. Translating it here would put a second opinion
# about the same run next to the first one, and the console reads the first.
exit $rc
