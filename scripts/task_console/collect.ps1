<#
  collect.ps1 - dump the live Windows Task Scheduler state as JSON on stdout.

  The existing collector emits raw facts without health judgments. An optional explicit
  private binding sends the same capture to task_console.observations for compilation and
  atomic snapshot publication. The server's raw Scheduler JSON contract remains available.

  I3 (spec invariant): enumerating zero tasks is a FAILURE, not an empty result. A collector that
  returns [] when the scheduler service is down looks exactly like a machine with no tasks, and the
  console would render a confident empty page. It throws instead.

  Emits UTF-8 without a BOM on stdout. Callers must decode as UTF-8; PowerShell 5.1 in a detached
  session otherwise hands back the ANSI codepage and every non-ASCII label becomes mojibake.
#>
[CmdletBinding()]
param(
  # Tasks whose name matches this are vendor-installed and out of scope for the console.
  [string]$VendorPattern = '^(NVIDIA|OneDrive|Adobe|Zoom|XRite|MicrosoftEdge|Nahimic|Optane|Intel|NvProfile|SoftLanding|RunPlatform|Lenovo|Launch Adobe)',
  [string]$ObservationBinding = $env:TASK_CONSOLE_OBSERVATION_BINDING,
  [string]$ObservationPython = $env:TASK_CONSOLE_OBSERVATION_PYTHON,
  # Used by the bounded periodic owner; emit local facts without recursive publication.
  [switch]$CaptureOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# Leave ten seconds of the existing server budget for startup and final delivery.
$observationDeadlineMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() + 80000

try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

$all = @(Get-ScheduledTask -TaskPath '\' -ErrorAction SilentlyContinue)
if ($all.Count -eq 0) {
  # Do not soften this into an empty payload. See the header.
  throw 'collect: Get-ScheduledTask enumerated 0 tasks. The scheduler service, CIM, or permissions are broken. This is not an empty machine.'
}

$rows = @()
foreach ($t in $all) {
  if ($t.TaskName -match $VendorPattern) { continue }
  # Get-ScheduledTaskInfo can fail (access denied, a task registered by an installer that is
  # mid-uninstall, a corrupt registration). The old `catch { }` swallowed that: every info field
  # came out null and NOTHING in the payload said the read had failed, so the consumer saw
  # rc=null, decided it was not RUNNING and not NOT_RUN, and printed "failed ?" --
  # a read failure rendered as the definite conclusion "this task failed".
  # Say it instead. "I could not read this" and "this failed" are different answers.
  $i = $null
  $infoErr = $null
  try { $i = $t | Get-ScheduledTaskInfo -ErrorAction Stop }
  catch { $infoErr = $_.Exception.Message }
  $s = $t.Settings

  # Triggers are emitted BOTH as a human string and as structured fields. The string is for the
  # table; the structure is what lets the caller lay today's occurrences out on a 24 hour timeline.
  # A summarised string cannot be expanded back into times, and guessing from it would be inventing.
  $trg = @()
  $trgRaw = @()
  foreach ($x in $t.Triggers) {
    $kind = $x.CimClass.CimClassName -replace 'MSFT_Task', '' -replace 'Trigger', ''
    $rep = ''
    if ($x.Repetition -and $x.Repetition.Interval) { $rep = '/' + $x.Repetition.Interval }
    $at = ''
    if ($x.StartBoundary) { try { $at = ' @' + ([datetime]$x.StartBoundary).ToString('HH:mm') } catch { } }
    $trg += ("$kind$rep$at").Trim()

    $props = $x.CimInstanceProperties.Name
    $trgRaw += [ordered]@{
      kind     = $kind
      enabled  = [bool]$x.Enabled
      start    = if ($x.StartBoundary) { "$($x.StartBoundary)" } else { $null }
      end      = if ($x.EndBoundary) { "$($x.EndBoundary)" } else { $null }
      interval = if ($x.Repetition) { "$($x.Repetition.Interval)" } else { $null }
      duration = if ($x.Repetition) { "$($x.Repetition.Duration)" } else { $null }
      days     = if ($props -contains 'DaysInterval')  { [int]$x.DaysInterval }  else { $null }
      weeks    = if ($props -contains 'WeeksInterval') { [int]$x.WeeksInterval } else { $null }
      dow      = if ($props -contains 'DaysOfWeek')    { [int]$x.DaysOfWeek }    else { $null }
    }
  }

  $rows += [ordered]@{
    name        = $t.TaskName
    # The task's OWN description, used as the fallback when the category map has no override.
    # Whitespace is collapsed because these are often multi-line and would wreck a table cell.
    description = if ($t.Description) { ($t.Description -replace '\s+', ' ').Trim() } else { $null }
    state       = "$($t.State)"
    rcRaw       = if ($i) { [int64]$i.LastTaskResult } else { $null }
    rcHex       = if ($i) { '0x{0:X}' -f ($i.LastTaskResult -band 0xFFFFFFFF) } else { $null }
    lastRun     = if ($i -and $i.LastRunTime -gt (Get-Date '2000-01-01')) { $i.LastRunTime.ToString('yyyy-MM-dd HH:mm') } else { $null }
    nextRun     = if ($i -and $i.NextRunTime) { $i.NextRunTime.ToString('yyyy-MM-dd HH:mm') } else { $null }
    lastRunEpoch = if ($i -and $i.LastRunTime -gt (Get-Date '2000-01-01')) { ([DateTimeOffset]$i.LastRunTime).ToUnixTimeMilliseconds() / 1000.0 } else { $null }
    nextRunEpoch = if ($i -and $i.NextRunTime) { ([DateTimeOffset]$i.NextRunTime).ToUnixTimeMilliseconds() / 1000.0 } else { $null }
    # NumberOfMissedRuns is the scheduler's OWN count of runs it should have started and
    # did not. Nothing else on this machine can answer 'should have run but did not':
    # an exit code only exists for runs that happened.
    missedRuns  = if ($i) { [int]$i.NumberOfMissedRuns } else { $null }
    # Non-null means the info read failed and every field above is null for THAT reason,
    # not because the scheduler had nothing to report.
    infoError   = $infoErr
    triggers    = ($trg -join ', ')
    triggersRaw = $trgRaw
    exec        = $t.Actions[0].Execute
    args        = $t.Actions[0].Arguments
    catchup     = [bool]$s.StartWhenAvailable
    retries     = [int]$s.RestartCount
    timeout     = "$($s.ExecutionTimeLimit)"
    multi       = "$($s.MultipleInstances)"
    # Reported as the CIM property means it, not as the cmdlet's inverted switch name.
    # DisallowStartIfOnBatteries=True means "refuses to start on battery", which is the default.
    refuseOnBattery = [bool]$s.DisallowStartIfOnBatteries
    stopOnBattery   = [bool]$s.StopIfGoingOnBatteries
    runLevel    = "$($t.Principal.RunLevel)"
    userId      = "$($t.Principal.UserId)"
  }
}

$capture = [ordered]@{
  generated = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
  observed_at = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
  enumerated = $all.Count
  tasks = $rows
}

if ($ObservationBinding -or $CaptureOnly) {
  if (-not $CaptureOnly -and (-not $ObservationPython -or -not [IO.Path]::IsPathRooted($ObservationPython) -or
      -not [IO.Path]::IsPathRooted($ObservationBinding))) {
    throw 'collect: observation binding and interpreter must be explicit absolute paths'
  }
  # Read facts once in this existing collection pass. Never execute task actions
  # or commands from observations. Only matching evidence survives the adapter.
  $local = [ordered]@{ observed_at=$capture.observed_at; processes=@(); listeners=@();
                      process_query='unchecked'; listener_query='unchecked' }
  try {
    $local.processes = @(Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object {
      @{pid=[int]$_.ProcessId; executable=$_.ExecutablePath; command_line=$_.CommandLine}
    })
    $local.process_query = 'checked'
  } catch {
    # Image paths remain useful when CIM command-line access is denied. Rules
    # requiring arguments cannot match this partial evidence.
    try {
      $local.processes = @(Get-Process -ErrorAction Stop | ForEach-Object {
        @{pid=[int]$_.Id; executable=$_.Path; command_line=$null}
      })
      $local.process_query = 'checked'
      $local.command_line_query = 'query_failed'
    } catch { $local.process_query = 'query_failed' }
  }
  try {
    $local.listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | ForEach-Object {
      @{pid=[int]$_.OwningProcess; port=[int]$_.LocalPort; address=[string]$_.LocalAddress}
    })
    $local.listener_query = 'checked'
  } catch { $local.listener_query = 'query_failed' }
  $capture.local = $local
}

if ($ObservationBinding -and -not $CaptureOnly) {
  # ASCII JSON is lossless for UTF-16 surrogate pairs and U+FEFF payloads.
  # The installed Python entry owns bounded I/O, the worker Job and cleanup.
  $json = $capture | ConvertTo-Json -Depth 30 -Compress
  $json = [regex]::Replace($json, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value })
  if ($json.Length -gt 16777216) { throw 'collect: observation input_limit' }
  $previousEncoding = $OutputEncoding
  try {
    $OutputEncoding = New-Object System.Text.UTF8Encoding $false
    # Stderr may contain an interpreter import traceback; never relay its paths
    # or raw capture. Only the fixed receipt fields below cross this facade.
    $ErrorActionPreference = 'Continue'
    $result = @($json | & $ObservationPython -I -B -X utf8 -m task_console.observations `
      --binding $ObservationBinding --deadline-ms $observationDeadlineMs --receipt-stdout 2>$null)
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    try { $receipt = ($result -join "`n") | ConvertFrom-Json -ErrorAction Stop }
    catch { throw 'collect: observation reader unavailable or invalid receipt' }
    if ($code -ne 0) {
      $reason = 'collection_failed'
      if ($receipt.reason_code -in @('dependency_unavailable', 'worker_timeout', 'worker_output_limit',
          'worker_input_limit', 'input_limit', 'worker_cleanup_failed', 'worker_launch_failed',
          'worker_cancelled', 'worker_failed')) { $reason = $receipt.reason_code }
      throw "collect: observation reader failed ($reason)"
    }
    if ($receipt.schemaVersion -ne 1 -or $receipt.published -ne $true) {
      throw 'collect: invalid observation publication receipt'
    }
  } finally {
    $OutputEncoding = $previousEncoding
    $ErrorActionPreference = 'Stop'
    $capture.Remove('local')
  }
}
# Preserve the raw Scheduler stdout contract for existing server/ingest readers.
$capture | ConvertTo-Json -Depth 6 -Compress
