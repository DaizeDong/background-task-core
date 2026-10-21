# Passive facts for the background_task_core status reader. No health policy lives here.
function Read-TaskHealthWatchList {
  param(
    [string]$Config,
    [switch]$UseLauncherBinding,
    [string]$LauncherBindingFile = ''
  )
  if ($UseLauncherBinding) {
    $launcher = Read-BackgroundTaskCoreLauncherBinding -BindingFile $LauncherBindingFile
    $Config = $launcher.Environment['BACKGROUND_TASK_CORE_HEALTH']
  }
  Assert-BackgroundTaskCoreAbsolutePath $Config
  if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) {
    throw 'Task-health watch-list is missing'
  }
  try {
    $configuration = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  } catch { throw 'Task-health watch-list is not valid JSON' }
  if ($configuration -isnot [pscustomobject] -or $configuration.tasks -isnot [array] -or $configuration.tasks.Count -eq 0) {
    throw 'Task-health watch-list must declare a nonempty tasks array'
  }
  return $configuration
}

function Resolve-TaskHealthLaunchInputs {
  param(
    [string]$ObservationBinding,
    [string]$ObservationPython,
    [string]$HealthPython,
    [string]$LauncherBindingFile = ''
  )
  # The unchanged hidden launcher supplies no interpreter arguments. Resolve its
  # inputs through the same private declaration and receipt verifier as the console.
  # Complete explicit inputs remain useful for reviewed deployments and diagnostics.
  $configuration = $null
  if (-not ($ObservationBinding -and $ObservationPython -and $HealthPython)) {
    $configuration = Read-BackgroundTaskCoreLauncherBinding -BindingFile $LauncherBindingFile
    $snapshotPath = $configuration.Environment['BACKGROUND_TASK_CORE_STATUS_SNAPSHOT']
    Assert-BackgroundTaskCoreAbsolutePath $snapshotPath
    if (-not $ObservationBinding) {
      $ObservationBinding = Join-Path (Split-Path -Parent $snapshotPath) 'collector-binding.json'
    }
  }
  Assert-BackgroundTaskCoreAbsolutePath $ObservationBinding
  if (-not (Test-Path -LiteralPath $ObservationBinding -PathType Leaf)) {
    throw 'Task-health observation binding is missing'
  }
  try {
    $collector = Get-Content -LiteralPath $ObservationBinding -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
  } catch { throw 'Task-health observation binding is not valid JSON' }
  if ($collector -isnot [pscustomobject] -or $collector.schemaVersion -isnot [int] -or
      $collector.schemaVersion -ne 1 -or $collector.runtime -isnot [pscustomobject]) {
    throw 'Task-health observation binding requires schemaVersion 1 and a runtime object'
  }
  $runtimeNames = @{
    runtime_config = 'RuntimeConfig'; private_root = 'PrivateRoot'
    state_root = 'StateRoot'; vault_root = 'VaultRoot'
  }
  foreach ($key in $runtimeNames.Keys) {
    $value = $collector.runtime.$key
    Assert-BackgroundTaskCoreAbsolutePath $value
    if ($configuration) {
      $expected = $configuration.Runtime[$runtimeNames[$key]]
      Assert-BackgroundTaskCoreAbsolutePath $expected
      if (-not [string]::Equals([IO.Path]::GetFullPath($value).TrimEnd('\','/'),
          [IO.Path]::GetFullPath($expected).TrimEnd('\','/'), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Task-health observation runtime does not match the launcher: $key"
      }
    }
  }
  Assert-BackgroundTaskCoreAbsolutePath $collector.snapshot
  if ($configuration) {
    if (-not [string]::Equals([IO.Path]::GetFullPath($collector.snapshot),
        [IO.Path]::GetFullPath($snapshotPath), [StringComparison]::OrdinalIgnoreCase)) {
      throw 'Task-health observation snapshot does not match the launcher'
    }
    $runtimeArguments = $configuration.Runtime
    $resolved = Resolve-BackgroundTaskCoreBinding @runtimeArguments -ReadOnly
    if (-not $ObservationPython) { $ObservationPython = $resolved.python }
    if (-not $HealthPython) { $HealthPython = $resolved.python }
  }
  foreach ($interpreter in @($ObservationPython, $HealthPython)) {
    Assert-BackgroundTaskCoreAbsolutePath $interpreter
    if (-not (Test-Path -LiteralPath $interpreter -PathType Leaf)) {
      throw 'Task-health interpreter is missing'
    }
  }
  return [pscustomobject]@{
    ObservationBinding = $ObservationBinding
    ObservationPython = $ObservationPython
    HealthPython = $HealthPython
  }
}

function Invoke-TaskObservationCollection([string]$Binding, [string]$Python) {
  # Runtime materialization supplies both paths. Never use a global Python or a
  # source-checkout fallback for the periodic publisher.
  $failure = @{schemaVersion=1; published=$false; reason_code='observation_binding_unconfigured'}
  if (-not $Binding -or -not [IO.Path]::IsPathRooted($Binding)) { return $failure }
  $failure.reason_code = 'observation_interpreter_unavailable'
  if (-not $Python -or -not [IO.Path]::IsPathRooted($Python) -or
      -not (Test-Path -LiteralPath $Python -PathType Leaf)) { return $failure }
  $priorPreference = $ErrorActionPreference
  $priorEncoding = $OutputEncoding
  try {
    $OutputEncoding = New-Object System.Text.UTF8Encoding $false
    $ErrorActionPreference = 'Continue'
    $shell = Join-Path $PSHOME 'powershell.exe'
    if (-not (Test-Path -LiteralPath $shell -PathType Leaf)) {
      $shell = Join-Path $PSHOME 'pwsh.exe'
    }
    $output = @(& $Python -I -B -X utf8 -m background_task_core observations --periodic `
      --binding $Binding --powershell $shell --receipt-stdout 2>$null)
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    $receipt = ($output -join "`n") | ConvertFrom-Json -ErrorAction Stop
    if ($receipt.schemaVersion -is [int] -and $receipt.schemaVersion -eq 1 -and
        $code -eq 0 -and $receipt.published -is [bool] -and $receipt.published -eq $true -and
        $receipt.authority_generation -cmatch '^[a-f0-9]{32}$' -and
        $receipt.input_revision -cmatch '^sha256:[a-f0-9]{64}$' -and
        $receipt.sha256 -cmatch '^[a-f0-9]{64}$' -and
        $receipt.effective_request_sha256 -cmatch '^[a-f0-9]{64}$' -and
        ($receipt.authority_epoch -is [int] -or $receipt.authority_epoch -is [long]) -and
        $receipt.authority_epoch -ge 0 -and
        ($receipt.tasks -is [int] -or $receipt.tasks -is [long]) -and $receipt.tasks -ge 0 -and
        ($receipt.subjects -is [int] -or $receipt.subjects -is [long]) -and $receipt.subjects -ge $receipt.tasks) {
      # Return only the fixed envelope, not arbitrary stdout fields.
      return @{schemaVersion=1; published=$true; tasks=$receipt.tasks; subjects=$receipt.subjects;
        authority_generation=$receipt.authority_generation; input_revision=$receipt.input_revision;
        authority_epoch=$receipt.authority_epoch; effective_request_sha256=$receipt.effective_request_sha256;
        sha256=$receipt.sha256}
    }
    $failure.reason_code = 'observation_collection_failed'
    if ($code -ne 0 -and $receipt.published -eq $false -and $receipt.reason_code -in @(
        'dependency_unavailable', 'runtime_binding_required', 'authority_unavailable', 'authority_pending',
        'authority_changed', 'binding_changed', 'catalog_input_changed', 'collector_unavailable',
        'capture_timeout', 'capture_output_limit', 'capture_cleanup_failed', 'capture_launch_failed',
        'capture_cancelled', 'capture_failed', 'worker_timeout', 'worker_output_limit',
        'worker_input_limit', 'input_limit', 'worker_cleanup_failed', 'worker_launch_failed',
        'worker_cancelled', 'worker_failed', 'collection_failed')) {
      $failure.reason_code = $receipt.reason_code
    }
  } catch { $failure.reason_code = 'observation_reader_unavailable' }
  finally {
    $OutputEncoding = $priorEncoding
    $ErrorActionPreference = $priorPreference
  }
  return $failure
}

function ConvertTo-TaskHealthEpoch($Value) {
  if ($null -eq $Value) { return $null }
  return ([DateTimeOffset]([datetime]$Value)).ToUnixTimeMilliseconds() / 1000.0
}

function Get-TaskHealthArtifact([string]$DeclaredPath) {
  $path = [Environment]::ExpandEnvironmentVariables($DeclaredPath)
  if ($path -eq '~') { $path = $HOME }
  elseif ($path.StartsWith('~/') -or $path.StartsWith('~\')) { $path = Join-Path $HOME $path.Substring(2) }
  try {
    $item = Get-Item -LiteralPath $path -ErrorAction Stop
    if ($item.PSIsContainer) {
      # Retain the monitor's recursive file observation scope. A partial traversal
      # cannot claim the newest receipt: any read error invalidates this fact.
      $item = Get-ChildItem -LiteralPath $path -Recurse -File -ErrorAction Stop |
              Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
      if (-not $item) { return @{mtime=$null; reason='artifact_directory_empty'} }
    }
    return @{mtime=(ConvertTo-TaskHealthEpoch $item.LastWriteTimeUtc); reason=$null}
  } catch {
    return @{mtime=$null; reason=('artifact_read_failed: ' + $_.Exception.GetType().Name)}
  }
}

function Get-TaskHealthSnapshot($Declarations, $TaskIndex, [string]$SchedulerError) {
  $rows = @{}
  $artifacts = @{}
  foreach ($entry in $Declarations) {
    $name = [string]$entry.name
    if ($name -and -not $rows.ContainsKey($name)) {
      if ($SchedulerError) {
        $rows[$name] = @{observations=@{query_failed=$true}; query_error=$SchedulerError}
      } elseif ($TaskIndex.ContainsKey($name)) {
        try {
          $task = $TaskIndex[$name]
          $info = $task | Get-ScheduledTaskInfo -ErrorAction Stop
          if (-not $info) { throw 'Scheduler returned no task information' }
          $rows[$name] = @{state=[string]$task.State; last_rc=$info.LastTaskResult;
            code_domain='scheduler'; last_run=(ConvertTo-TaskHealthEpoch $info.LastRunTime);
            next_run=(ConvertTo-TaskHealthEpoch $info.NextRunTime); missed_runs=$info.NumberOfMissedRuns}
        } catch {
          $rows[$name] = @{observations=@{query_failed=$true}; query_error=$_.Exception.GetType().Name}
        }
      }
    }
    foreach ($check in @($entry) + @($entry.checks)) {
      if (-not $check) { continue }
      $path = $check.artifact
      if (-not $path -and $check.legacy) { $path = $check.legacy.artifact }
      if ($path -and -not $artifacts.ContainsKey([string]$path)) {
        $artifacts[[string]$path] = Get-TaskHealthArtifact ([string]$path)
      }
    }
  }
  return @{schemaVersion=1; declarations=@($Declarations); rows=$rows; artifact_observations=$artifacts}
}

function Invoke-TaskHealthEvaluator($Snapshot, [string]$Python, [string]$PythonPath, [double]$Now) {
  # The configured interpreter must have background_task_core installed. Only an explicit
  # override changes the child PYTHONPATH; there is no checkout discovery/fallback.
  # ASCII JSON escapes avoid the .NET Framework stdin encoding default.
  $json = $Snapshot | ConvertTo-Json -Depth 60 -Compress
  $json = [regex]::Replace($json, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value })
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $Python
  # Normal evaluation must not import an old checkout from cwd or ambient PYTHONPATH.
  # An explicit diagnostic override is child-only and remains opt-in.
  $isolation = if ($PythonPath) { '-P -B' } else { '-I -B' }
  $psi.Arguments = $isolation + ' -m background_task_core status --now ' + $Now.ToString('R', [Globalization.CultureInfo]::InvariantCulture)
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
  $psi.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
  $psi.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
  if ($PythonPath) { $psi.EnvironmentVariables['PYTHONPATH'] = $PythonPath }
  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $psi
  $started = $false
  try {
    if (-not $process.Start()) { throw 'Could not start the component health reader' }
    $started = $true
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $process.StandardInput.WriteLine($json)
    $process.StandardInput.Close()
    if (-not $process.WaitForExit(60000)) {
      $process.Kill()
      throw 'Component health reader timed out'
    }
    $output = $stdout.GetAwaiter().GetResult()
    $errorText = $stderr.GetAwaiter().GetResult()
    if ($process.ExitCode -ne 0) {
      throw "Component health reader exited $($process.ExitCode): $($errorText.Trim())"
    }
    $result = $output | ConvertFrom-Json -ErrorAction Stop
    if ($result.schemaVersion -ne 1 -or $result.tasks -isnot [array] -or
        $null -eq $result.summary -or $null -eq $result.coverage -or
        $result.summary.attentionStates -isnot [array] -or
        $null -eq $result.summary.total -or
        $result.tasks.Count -ne @($Snapshot.declarations).Count -or
        $result.summary.total -ne $result.tasks.Count) {
      throw 'Invalid component health response envelope'
    }
    foreach ($coverage in @($result.coverage) + @($result.tasks | ForEach-Object { $_.coverage })) {
      if ($null -eq $coverage -or $null -eq $coverage.checked -or
          $null -eq $coverage.expected -or $null -eq $coverage.ratio -or
          $coverage.state -notin @('zero','partial','complete')) {
        throw 'Incomplete component health coverage response'
      }
    }
    foreach ($task in $result.tasks) {
      if ($task.state -notin @('up','down','never','grace','unknown','running','paused') -or
          $task.verdict -notin @('healthy','unhealthy','degraded','unknown') -or
          $task.checks -isnot [array] -or $task.reason_codes -isnot [array]) {
        throw 'Incomplete component health task response'
      }
    }
    return $result
  } finally {
    if ($started -and -not $process.HasExited) { $process.Kill() }
    $process.Dispose()
  }
}
