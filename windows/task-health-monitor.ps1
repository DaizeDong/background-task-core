<#
  task-health-monitor.ps1 — the unified local health layer for skill automation.

  The problem it solves: every skill owns a Windows scheduled task, and when one fails (one task
  was sitting at 0x1 for days) or silently stops running, NOTHING surfaces it — each task is an island. This is a
  single external observer that reads each configured task's LastTaskResult + LastRunTime from the Task
  Scheduler and routes anything unhealthy to Agent Center (infra), so all skill-automation health lands in
  one place. It also pushes its OWN heartbeat watchdog into schedule-reminder, so if the monitor itself
  dies, schedule-reminder's tick surfaces that too (who watches the watcher).

  Design choice: this is the "negative" observer (checks OS results from outside) and covers skills whose
  runners we do NOT want to edit. market-intel's own tasks use the "positive" pattern instead (the job
  self-pushes a heartbeat on a clean completion) and are omitted from the config to avoid double-coverage.

  Task health comes only from the installed background_task_core status reader.
  This script captures root Scheduler rows and declared artifact mtimes; the shared
  evaluator owns exit-code normalization, freshness, workload evidence and coverage.
  Per-task log lines retain the legacy history shape and include backend verdicts.

  FLEET DOC DEBT IS REPORTED HERE, AND IT IS NOT A TASK FAILURE. Until 2026-07-31 a RED fleet
  verdict made sync-config-to-backup.ps1 exit 1, which made SyncClaudeConfig's LastTaskResult 1,
  which made this monitor print "SyncClaudeConfig: FAILED 0x1 (config backup Layer 1)". The backup
  had succeeded; a SKILL.md was too long. Two signals with completely different urgency shared one
  wire, and the loud one was borrowed by the quiet one, which is how the one alarm that must never
  be ignored gets trained into background noise.
  The backup's exit code now means only "did the backup work". The fleet verdict is read HERE,
  directly from fleet_check.py's own status artifact, and reported as its own block: separate list,
  separate heading, wording that never says FAILED, and printed even when every task is healthy so
  it cannot be lost. Staleness of that artifact is itself reported, because a verdict nobody
  produced is not a green one.

  SILENCE IS NEVER A RESULT (2026-07-31). Three separate ways this script reported success by
  saying nothing, all now closed, all of them the same mistake in different clothes:
    a GREEN fleet rendered as zero lines and no fleet section, so healthy, crashed and broken all
      looked alike. Green is now stated, with its timestamp and its coverage.
    the fleet artifact was read against a 30 hour rolling window with no run-start test, so a ten
      hour old GREEN passed at 23:00 with the 22:00 producer having produced nothing. Freshness is
      now measured against the producer task's actual last run.
    the daily-digest marker was written before the relay result was looked at, so one failed push
      suppressed every retry for the rest of the calendar day and the log said "relayed". The
      marker is now a receipt for a confirmed delivery and nothing else.
  The rule these share: an operator can only audit something that is written down. Any state worth
  reporting has to produce a line, including the good one, so that missing output means the
  reporter is broken instead of meaning everything is fine.
#>
param(
  [string]$Python  = (Join-Path $env:ProgramData 'miniconda3\python.exe'),
  [string]$Config  = "",
  # Both default to the private launcher binding: the schedule-reminder scripts beside its
  # BACKGROUND_TASK_CORE_REMINDER_CLI, and the directory of its BACKGROUND_TASK_CORE_HISTORY log.
  [string]$SrDir   = "",
  [string]$LogDir  = "",
  # fleet-verdict.ps1 is shared with the owner's nightly config backup, so it stays with that
  # backup's scripts under CLAUDE_CONFIG_DIR rather than in this repository.
  [string]$FleetVerdictLib = $(if ($env:CLAUDE_CONFIG_DIR) { Join-Path $env:CLAUDE_CONFIG_DIR 'scripts\lib\fleet-verdict.ps1' } else { '' }),
  # fleet_check.py's own default location. It used to be redirected into the monitor's log directory, which
  # produced a second same-named artifact that any hand run of the tool left behind; see
  # $script:FleetStatusDefault in lib/fleet-verdict.ps1. Empty means "use the shared default",
  # which keeps the one canonical path defined in exactly one place.
  [string]$FleetStatus = "",
  [double]$FleetMaxAgeHours = 30,
  # WHICH RUN the fleet verdict is supposed to describe. The fleet check is skill-smith's own
  # SkillSmithFleetCheck task (declared in that skill's automation/tasks.json), so the honest
  # freshness question is "does the artifact cover the last time THAT task ran", not "is the
  # artifact younger than a rolling window". See the fleet block below for the ten-hour-old-GREEN
  # hole this closes.
  [string]$FleetProducerTask = "SkillSmithFleetCheck",
  # Used only if the producer task cannot be interrogated (not registered, never run, scheduler
  # unavailable). Local time of day, matching the producer task's trigger.
  [string]$FleetProducerDailyAt = "21:30",
  # A producer run that started less than this long ago may still be running, so demanding its
  # output yet would be a false alarm. Inside the window we fall back to demanding the PREVIOUS
  # run's artifact, which is still a real demand: we never fall back to demanding nothing.
  [double]$FleetProducerGraceHours = 3,
  # Optional installed evaluator interpreter; empty inherits the existing Python parameter.
  [string]$HealthPython = "",
  # Explicit child-only module path override, never inferred from a source checkout.
  [string]$HealthPythonPath = "",
  [string]$ObservationBinding = $env:BACKGROUND_TASK_CORE_OBSERVATION_BINDING,
  [string]$ObservationPython = $env:BACKGROUND_TASK_CORE_OBSERVATION_PYTHON,
  [string]$LauncherBindingFile = '',
  # Publish one passive snapshot and return before logs, reminders or notifications.
  [switch]$CollectionOnly
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot 'lib\task-health-observations.ps1')
if (-not $CollectionOnly) {
  . (Join-Path $PSScriptRoot 'background-task-core-binding.ps1')
  $launchInputs = Resolve-TaskHealthLaunchInputs -ObservationBinding $ObservationBinding `
    -ObservationPython $ObservationPython -HealthPython $HealthPython -LauncherBindingFile $LauncherBindingFile
  $ObservationBinding = $launchInputs.ObservationBinding
  $ObservationPython = $launchInputs.ObservationPython
  $HealthPython = $launchInputs.HealthPython
  # Reject an unconfigured monitor before collection, logs, models or delivery.
  # An invalid watch-list is not a task failure to send through the notification path.
  $cfg = Read-TaskHealthWatchList -Config $Config -LauncherBindingFile $LauncherBindingFile `
    -UseLauncherBinding:(-not $PSBoundParameters.ContainsKey('Config'))
}
$periodicReceipt = Invoke-TaskObservationCollection $ObservationBinding $ObservationPython
if ($CollectionOnly) {
  $periodicReceipt | ConvertTo-Json -Depth 5 -Compress
  if ($periodicReceipt.published) { exit 0 }
  exit 2
}

