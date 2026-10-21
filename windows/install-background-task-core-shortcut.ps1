<#
  给基建控制台建一个真正的桌面入口:桌面快捷方式 + 开始菜单项,带图标。

  为什么需要它:启动器本身已经用浏览器的 --app= 开无边框窗口,看起来就是个应用,
  但要用它得先想起来去敲一条 PowerShell 命令。一个要先想起来才能用的入口,
  对一个「打开就在眼前」的东西来说等于不存在。

  图标保存在 BACKGROUND_TASK_CORE_CONFIG 指向的私有伴生仓的 data/assets/icon.ico,缺失时直接报错。

  幂等:重复跑只会覆盖同名快捷方式。加 -Remove 卸掉。
#>
param(
  [switch]$Remove,
  [string]$Name = 'Fleet Console'
)

$ErrorActionPreference = 'Stop'

$launcher = Join-Path $PSScriptRoot 'background-task-core.ps1'
if (-not $env:BACKGROUND_TASK_CORE_CONFIG) { throw 'BACKGROUND_TASK_CORE_CONFIG is not set; it names the private companion that holds the icon' }
$companionRoot = $env:BACKGROUND_TASK_CORE_CONFIG
$iconPath = Join-Path $companionRoot 'data\assets\icon.ico'

$desktop  = [Environment]::GetFolderPath('Desktop')
$startDir = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'
$targets  = @((Join-Path $desktop "$Name.lnk"), (Join-Path $startDir "$Name.lnk"))

if ($Remove) {
  foreach ($t in $targets) {
    if (Test-Path $t) { Remove-Item $t -Force; "removed $t" }
  }
  return
}

if (-not (Test-Path $launcher)) { throw "launcher not found: $launcher" }

# Use the launcher's verified installed runtime without starting or changing the service.
. (Join-Path $PSScriptRoot 'background-task-core-binding.ps1')
$configuration = Read-BackgroundTaskCoreLauncherBinding
$runtimeArgs = $configuration.Runtime
$resolved = Resolve-BackgroundTaskCoreBinding @runtimeArgs -ReadOnly
$admissionCode = @'
import json, sys
from pathlib import Path
from fleet_guards import filesystem as fs

root = fs.validate_path(Path(sys.argv[1]))
target = fs.validate_path(root / 'data/assets/icon.ico', root=root)
if target.exists() and not target.is_file():
    raise ValueError('Icon destination is not a file')
print(json.dumps({'path': str(target)}))
'@
$rawAdmission = & $resolved.python -I -B -c $admissionCode $companionRoot
if ($LASTEXITCODE -ne 0) { throw 'Icon destination check failed; no shortcut was changed' }
$admission = ($rawAdmission -join "`n") | ConvertFrom-Json
if (-not [string]::Equals($admission.path, $iconPath, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'Icon destination admission returned an unexpected path'
}

if (-not (Test-Path -LiteralPath $iconPath -PathType Leaf)) { throw "icon not found: $iconPath (restore it from the private companion)" }

$pwsh = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shell = New-Object -ComObject WScript.Shell
foreach ($t in $targets) {
  $dir = Split-Path $t
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $sc = $shell.CreateShortcut($t)
  $sc.TargetPath = $pwsh
  # -WindowStyle Hidden:启动器以隐藏窗口启动服务,不该留一个黑框在桌面上。
  $sc.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`""
  $sc.WorkingDirectory = Split-Path $launcher
  $sc.Description = 'Local infrastructure console'
  $sc.IconLocation = "$iconPath,0"
  $sc.Save()
  "created $t"
}

# 建完立刻自检:一个「建好了」的输出和一个真的建好了的快捷方式必须是两回事。
$missing = $targets | Where-Object { -not (Test-Path $_) }
if ($missing) { throw "shortcut reported created but is not on disk: $($missing -join ', ')" }
"icon: $iconPath"
