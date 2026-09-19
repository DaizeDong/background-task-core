# Read-only Scheduler identity and one decreasing deadline. No task runner or policy grant.
param([string]$QueryTaskName = '')

function ConvertTo-TaskDeadline {
    param([Parameter(Mandatory)][string]$Value)
    if ($Value -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,7})?Z$') {
        throw 'task-context: deadline must be an absolute UTC timestamp ending in Z'
    }
    try { return [datetimeoffset]::Parse($Value, [Globalization.CultureInfo]::InvariantCulture) }
    catch { throw 'task-context: malformed UTC deadline' }
}

function Get-SchedulerTaskContext {
    param([Parameter(Mandatory)][string]$TaskName, $Service = $null,
          [datetimeoffset]$Now = [datetimeoffset]::UtcNow)
    if ($TaskName -notmatch '^[A-Za-z0-9_.-]+$') { throw 'task-context: exact root task name required' }
    if ($null -eq $Service) { $Service = New-Object -ComObject 'Schedule.Service' }
    $Service.Connect()
    $task = $Service.GetFolder('\').GetTask($TaskName)
    $path = '\' + $TaskName
    if ($task.Path -cne $path -or $task.Name -cne $TaskName) { throw 'task-context: task path/name mismatch' }
    $instances = $task.GetInstances(0)
    if ($instances.Count -ne 1) { throw 'task-context: exactly one running Scheduler instance required' }
    $instance = $instances.Item(1)
    if ($instance.Path -cne $path -or [int]$instance.State -ne 4) { throw 'task-context: running instance mismatch' }
    $guid = [guid]::Empty
    if (-not [guid]::TryParse([string]$instance.InstanceGuid, [ref]$guid) -or $guid -eq [guid]::Empty) {
        throw 'task-context: Scheduler InstanceGuid required'
    }
    $started = [datetimeoffset]([datetime]$task.LastRunTime).ToUniversalTime()
    if ($started.Year -lt 2000 -or $started -gt $Now) { throw 'task-context: invalid Scheduler start time' }
    try { $limit = [Xml.XmlConvert]::ToTimeSpan([string]$task.Definition.Settings.ExecutionTimeLimit) }
    catch { throw 'task-context: malformed Scheduler execution limit' }
    if ($limit.TotalSeconds -lt 0) { throw 'task-context: negative Scheduler execution limit' }
    # Recheck attribution after reading start/limit; a replaced run is never accepted.
    $again = $task.GetInstances(0)
    if ($again.Count -ne 1 -or $again.Item(1).InstanceGuid -cne $instance.InstanceGuid -or
        ([datetime]$task.LastRunTime).ToUniversalTime() -ne $started.UtcDateTime) {
        throw 'task-context: Scheduler instance changed during observation'
    }
    $deadline = $null
    if ($limit.TotalSeconds -gt 0) { $deadline = $started.Add($limit).UtcDateTime.ToString('o') }
    return [pscustomobject]@{
        task_name=$TaskName; run_id=('scheduler:' + $TaskName + ':' + $guid.ToString('B'))
        started_utc=$started.UtcDateTime.ToString('o'); deadline_utc=$deadline
    }
}

function New-TaskBudget {
    param([Parameter(Mandatory)][ValidateRange(61,2147483647)][double]$TimeoutSec,
          [AllowNull()][AllowEmptyString()][string]$DeadlineUtc = $env:TASK_DEADLINE_UTC,
          [datetimeoffset]$Now = [datetimeoffset]::UtcNow)
    $deadline = $Now.AddSeconds($TimeoutSec)
    if ($DeadlineUtc) {
        $outer = ConvertTo-TaskDeadline $DeadlineUtc
        if ($outer -lt $deadline) { $deadline = $outer }
    }
    return [pscustomobject]@{Deadline=$deadline; Started=$Now; Clock=[Diagnostics.Stopwatch]::StartNew()}
}

function Initialize-TaskBudget {
    param([Parameter(Mandatory)][string]$TaskName,
          [Parameter(Mandatory)][double]$TimeoutSec, [switch]$Scheduled,
          [datetimeoffset]$StartedAt = [datetimeoffset]::UtcNow)
    $budget = New-TaskBudget -TimeoutSec $TimeoutSec -Now $StartedAt
    if ($env:TASK_SCHEDULER_NAME -and $env:TASK_SCHEDULER_NAME -cne $TaskName) {
        throw 'task-context: inherited Scheduler task name mismatch'
    }
    if ($Scheduled -or $env:TASK_SCHEDULER_NAME) {
        $context = Get-SchedulerTaskContext -TaskName $TaskName
        if ($context.deadline_utc) {
            $outer = ConvertTo-TaskDeadline $context.deadline_utc
            if ($outer -lt $budget.Deadline) { $budget.Deadline = $outer }
        }
    }
    # Children inherit the same absolute deadline, never a freshly restarted duration.
    $env:TASK_DEADLINE_UTC = $budget.Deadline.UtcDateTime.ToString('o')
    $null = Get-TaskRemainingSeconds $budget
    return $budget
}

function Get-TaskRemainingSeconds {
    param([Parameter(Mandatory)]$Budget, [double]$FinalizationSlackSec = 60,
          [datetimeoffset]$Now = [datetimeoffset]::UtcNow)
    if ($FinalizationSlackSec -lt 0) { throw 'task-context: negative finalization slack' }
    # Wall-clock deadlines survive process boundaries; monotonic elapsed also prevents
    # a backward clock adjustment from increasing the local allowance.
    $remaining = [math]::Min(($Budget.Deadline - $Now).TotalSeconds,
        ($Budget.Deadline - $Budget.Started).TotalSeconds - $Budget.Clock.Elapsed.TotalSeconds)
    $remaining = [math]::Floor($remaining - $FinalizationSlackSec)
    if ($remaining -le 0) { throw 'task-context: execution budget exhausted before business call' }
    return $remaining
}

if ($QueryTaskName) {
    $ErrorActionPreference = 'Stop'
    Get-SchedulerTaskContext -TaskName $QueryTaskName | ConvertTo-Json -Compress
}