# Shared definition of the fleet verdict and of what a backup failure is. See its header for the
# SyncClaudeConfig 0x1 incident that forced the two apart.
if (-not $FleetVerdictLib -or -not (Test-Path -LiteralPath $FleetVerdictLib -PathType Leaf)) {
  throw 'fleet-verdict.ps1 not found; set CLAUDE_CONFIG_DIR or pass -FleetVerdictLib'
}
. $FleetVerdictLib

# UTF-8 native-command I/O (CRITICAL for the Chinese infra push). PS 5.1 in a detached scheduled-task
# session defaults [Console]::OutputEncoding to the system codepage (GBK/936) and decodes a child
# process's UTF-8 stdout as GBK, so '... | Out-String' turned llmcall's Chinese tidy into mojibake
# (the exact garbled infra message that shipped). Pin both directions to UTF-8 so captured Chinese
# round-trips intact (verified under the windowless wscript task). Mirrors sync-config-to-backup.ps1.
try {
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [Console]::OutputEncoding = $utf8NoBom   # how PS decodes a child process's stdout (the mojibake fix)
  $OutputEncoding           = $utf8NoBom   # how PS encodes text piped to a child's stdin
  try { [Console]::InputEncoding = $utf8NoBom } catch {}   # detached session may lack a stdin handle
  $env:PYTHONIOENCODING = 'utf-8'
} catch { }

if (-not $SrDir -or -not $LogDir) {
  $launcherEnvironment = (Read-BackgroundTaskCoreLauncherBinding -BindingFile $LauncherBindingFile).Environment
  if (-not $SrDir) {
    if (-not $launcherEnvironment['BACKGROUND_TASK_CORE_REMINDER_CLI']) { throw 'Launcher binding names no BACKGROUND_TASK_CORE_REMINDER_CLI; pass -SrDir' }
    $SrDir = Split-Path -Parent $launcherEnvironment['BACKGROUND_TASK_CORE_REMINDER_CLI']
  }
  if (-not $LogDir) {
    if (-not $launcherEnvironment['BACKGROUND_TASK_CORE_HISTORY']) { throw 'Launcher binding names no BACKGROUND_TASK_CORE_HISTORY; pass -LogDir' }
    $LogDir = Split-Path -Parent $launcherEnvironment['BACKGROUND_TASK_CORE_HISTORY']
  }
}
$reminder = Join-Path $SrDir "reminder.py"
$relay    = Join-Path $SrDir "relay.py"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$log = Join-Path $LogDir "task-health-monitor.log"
# Size rotation: past 5 MB the current log becomes .1 (replacing the previous .1), so the pair stays under 10 MB.
if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 5MB) { Move-Item -LiteralPath $log -Destination "$log.1" -Force }
function Log($m) { $line = "[{0:yyyy-MM-dd HH:mm:ss}] {1}" -f (Get-Date), $m; Add-Content -Path $log -Value $line -Encoding UTF8; $line }

$script:deliveryFailed = $false
$script:healthReadFailed = $false
Log "=========== task-health monitor start ==========="
if ($periodicReceipt) {
  Log ('periodic observations: ' + ($periodicReceipt | ConvertTo-Json -Depth 5 -Compress))
}
$stream = if ($cfg -and $cfg.stream) { $cfg.stream } else { "infra" }

