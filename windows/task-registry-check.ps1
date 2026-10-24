<#
  task-registry-check.ps1 - 计划任务创建规范的执行机制

  规范全文在本仓 docs/task-creation.md。

  断言三条(第四条"开关漂移"尚未实现,理由见文件末尾,不要假装它在):
    A1 三处登记齐全 -- 每个活跃的自装任务同时出现在活体任务表、备份 allow-list(登记权威生成的
       TaskNames.ps1 投影)、健康监控清单里。两份清单的位置都从私有 launcher binding 读。
       声明过的例外(task-health.json 里带 watched_elsewhere 的条目)算通过,名字碰巧没被正则
       匹配上的沉默不算。
    A2 声明的产物不是日志 -- .log 型 artifact 必须声明语义(只在成功路径写,或不能证明成功)。
    A3 并发闸自测通过 -- 含负对照。task-gate.ps1 -SelfTest 的退出码即失败项数。

  枚举到 0 个任务时报失败,不报通过。

  退出码: 0 = 全部通过 ; 1 = 有违规(逐条打印) ; 2 = 检查器自身无法工作(枚举失败等)
#>
[CmdletBinding()]
param(
  # Both default to the private launcher binding (BACKGROUND_TASK_CORE_HEALTH and
  # BACKGROUND_TASK_CORE_ALLOWLIST).
  [string]$HealthConfig = '',
  [string]$AllowList    = '',
  [string]$GateScript   = $(if ($env:CLAUDE_CONFIG_DIR) { Join-Path $env:CLAUDE_CONFIG_DIR 'scripts\task-gate.ps1' } else { '' }),
  [switch]$SkipGateSelfTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $HealthConfig -or -not $AllowList) {
  try {
    . (Join-Path $PSScriptRoot 'background-task-core-binding.ps1')
    $bound = (Read-BackgroundTaskCoreLauncherBinding).Environment
  } catch { Write-Host "  [FATAL] launcher binding 读不出: $($_.Exception.Message)"; exit 2 }
  if (-not $HealthConfig) { $HealthConfig = $bound['BACKGROUND_TASK_CORE_HEALTH'] }
  if (-not $AllowList) { $AllowList = $bound['BACKGROUND_TASK_CORE_ALLOWLIST'] }
  if (-not $HealthConfig -or -not $AllowList) { Write-Host "  [FATAL] launcher binding 没有 HEALTH 或 ALLOWLIST,这不是通过。"; exit 2 }
}

$violations = New-Object System.Collections.Generic.List[string]
$checked = [ordered]@{}

function Fail($m) { $violations.Add($m) | Out-Null; Write-Host "  [FAIL] $m" }
function Pass($m) { Write-Host "  [ok]   $m" }

# --- 厂商任务不在本规范范围内 ---
$vendorPat = '^(NVIDIA|OneDrive|Adobe|Zoom|XRite|MicrosoftEdge|Nahimic|Optane|Intel|NvProfile|SoftLanding|RunPlatform|Lenovo|Launch Adobe)'

