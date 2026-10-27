<#
  background-task-core.ps1 - 启动本机的任务调度台。

  本机的路径(分类配置、健康监控清单、备份 allow-list、历史日志)都不在这里:它们写在
  BACKGROUND_TASK_CORE_CONFIG 指向的私有伴生仓的 data/machines/<CLAUDE_MACHINE_ID>/launcher-binding.json 里,这个启动器只读那一份。

  服务以已安装的解释器起成隐藏独立进程,不挂在启动它的那个窗口上。之所以特地这样:
  用前台 python 起的话,关掉终端(或会话被清理)服务就没了,而你正开着的页面下一次刷新就报错。
  代价是它不会自己退出,所以下面给了 -Stop。

  用法:
    background-task-core.ps1            开独立窗口(Chrome/Edge app 模式,无地址栏)
    background-task-core.ps1 -Tab       开普通浏览器标签页
    background-task-core.ps1 -NoBrowser 启动服务，不打开窗口
    background-task-core.ps1 -Stop      停掉服务
    background-task-core.ps1 -Status    只看状态,不改任何东西
#>
[CmdletBinding()]
param(
  [ValidateRange(1,65535)][int]$Port = 8787,
  [switch]$Tab,
  [switch]$NoBrowser,
  [switch]$Stop,
  [switch]$Status,
  [string]$Python,
  [string]$ArtifactResolver,
  [string]$InstalledRoot,
  [string]$RuntimeConfig,
  [string]$PrivateRoot,
  [string]$StateRoot,
  [string]$VaultRoot,
  [string]$BindingFile = '',
  [string]$PythonPath = "",
  [string]$StatusSnapshot = "",
  [string]$CatalogSnapshot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$url = "http://127.0.0.1:$Port/"
if ($PythonPath) { throw 'PythonPath override removed; use reviewed installed artifacts' }
. (Join-Path $PSScriptRoot 'background-task-core-binding.ps1')
if (-not $BindingFile) { $BindingFile = Get-BackgroundTaskCoreDefaultBindingFile }
foreach ($snapshot in @($StatusSnapshot, $CatalogSnapshot)) {
  if ($snapshot) { Assert-BackgroundTaskCoreAbsolutePath $snapshot }
}
$overrides = @{}
foreach ($name in @('Python','ArtifactResolver','InstalledRoot','RuntimeConfig','PrivateRoot','StateRoot','VaultRoot')) {
  if ($PSBoundParameters.ContainsKey($name)) { $overrides[$name] = $PSBoundParameters[$name] }
}
$startupMutex = $null
$startupHeld = $false
if (-not $Status) {
  $startupMutex = [Threading.Mutex]::new($false, (Get-BackgroundTaskCoreStartupMutexName -BindingFile $BindingFile))
  try { $startupHeld = $startupMutex.WaitOne(0) }
  catch [Threading.AbandonedMutexException] { $startupHeld = $true }
  if (-not $startupHeld) { $startupMutex.Dispose(); throw 'Another background-task-core start/stop is in progress; retry after it finishes.' }
}
try {
# The same mutex covers the fresh listener check, binding read, launch and readiness.
try {
  $configuration = Read-BackgroundTaskCoreLauncherBinding -BindingFile $BindingFile -Overrides $overrides
  $runtime = $configuration.Runtime
  $binding = Resolve-BackgroundTaskCoreBinding @runtime -ReadOnly
} catch {
  if (-not $Status) { throw }
  $listener = Get-BackgroundTaskCoreListenerIdentity -Port $Port
  Write-Host "Binding unavailable; listener=$($listener.Status); identity unverified. $($_.Exception.Message)"
  return
}
$listener = Get-BackgroundTaskCoreListenerIdentity -Port $Port -Python $binding.python
if ($Status) {
  Write-Host "background-task-core: $url listener=$($listener.Status) pid=$($listener.ProcessId)"
  return
}
if ($listener.Status -eq 'mismatch') {
  throw "Background-task-core migration needed: port $Port belongs to an old, foreign, or unverifiable listener. Complete the reviewed live handoff before opening this launcher."
}
if ($Stop) {
  if ($listener.Status -eq 'matched') { Stop-Process -Id $listener.ProcessId -Force }
  else { Write-Host 'Background-task-core is not running' }
  return
}

if ($listener.Status -eq 'matched') {
  Write-Host "Compatible installed background-task-core is running (pid=$($listener.ProcessId))."
} else {
  $binding = Resolve-BackgroundTaskCoreBinding @runtime
  Set-BackgroundTaskCoreWorkReader -Configuration $configuration -Resolved $binding
  $pyw = $binding.python
  $env:PYTHONDONTWRITEBYTECODE = '1'
  foreach ($key in $configuration.Environment.Keys) {
    [Environment]::SetEnvironmentVariable($key, $configuration.Environment[$key], 'Process')
  }
  # Optional observations remain unavailable until the operator binds them.
  if ($StatusSnapshot) { $env:BACKGROUND_TASK_CORE_STATUS_SNAPSHOT = $StatusSnapshot }
  if ($CatalogSnapshot) { $env:BACKGROUND_TASK_CORE_CATALOG_SNAPSHOT = $CatalogSnapshot }
  $log = Join-Path $runtime.StateRoot 'background-task-core.log'
  $proc = Start-Process -FilePath $pyw -WorkingDirectory $binding.generation -PassThru -WindowStyle Hidden `
          -ArgumentList '-P','-u','-m','background_task_core.server','--port',"$Port",'--no-browser' `
          -RedirectStandardOutput $log -RedirectStandardError "$log.err"

  # 等它真的开始监听,而不是等一个固定秒数再假设成功。
  $ok = $false
  for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    $listener = Get-BackgroundTaskCoreListenerIdentity -Port $Port -Python $binding.python
    if ($listener.Status -eq 'mismatch') { throw "Background-task-core migration needed: unexpected listener on port $Port" }
    if ($listener.Status -eq 'matched') { $ok = $true; break }
    if ($proc.HasExited) { break }
  }
  if (-not $ok) {
    Write-Host "启动失败,端口 $Port 没有监听。错误输出:" -ForegroundColor Red
    if (Test-Path "$log.err") { Get-Content "$log.err" -Tail 15 | ForEach-Object { Write-Host "  $_" } }
    exit 1
  }
  Write-Host "已启动 pid=$($proc.Id)  独立进程,不随这个窗口结束"
}

if ($configuration.Environment.ContainsKey('BACKGROUND_TASK_CORE_REMINDER_CLI')) {
  Assert-BackgroundTaskCoreWorkReady -Url $url
} else {
  Write-Warning 'Work view is not configured; task console is running with that view unavailable.'
}

Write-Host "任务调度台: $url"
Write-Host "  停止: background-task-core.ps1 -Stop    状态: background-task-core.ps1 -Status"

if ($NoBrowser) { return }
if ($Tab) {
  Start-Process $url
} else {
  $browsers = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
  ) | Where-Object { Test-Path -LiteralPath $_ }
  if ($browsers) {
    Start-Process -FilePath $browsers[0] -ArgumentList "--app=$url",'--window-size=1700,1040'
    Write-Host "  已用 $([System.IO.Path]::GetFileNameWithoutExtension($browsers[0])) 开独立窗口"
  } else {
    Start-Process $url
    Write-Host "  没找到 Chrome/Edge,用了默认浏览器"
  }
}
} finally {
  if ($startupHeld) { $startupMutex.ReleaseMutex() }
  if ($null -ne $startupMutex) { $startupMutex.Dispose() }
}