# Enumerate the literal root only. Same-name tasks in other folders never satisfy
# a declaration. A failed or ambiguous enumeration remains an observation error.
$script:TaskIndex = @{}
$schedulerError = $null
try {
  foreach ($task in @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop)) {
    if (-not $task -or $task.TaskPath -cne '\' -or -not $task.TaskName) { continue }
    if ($script:TaskIndex.ContainsKey($task.TaskName)) { throw 'Ambiguous root task identity' }
    $script:TaskIndex[$task.TaskName] = $task
  }
} catch { $schedulerError = $_.Exception.GetType().Name }
function Get-IndexedTask {
  param([string]$Name)
  if (-not $schedulerError -and $script:TaskIndex.ContainsKey($Name)) { return $script:TaskIndex[$Name] }
  return $null
}

function Get-ExpectedFleetRunStart {
  param([string]$TaskName, [string]$DailyAt, [double]$GraceHours)
  $now = Get-Date
  $start = $null
  try {
    $pt = Get-IndexedTask $TaskName
    if ($pt) {
      $lr = ($pt | Get-ScheduledTaskInfo).LastRunTime
      # Task Scheduler reports 1999-11-30 for "never ran"; that is not a run to measure against.
      if ($lr -and $lr.Year -gt 2000 -and $lr -le $now) { $start = $lr }
    }
  } catch { $start = $null }
  if (-not $start) {
    $hm = @($DailyAt -split ':')
    $t = $now.Date.AddHours([int]$hm[0]).AddMinutes([int]($(if ($hm.Count -gt 1) { $hm[1] } else { 0 })))
    if ($t -gt $now) { $t = $t.AddDays(-1) }
    $start = $t
  }
  if (($now - $start).TotalHours -lt $GraceHours) { $start = $start.AddDays(-1) }
  return $start
}

$unhealthy = @()
$neutral = @()
$healthy = @()
$noted = @()
$healthSnapshot = $null
$healthResult = $null
try {
  $healthSnapshot = Get-TaskHealthSnapshot $cfg.tasks $script:TaskIndex $schedulerError
  $readerPython = if ($HealthPython) { $HealthPython } else { $Python }
  $healthResult = Invoke-TaskHealthEvaluator $healthSnapshot $readerPython $HealthPythonPath (ConvertTo-TaskHealthEpoch (Get-Date))
  # JSON retains every check and the exact backend verdict/coverage/reason_codes.
  Log ('component health: ' + ($healthResult | ConvertTo-Json -Depth 60 -Compress))
  foreach ($task in $healthResult.tasks) {
    # Compatibility labels only: no thresholds or return-code interpretation here.
    $prefix = switch ($task.state) {
      'up' { 'OK' }; 'down' { 'FAILED' }; 'never' { 'FAILED' }
      'grace' { 'STALE' }; 'paused' { 'NEUTRAL' }; 'running' { 'NEUTRAL' }
      default { 'UNKNOWN' }
    }
    $text = '{0} (state={1}; verdict={2}; coverage={3}/{4} {5}; reason_codes={6}; check={7})' -f `
      $prefix, $task.state, $task.verdict, $task.coverage.checked, $task.coverage.expected, `
      $task.coverage.state, ($task.reason_codes -join ','), $task.check
    if ($null -ne $task.last_run) {
      $last = [DateTimeOffset]::FromUnixTimeMilliseconds([long]($task.last_run * 1000))
      $text += ' last ' + $last.LocalDateTime.ToString('M/d/yyyy h:mm:ss tt', [Globalization.CultureInfo]::InvariantCulture)
    }
    Log "  $($task.name) : $text"
    $line = "$($task.name): $text"
    if ($healthResult.summary.attentionStates -contains $task.state) { $unhealthy += $line }
    elseif ($task.verdict -eq 'healthy') { $healthy += $line }
    elseif ($task.verdict -eq 'degraded') { $noted += $line }
    else { $neutral += $line }
  }
  if ($healthResult.coverage.state -eq 'zero') {
    $unhealthy += '[monitor]: UNKNOWN (coverage=0/0 zero; no declared tasks evaluated)'
    Log 'coverage: zero (0/0); no declared tasks evaluated'
  }
} catch {
  $script:healthReadFailed = $true
  $unhealthy += '[monitor]: UNKNOWN (reader_failed; component health unavailable)'
  Log "component health reader_failed: $($_.Exception.Message)"
}