Write-Host "task-registry-check  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# =====================================================================
# 枚举先行,并证明枚举成功(不变量 I3)
# =====================================================================
$allTasks = @(Get-ScheduledTask -TaskPath '\' -ErrorAction SilentlyContinue)
if ($allTasks.Count -eq 0) {
  Write-Host "  [FATAL] Get-ScheduledTask 枚举到 0 个任务,本次检查什么都没断言。这不是通过。"
  exit 2
}
$mine = @($allTasks | Where-Object { $_.TaskName -notmatch $vendorPat })
$active = @($mine | Where-Object { $_.State -ne 'Disabled' })
if ($active.Count -eq 0) {
  Write-Host "  [FATAL] 过滤后活跃自装任务为 0 个,判别式可能坏了。这不是通过。"
  exit 2
}
$checked['活跃自装任务'] = $active.Count

# =====================================================================
# A1  三处登记齐全
# =====================================================================
Write-Host "A1 三处登记"

if (-not (Test-Path -LiteralPath $HealthConfig)) { Write-Host "  [FATAL] 读不到 $HealthConfig"; exit 2 }
$cfgRaw = Get-Content -LiteralPath $HealthConfig -Raw
$cfg = $cfgRaw | ConvertFrom-Json
$watched = @{}
foreach ($t in $cfg.tasks) {
  if ($t.PSObject.Properties.Name -contains 'name' -and $t.name) { $watched[$t.name] = $t }
}
if ($watched.Count -eq 0) { Write-Host "  [FATAL] task-health.json 解析出 0 个条目,这不是通过。"; exit 2 }
$checked['健康监控条目'] = $watched.Count

if (-not (Test-Path -LiteralPath $AllowList)) { Write-Host "  [FATAL] 读不到 $AllowList"; exit 2 }
# 投影是登记权威生成的 PowerShell 文件,只定义 $TaskNames;夜间备份导出读的也是它。
$TaskNames = $null
. $AllowList
$allowNames = @($TaskNames | Where-Object { $_ })
if ($allowNames.Count -eq 0) { Write-Host "  [FATAL] allow-list 解析出 0 个名字,这不是通过。"; exit 2 }
$checked['备份 allow-list'] = $allowNames.Count

foreach ($t in ($active | Sort-Object TaskName)) {
  $n = $t.TaskName
  $inAllow = $allowNames -contains $n
  $entry = if ($watched.ContainsKey($n)) { $watched[$n] } else { $null }
  $inHealth = $null -ne $entry
  $exempt = $inHealth -and ($entry.PSObject.Properties.Name -contains 'watched_elsewhere')

  $missing = @()
  if (-not $inAllow) { $missing += '备份 allow-list(换机会静默丢失)' }
  if (-not $inHealth) { $missing += '健康监控清单(死了没人知道)' }
  if ($missing.Count -gt 0) {
    Fail "$n 缺登记: $($missing -join ' + ')"
  } elseif ($exempt) {
    Pass "$n (声明过的例外: 由 $($entry.watched_elsewhere) 监视)"
  }
}
if ($violations.Count -eq 0) { Pass "全部 $($active.Count) 个活跃任务三处登记齐全" }

# =====================================================================
# A2  声明的产物不是日志
# =====================================================================
Write-Host "A2 产物不是日志(不变量 I2)"
$a2bad = 0
# 真正的不变量不是"扩展名不能是 .log",是"这个文件不能在失败路径上被写"。
# 文件名只是代理指标,而且是个不准的代理: 一个只在成功路径上写的 .log 是合法的产物,
# 它变陈旧本身就是"任务瞎了"的信号(出处是 2026-10-17 已删除的 CcDailyTriage)。
# 所以规则是"例外必须声明",和 ok_codes 同一套: 声明过的可复核,沉默的不可复核。
$a2review = 0
foreach ($t in $cfg.tasks) {
  if ($t.PSObject.Properties.Name -notcontains 'artifact') { continue }
  if (-not $t.artifact) { continue }
  if ($t.artifact -notmatch '\.log$') { continue }
  $props = $t.PSObject.Properties.Name
  if ($props -contains 'artifact_cannot_prove_success' -and $t.artifact_cannot_prove_success) {
    # 已核实过、结论是它撑不起豁免、并且已经如实声明。这是通过,不是违规:
    # 它保留产物是为了抓"彻底不跑了",而监控那边的豁免分支已经因为这个字段对它关闭。
    Pass "$($t.name) 已核实产物撑不起豁免,已声明,监控豁免对它关闭"
  } elseif ($props -contains 'artifact_written_only_on_success') {
    Pass "$($t.name) 日志即产物,已声明只在成功时写"
  } elseif ($props -contains 'artifact_review_pending') {
    # 已经核实过并且核实结果是坏的,不能和"还没核"显示成一样。前者是已知缺陷,
    # 后者是未知,把它们并成一桶就等于把已知缺陷藏进未知里。
    if ($t.artifact_review_pending -match '^VERIFIED BAD') {
      Fail "$($t.name) 已证实产物在失败路径上照写,监控对它的判绿是假的: $($t.artifact)"
      $a2bad++
    } else {
      Write-Host "  [待核]  $($t.name) 产物是 .log,尚未核实它在失败路径上是否照写: $($t.artifact)"
      $a2review++
    }
  } else {
    Fail "$($t.name) 声明的产物是 .log 却没有任何声明: $($t.artifact)。要么加 artifact_written_only_on_success(附理由),要么加 artifact_review_pending 表示还没核。沉默不是选项。"
    $a2bad++
  }
}
if ($a2bad -eq 0 -and $a2review -eq 0) { Pass "没有未声明的日志型产物" }
elseif ($a2bad -eq 0) { Pass "无违规;$a2review 个待核实(已声明为待核,不是沉默)" }

# =====================================================================
# A3  并发闸自测(含负对照)
# =====================================================================
Write-Host "A3 并发闸自测"
if ($SkipGateSelfTest) {
  Write-Host "  [skip] 按 -SkipGateSelfTest 跳过。注意:跳过不是通过。"
} elseif (-not (Test-Path -LiteralPath $GateScript)) {
  Fail "并发闸脚本不存在: $GateScript"
} else {
  $ps = (Get-Process -Id $PID).Path
  & $ps -NoProfile -ExecutionPolicy Bypass -File $GateScript -SelfTest | Out-Null
  $rc = $LASTEXITCODE
  if ($rc -eq 0) { Pass "闸门自测 9 项全过(含负对照)" }
  else { Fail "闸门自测有 $rc 项失败,跑 task-gate.ps1 -SelfTest 看细节" }
}

# =====================================================================
Write-Host ""
Write-Host "检查范围: $(($checked.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ', ')"
if ($violations.Count -eq 0) {
  Write-Host "task-registry-check: 通过"
  exit 0
}
Write-Host "task-registry-check: $($violations.Count) 项违规"
exit 1

<#
  尚未实现,不要以为它在:

  A4 开关漂移(活体任务的 StartWhenAvailable / RestartCount / ExecutionTimeLimit 等
     是否等于它声明的值)。做不了的原因不是难,是 task-health.json 里还没有"声明的开关"
     这个字段 -- 没有声明就没有可比对的东西,现在写一个只会对着默认值自说自话。
     要做的顺序是: 先给条目加 expected_settings,再让这里断言。

     在那之前,规范第 3.1 节那张"会咬人的默认值"表是靠人执行的,不是靠机器。
     这句话写在这里,是为了让"没有 A5" 是一个可读到的事实,而不是一个没人注意到的空白。
#>
