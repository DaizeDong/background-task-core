<#
  export-task-xml.ps1: export the Task Scheduler XML of every task in the registration authority's
  TaskNames.ps1 projection (the launcher binding's BACKGROUND_TASK_CORE_ALLOWLIST) into
  <companion>\data\task-exports\<Name>.xml, where the companion's own push versions it.

  The export is a reference copy. Tasks are recreated on a new machine from each owning
  repository's automation/tasks.json by install-skill, never from these files. A task declared with
  "backup": false is not in the projection and is not exported.

  The new export is written beside the old one and swapped in, so a run that throws leaves the
  previous export in place. Exit 0 when every listed task was exported, 1 when any was not found.
#>
param(
  [string]$Companion = $env:BACKGROUND_TASK_CORE_CONFIG,
  [string]$LauncherBindingFile = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'background-task-core-binding.ps1')
if (-not $Companion) { throw 'Pass -Companion or set BACKGROUND_TASK_CORE_CONFIG' }

$environment = (Read-BackgroundTaskCoreLauncherBinding -BindingFile $LauncherBindingFile).Environment
$projection = $environment['BACKGROUND_TASK_CORE_ALLOWLIST']
if (-not $projection) { throw 'Launcher binding names no BACKGROUND_TASK_CORE_ALLOWLIST' }
$TaskNames = $null
. $projection
$names = @($TaskNames | Where-Object { $_ })
if (-not $names.Count) { throw 'The TaskNames.ps1 projection lists no task' }

$target = Join-Path $Companion 'data\task-exports'
$staging = $target + '.new'
if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging | Out-Null
$missing = @()
foreach ($name in $names) {
  $file = Join-Path $staging (($name -replace '[\\/:*?"<>|]', '_') + '.xml')
  try { Export-ScheduledTask -TaskName $name -ErrorAction Stop | Set-Content -LiteralPath $file -Encoding UTF8 }
  catch { $missing += $name }
}
if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
Move-Item -LiteralPath $staging -Destination $target

$line = 'exported {0} of {1} task(s) to {2}' -f ($names.Count - $missing.Count), $names.Count, $target
if ($missing.Count) { $line += '; not in Task Scheduler: ' + ($missing -join ', ') }
Write-Output $line
if ($missing.Count) { exit 1 }
exit 0