# ---- fleet doc debt: a SEPARATE signal, read from its own artifact, never a task result ----------
# This block deliberately does not touch $unhealthy. A task in $unhealthy means a scheduled job on
# this machine is failing or has gone silent, which is an operational emergency. Fleet doc debt is a
# chore. Merging them is the exact defect this section exists to undo, and the merge is easy to make
# by accident, so the two lists stay physically separate all the way to the push.
#
# TWO THINGS THIS BLOCK USED TO GET WRONG, BOTH FIXED 2026-07-31, BOTH OF THE HOUSE STYLE (the
# output looked entirely normal while being untrue):
#
#   1. A ROLLING AGE WINDOW CANNOT ASK WHETHER THE RUN HAPPENED. This read the artifact with
#      MaxAgeHours 30 and deliberately passed no run-start test, on the reasoning that the monitor
#      did not run the check so it had no run to measure against. That reasoning is wrong: it did
#      not run the check, but it knows exactly which run was SUPPOSED to produce it, because that
#      run is a scheduled task on this machine with a LastRunTime. Measured consequence: at 23:00,
#      with the 22:00 nightly having produced nothing at all, a ten hour old GREEN was reported as
#      a current GREEN. Thirty hours of slack is a whole missed night. The monitor now demands the
#      artifact cover the producer's last run, and a stale one renders as UNKNOWN rather than as
#      its last known verdict.
#
#   2. GREEN SAID NOTHING. Format-FleetVerdictBlock returned zero lines for a fresh GREEN unless a
#      caller opted in, and this caller did not, so a healthy fleet produced no fleet section and
#      was indistinguishable from a reader that had crashed. Green is now rendered by default and
#      the -OmitWhenGreen opt-out is deliberately not used here.
#
$fleetLines = @()
try {
  $fsPath = if ($FleetStatus) { $FleetStatus } else { Resolve-FleetStatusPath }
  $expected = Get-ExpectedFleetRunStart -TaskName $FleetProducerTask -DailyAt $FleetProducerDailyAt -GraceHours $FleetProducerGraceHours
  $label = "the {0} run of {1} (which is what produces the fleet verdict)" -f ("{0:yyyy-MM-dd HH:mm}" -f $expected), $FleetProducerTask
  $fv = Get-FleetVerdict -StatusPath $fsPath -MaxAgeHours $FleetMaxAgeHours -NotOlderThan $expected -FreshnessLabel $label
  $fleetLines = @(Format-FleetVerdictBlock -Verdict $fv)
  Log ("fleet verdict: {0} (must cover {1:yyyy-MM-dd HH:mm} run of {2}){3}" -f $fv.State, $expected, $FleetProducerTask, $(if ($fv.Digest) { " | $($fv.Digest)" } else { "" }))
} catch {
  # Never let this take the monitor down: the task verdicts above are the load-bearing output.
  $fleetLines = @("[fleet] FLEET DOC DEBT: UNKNOWN, the reader itself failed: $($_.Exception.Message)",
                  "  This is a documentation and conformance chore. It is NOT a backup problem.")
  Log "fleet verdict read FAILED: $($_.Exception.Message)"
}

# ---- TASK CONSOLE INGEST (2026-09-02) --------------------------------------
# Pull the Windows Operational event log and this log into the console's database.
#
# WHY IT RUNS HERE, and why it is not optional. That event log is a CIRCULAR buffer: measured 635
# events an hour against a 64 MB cap, so roughly FIVE DAYS before the oldest records are overwritten.
# Anything not ingested before then is gone and cannot be recovered from anywhere. Reading it also
# costs about 109 seconds, which is why it must not sit on a web request; this hourly pass is the
# right place to pay it.
#
# Failure is reported, never fatal: the per-task verdicts above are the load-bearing output and an
# ingest problem must not take them down with it.
$ingestLines = @()
try {
  # The ingester is the installed generation's `python -m background_task_core ingest`, the same interpreter
  # as the health reader; the database path comes from the launcher binding.
  $consoleDb = (Read-BackgroundTaskCoreLauncherBinding -BindingFile $LauncherBindingFile).Environment['BACKGROUND_TASK_CORE_DB']
  if (-not $HealthPython -or -not $consoleDb) {
    $ingestLines = @("[console] ingester unavailable: no installed interpreter or no BACKGROUND_TASK_CORE_DB; run history stops accumulating")
    Log "console ingest: unavailable"
  } else {
    $env:BACKGROUND_TASK_CORE_HISTORY = $log
    $env:BACKGROUND_TASK_CORE_DB = $consoleDb
    # --skip-runlog: THE HOURLY PASS DOES NOT BACKFILL SIXTY DAYS OF RUN HISTORY.
    #
    # Without it this task was killed by its own ExecutionTimeLimit on EVERY run for 73 consecutive
    # hours, exit code 267014. The health sweep itself finishes in 39 seconds; the remaining ~900
    # went into this one call. And the nesting could never have worked: console_ingest passes
    # timeout=900 to runlog.ps1 while the task's own limit is 600, so the inner budget was
    # unreachable and the outer guillotine fired first, every time.
    #
    # The slow part is not the script. Reading this machine's TaskScheduler event log is ~50x slower
    # than its other logs (500 events: System 654ms, Application 360ms, TaskScheduler 31,979ms), and
    # no query shape avoids it -- FilterHashtable, FilterXPath and an unfiltered read all take the
    # same six minutes. That is a separate machine-level problem, tracked on its own.
    #
    # Measured after this change: 6 seconds. The run history is backfilled by ConsoleIngestFull
    # instead, which runs daily and is not racing an hourly limit.
    $iout = & $HealthPython -I -B -X utf8 -m background_task_core ingest --skip-runlog 2>&1 | Out-String
    $irc = $LASTEXITCODE
    if ($irc -eq 0) { Log "console ingest: ok" }
    else {
      $tail = ($iout -split "`r?`n" | Where-Object { $_ -match 'FAIL|Traceback|Error' } | Select-Object -First 3) -join ' | '
      $ingestLines = @("[console] ingest rc=$irc $tail")
      Log "console ingest: rc=$irc"
    }
  }
} catch {
  $ingestLines = @("[console] the ingester itself failed: $($_.Exception.Message)")
  Log "console ingest FAILED: $($_.Exception.Message)"
}

# ---- TASK REGISTRY CONFORMANCE (2026-09-01) --------------------------------
# Runs task-registry-check.ps1, which asserts the task-creation spec:
#   A1 every live self-installed task appears in all three registries (live scheduler,
#      backup allow-list, this watch list). A task missing from the allow-list is lost
#      silently on a machine rebuild; one missing from here dies with nobody noticing.
#   A2 a .log declared as artifact must carry an explicit declaration, because a log that
#      is also written on crash paths turns this monitor's "OK by artifact" branch into a
#      permanent green light. RefreshMarketIntel is the measured case.
#   A3 launchers are not UTF-8 BOM (wscript needs UTF-16LE or ASCII).
#   A4 the concurrency gate's self-test passes, negative control included.
#
# SEPARATE BLOCK ON PURPOSE, not folded into $unhealthy. "This task is broken" and "this
# task was never registered anywhere" have different urgencies, and this file already
# carries the lesson of what happens when two signals share one wire: the loud one gets
# borrowed by the quiet one until the alarm that must never be ignored becomes background.
# Same reason the fleet-doc-debt verdict got its own heading above.
$registryLines = @()
try {
  $rcheck = Join-Path $PSScriptRoot "task-registry-check.ps1"
  if (-not (Test-Path -LiteralPath $rcheck)) {
    $registryLines = @("[registry] checker MISSING at $rcheck; the spec is unenforced this run")
    Log "registry check: script missing at $rcheck"
  } else {
    $psExe = (Get-Process -Id $PID).Path
    $rout = & $psExe -NoProfile -ExecutionPolicy Bypass -File $rcheck 2>&1 | Out-String
    $rrc  = $LASTEXITCODE
    if ($rrc -eq 0) {
      Log "registry check: PASS"
    } elseif ($rrc -eq 2) {
      # The checker refused to assert anything. That is not a pass and must not read like one.
      $registryLines = @("[registry] the checker could not run its assertions (rc=2). This is NOT a pass.")
      Log "registry check: rc=2, checker could not assert"
    } else {
      $bad = @($rout -split "`r?`n" | Where-Object { $_ -match '\[FAIL\]' } | ForEach-Object { $_.Trim() })
      if ($bad.Count -eq 0) { $bad = @("(rc=$rrc but no FAIL line parsed; read the checker output directly)") }
      $registryLines = @("[registry] $($bad.Count) task-creation-spec violation(s)") + ($bad | ForEach-Object { "  $_" })
      Log "registry check: rc=$rrc, $($bad.Count) violation(s)"
    }
  }
} catch {
  # Never take the monitor down over this: the per-task verdicts above are the load-bearing output.
  $registryLines = @("[registry] the conformance checker itself failed: $($_.Exception.Message)")
  Log "registry check FAILED: $($_.Exception.Message)"
}

# ---- relay unhealthy to Agent Center (silence when all-clear; the watchdog proves the run happened) ----
# Returns the child's combined output AND records its exit code in $script:PyExit. The exit code is
# not decoration: relay.py's send returns 0 only when the webhook (or the Big Brother fallback)
# actually accepted the message, and returns 1 when it did not. A caller that ignores it cannot
# tell a delivered alert from a dropped one. See the delivery gate below for what ignoring it cost.
$script:PyExit = $null
function PyCall($argList) {
  $ErrorActionPreference = "Continue"
  $out = & $Python @argList 2>&1 | Out-String
  $script:PyExit = $LASTEXITCODE
  $ErrorActionPreference = "Stop"
  return $out
}
# The push fires when EITHER list has something to say. Gating it on $unhealthy alone would have
# meant a RED fleet on an otherwise healthy machine produced no push at all, which is precisely the
# hole that made borrowing the backup's exit code look necessary in the first place.
if ($unhealthy.Count -gt 0 -or $fleetLines.Count -gt 0 -or $registryLines.Count -gt 0 -or $ingestLines.Count -gt 0) {
  $body = "🩺 [task-health] $($unhealthy.Count) unhealthy / $($healthy.Count) ok / $($noted.Count) noted / $($neutral.Count) pending"
  if ($unhealthy.Count -gt 0) {
    $body += "`n" + (($unhealthy | ForEach-Object { "  * $_" }) -join "`n")
  } else {
    $body += "`n  (every monitored scheduled task is healthy)"
  }
  if ($fleetLines.Count -gt 0) {
    # Own heading, own block, below the task list and visibly not part of it.
    $body += "`n`n📋 [fleet-doc-debt] a chore, not a task failure`n" + (($fleetLines | ForEach-Object { "  $_" }) -join "`n")
  }
  if ($ingestLines.Count -gt 0) {
    $body += "`n`n💾 [background-task-core] 摄入问题,不是任务失败`n" + (($ingestLines | ForEach-Object { "  $_" }) -join "`n")
  }
  if ($registryLines.Count -gt 0) {
    # Own heading for the same reason as the block above: a registration gap is not a task failure,
    # and merging the two counts would make the number that means "go look" mean two things.
    $body += "`n`n🗂 [task-registry] spec conformance, not a task failure`n" + (($registryLines | ForEach-Object { "  $_" }) -join "`n")
  }

  # DAILY DIGEST GATE: the hourly cadence is for FAST DETECTION (watchdog heartbeat below still fires
  # every run), but a Discord push every hour on a standing failure is noise. Push the user-facing
  # alert at most once per calendar day; a date-marker file records the last push day. The log still
  # records the full verdict every hour, so nothing is lost -- only the phone ping is rate-limited.
  $marker = Join-Path $LogDir ".task-health-last-alert"
  $today  = "{0:yyyy-MM-dd}" -f (Get-Date)
  # The marker used to hold ONLY the date, which made the gate "one push per day" rather
  # than "one push per distinct failure per day". The first push of a day claimed the only
  # slot and everything after it was suppressed as a duplicate, including runs where a
  # DIFFERENT task had started failing. Measured 2026-08-19: 25 runs, 1 push, 24 suppressed;
  # the single push carried a standing false positive, while a real DemandMiningDaemon
  # failure that appeared at 03:00 never reached the phone at all.
  #
  # The key is now "<date>|<names already alerted today>". A repeat of the same failure set
  # still stays quiet, which is the whole point of the gate, but a newly failing task is a
  # different set and gets through. A stale date-only marker parses to an empty name set,
  # so it self-heals on the next push with no migration.
  $rawMarker = if (Test-Path $marker) { (Get-Content $marker -Raw).Trim() } else { "" }
  $mParts       = $rawMarker -split '\|', 2
  $lastAlertDay = $mParts[0]
  # [string[]] is load bearing. PowerShell unrolls a one-element array on assignment, so
  # after a day whose push carried exactly one task this would become a plain String, and
  # the union below would then be STRING CONCATENATION rather than an array merge: the two
  # names fused into one token, that token matched nothing on the next run, and the gate
  # would have re-alerted forever. The regression test caught this; the bug is invisible
  # until a second distinct task fails on the same day, which is precisely the case this
  # whole change exists to handle.
  [string[]]$alreadyNames = if ($lastAlertDay -eq $today -and $mParts.Count -gt 1 -and $mParts[1]) {
    @($mParts[1] -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  } else { @() }
  # Each unhealthy line starts with the task name followed by a colon.
  $sigNow  = @($unhealthy | ForEach-Object { ($_ -split ':')[0].Trim() } | Sort-Object -Unique)
  $newOnes = @($sigNow | Where-Object { $alreadyNames -notcontains $_ })

  # ---- escalation: a failure that does not GO AWAY has to speak up again ---------------------------
  # The gate above answers "is this the same failure set as the last push", and for noise that is the
  # right question. It is the wrong question for duration. On 2026-09-08 ccModelRefresh failed every
  # hour from 10:35 to 16:35; the monitor judged it red correctly all six times and the phone heard
  # about it exactly once, because after the first push it was never a NEW name again. Six hours of a
  # broken job and one ping that looked like any other morning's.
  #
  # So a second axis, tracked in its own file so the dedup marker and its regression test are
  # untouched: how long has each name been failing continuously. Cross the window and it is pushed
  # again, once per window, labelled with the duration. A name that recovers is dropped, so the clock
  # restarts on the next distinct outage rather than counting yesterday's.
  $ESCALATE_AFTER_HOURS = 4
  $durFile = Join-Path $LogDir ".task-health-failing-since"
  $since = @{}
  if (Test-Path $durFile) {
    foreach ($ln in (Get-Content -LiteralPath $durFile)) {
      $p = $ln -split '\|', 3
      if ($p.Count -ge 2 -and $p[0]) { $since[$p[0]] = @{ first = $p[1]; escalated = if ($p.Count -ge 3) { $p[2] } else { '' } } }
    }
  }
  $nowIso = (Get-Date).ToString('o')
  $escalations = New-Object System.Collections.Generic.List[string]
  $nextSince = @{}
  foreach ($n in $sigNow) {
    $firstIso = if ($since.ContainsKey($n)) { $since[$n].first } else { $nowIso }
    $lastEsc  = if ($since.ContainsKey($n)) { $since[$n].escalated } else { '' }
    $hours = ([datetime]$nowIso - [datetime]$firstIso).TotalHours
    $sinceEsc = if ($lastEsc) { ([datetime]$nowIso - [datetime]$lastEsc).TotalHours } else { [double]::PositiveInfinity }
    if ($hours -ge $ESCALATE_AFTER_HOURS -and $sinceEsc -ge $ESCALATE_AFTER_HOURS) {
      $escalations.Add(("{0} (failing {1:N0}h)" -f $n, $hours))
      $lastEsc = $nowIso
    }
    $nextSince[$n] = @{ first = $firstIso; escalated = $lastEsc }
  }
  # Only names failing RIGHT NOW are carried forward; a recovered task forgets its clock.
  Set-Content -LiteralPath $durFile -Encoding UTF8 -Value (
    @($nextSince.Keys | ForEach-Object { "{0}|{1}|{2}" -f $_, $nextSince[$_].first, $nextSince[$_].escalated })
  )
  if ($escalations.Count) { Log ("escalating still-failing task(s): " + ($escalations -join ', ')) }

  if ($lastAlertDay -eq $today -and $newOnes.Count -eq 0 -and $escalations.Count -eq 0) {
    Log "unhealthy=$($unhealthy.Count), fleet-lines=$($fleetLines.Count), same failure set as today's push ($($sigNow -join ',')); suppressing duplicate"
  } else {
    # CHINESE TIDY: run the raw English verdict through llmcall (its own chain) to translate and
    # format it into a clean Chinese phone push. Model and effort are llmcall's policy, the same
    # strongest tier every background call gets: the old --effort low exception was withdrawn on
    # 2026-10-07 (owner decision D5 of the llmcall consolidation). The budget is 420 s, not 180 s:
    # at effort low the ledger showed p50 31 s, p90 46 s and a 97 s maximum with 7 of 101 calls
    # timing out at 180 s, and the strongest tier is slower. The task's limit is 1800 s because the
    # periodic observation pass alone takes about nine minutes; with the old 600 s limit every run
    # ended as 0x41306 although its work finished. The call stays fail-OPEN: any
    # error, timeout or empty result ships the original English body, so an alert is never lost to
    # a slow or flaky translator. PYTHONIOENCODING=utf-8 works around llmcall's GBK-stdout crash on
    # Windows.
    $pushBody = $body
    # Say WHY this one is being sent when it is a duration alert, or it reads as a repeat of a ping
    # the operator already dismissed, which is exactly how a six hour outage stayed quiet.
    if ($escalations.Count) {
      $pushBody = ("STILL FAILING after " + $ESCALATE_AFTER_HOURS + "h: " + ($escalations -join '; ') +
                   "`n(this is a duration alert, not a new failure)`n`n" + $body)
    }
    try {
      $prompt = "你是运维助手。下面是 Windows 计划任务健康检查的英文原始报告，请整理成简洁的中文推送消息。" +
                "要求：保留任务名（英文原样）、状态码、时间等关键信息；用中文说明每条含义；简明扼要，适合手机推送；" +
                "不要加任何前言/免责声明/代码块，直接给消息正文。`n`n原始报告：`n" + $body
      $tmpIn = Join-Path $env:TEMP ("th-prompt-{0}.txt" -f ([guid]::NewGuid().ToString('N')))
      [System.IO.File]::WriteAllText($tmpIn, $prompt, (New-Object System.Text.UTF8Encoding($false)))
      $prevEnc = $env:PYTHONIOENCODING
      $env:PYTHONIOENCODING = "utf-8"
      # Read the prompt file with an EXPLICIT UTF-8 decode: Get-Content -Raw would read this UTF-8-no-BOM
      # file using PS 5.1's ANSI/GBK default and corrupt the Chinese instructions before they reach llmcall.
      # The output capture is made safe by the [Console]::OutputEncoding=UTF8 set at the top of the script.
      $promptText = [System.IO.File]::ReadAllText($tmpIn, (New-Object System.Text.UTF8Encoding($false)))
      $cn = $promptText | & $Python "-m" "llmcall" "--mode" "judge" "--timeout" "420" 2>$null | Out-String
      $env:PYTHONIOENCODING = $prevEnc
      Remove-Item $tmpIn -ErrorAction SilentlyContinue
      if ($cn -and $cn.Trim().Length -gt 0) { $pushBody = $cn.Trim(); Log ("llmcall tidied alert to Chinese; sample: " + $pushBody.Substring(0, [Math]::Min(80, $pushBody.Length))) }
      else { Log "llmcall returned empty; falling back to English body" }
    } catch {
      Log "llmcall tidy failed ($($_.Exception.Message)); falling back to English body"
    }

    # PowerShell 5.1 mangles non-ASCII argv (emoji / CJK -> mojibake); base64 the UTF-8 body and use
    # relay.py --text-b64 so the emoji and any non-ASCII text survive intact.
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($pushBody))
    $r = PyCall @($relay, "send", "--stream", $stream, "--text-b64", $b64)
    $relayRc = $script:PyExit

    # THE MARKER IS A RECEIPT, NOT AN INTENTION. Until 2026-07-31 these three lines ran in the
    # order: send, write the marker unconditionally, log "relayed daily digest". The send's result
    # was never looked at. So a webhook outage produced a log line claiming delivery and a marker
    # claiming today was already handled, and because the marker gates one push per calendar day,
    # every remaining hourly run that day suppressed itself as a duplicate of a push that never
    # happened. Net effect of a transient network failure at the wrong minute: the operator gets
    # nothing for 24 hours and the log says they were told. That is the worst shape a monitoring
    # bug can take, because the silence it produces is the same silence that means all is well.
    #
    # Now: the marker is written ONLY on a confirmed 0 from relay.py, so a failure leaves the day
    # unmarked and the next hourly run retries. And a failure is not merely "not recorded as
    # success", it is recorded AS A FAILURE in three durable places that do not share a transport
    # with the thing that just failed:
    #   1. this log, in words that are greppable and do not say "relayed";
    #   2. a file under logs\undelivered\ holding the exact body that did not go out, so nothing
    #      the digest was trying to say is lost even if every retry also fails;
    #   3. a schedule-reminder item, which reaches the operator through the reminder tick rather
    #      than through the Discord webhook that is the suspect here.
    if ($relayRc -eq 0) {
      # Record the UNION of what had already been alerted today and what this push carried,
      # so a later run only breaks through for a name neither of them covered. Still written
      # only on a confirmed rc=0: the marker stays a delivery receipt, never an intent.
      $markerNames = @(@($alreadyNames) + @($sigNow) | Sort-Object -Unique) -join ','
      Set-Content -Path $marker -Value ("{0}|{1}" -f $today, $markerNames) -Encoding ASCII
      Log "relayed daily digest to $stream (relay rc=0, unhealthy=$($unhealthy.Count), new=$($newOnes -join ','), fleet-doc-debt lines=$($fleetLines.Count))"
    } else {
      $undelDir = Join-Path $LogDir "undelivered"
      if (-not (Test-Path $undelDir)) { New-Item -ItemType Directory -Path $undelDir -Force | Out-Null }
      $undelPath = Join-Path $undelDir ("task-health-{0:yyyy-MM-dd-HH-mm-ss}.txt" -f (Get-Date))
      $undelText = ("UNDELIVERED task-health digest`r`nattempted {0:yyyy-MM-dd HH:mm:ss}`r`nstream: {1}`r`nrelay.py exit code: {2}`r`nrelay output:`r`n{3}`r`n---- body that did not go out ----`r`n{4}`r`n" -f (Get-Date), $stream, $relayRc, $r, $pushBody)
      try { [System.IO.File]::WriteAllText($undelPath, $undelText, (New-Object System.Text.UTF8Encoding($false))) } catch { Log "WARN: could not persist undelivered digest: $($_.Exception.Message)" }
      Log "DELIVERY FAILED: relay.py exited $relayRc, the digest did NOT reach '$stream'. Marker NOT written, so the next hourly run retries. Body saved to $undelPath"
      $script:deliveryFailed = $true
      try {
        $due = (Get-Date).ToUniversalTime().AddHours(1).ToString("yyyy-MM-ddTHH:mm:ssZ")
        $rem = PyCall @($reminder, "--actor", "task-health", "add",
          "--title", "task-health digest UNDELIVERED (relay rc=$relayRc); body in logs\undelivered", "--kind", "task",
          "--due-at", $due, "--priority", "1", "--source", "task-health", "--tags", "task-health,delivery-failure",
          "--idempotency-key", "task-health:delivery-failure")
        if ($rem -match '"ok":\s*true') { Log "delivery-failure reminder pushed (due $due)" } else { Log "WARN: delivery-failure reminder add did not report ok:true" }
      } catch { Log "WARN: delivery-failure reminder push threw: $($_.Exception.Message)" }
    }
  }
} else {
  # Now effectively unreachable: the fleet block always produces lines (green included), so the
  # push condition is always satisfied and the daily digest goes out every day whether the news is
  # good or bad. That is the point. Reaching here means the fleet renderer returned nothing, which
  # is itself the defect this file spent 2026-07-31 removing, so it is logged as a defect and not
  # as an all-clear.
  Log "BUG: nothing to push. unhealthy=0 AND the fleet block rendered 0 lines; the fleet renderer is supposed to speak on green. Treat this as the reporting chain being broken, not as a healthy night."
}

# ---- push the monitor's own heartbeat watchdog (add = upsert on the stable key) ----
$due = (Get-Date).ToUniversalTime().AddHours(30).ToString("yyyy-MM-ddTHH:mm:ssZ")
$wd = PyCall @($reminder, "--actor", "task-health", "add",
  "--title", "task-health monitor (auto-heartbeat)", "--kind", "task", "--due-at", $due,
  "--priority", "3", "--source", "task-health", "--tags", "task-health,watchdog",
  "--idempotency-key", "task-health:monitor-watchdog")
if ($wd -match '"ok":\s*true') { Log "monitor watchdog pushed to $due" } else { Log "WARN: monitor watchdog add did not report ok:true" }

Log "=========== task-health monitor end (unhealthy=$($unhealthy.Count), fleet-doc-debt lines=$($fleetLines.Count), digest delivered=$(if ($script:deliveryFailed) { 'NO' } else { 'yes-or-not-due' })) ==========="
# Task findings are reported without failing the observer. A failed collection,
# health reader or required delivery is a failure of the observer itself.
if ($script:deliveryFailed -or $script:healthReadFailed -or -not $periodicReceipt.published) { exit 1 }
exit 0
