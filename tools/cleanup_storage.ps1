param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [string]$Python = 'python',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$retentionRoot = (Get-Item -LiteralPath $DataRoot).FullName.TrimEnd('\', '/')
$retentionPlanner = Join-Path $PSScriptRoot 'storage_retention.py'
function Read-RetirementPlan {
    $text = & $Python -B $retentionPlanner --data-root $retentionRoot
    if ($LASTEXITCODE -ne 0) { throw 'Retirement admission failed; nothing was removed' }
    return ($text | ConvertFrom-Json)
}
function Assert-RetirementPath([string]$Path, [string]$TargetRoot) {
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -ne $TargetRoot -and -not $full.StartsWith($TargetRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Deletion escaped its admitted artifact root'
    }
    if (-not $full.StartsWith($retentionRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Deletion escaped the companion data directory'
    }
    $ancestor = $full
    while ($ancestor -and $ancestor -ne $retentionRoot) {
        $parentItem = Get-Item -LiteralPath $ancestor -Force
        if (($parentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Refusing a reparse ancestor' }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
    $item = Get-Item -LiteralPath $full -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Refusing to follow a reparse point' }
    return $item
}
function Remove-RetiredArtifact([string]$Path, [string]$TargetRoot, $KnownItem = $null) {
    $item = $KnownItem
    if ($null -eq $item) { $item = Assert-RetirementPath $Path $TargetRoot }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Refusing to follow a reparse point' }
    if ($item.PSIsContainer) {
        $null = Assert-RetirementPath $Path $TargetRoot
        foreach ($child in @(Get-ChildItem -LiteralPath $item.FullName -Force)) {
            if (-not $child.FullName.StartsWith($TargetRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Child escaped its admitted artifact root'
            }
            Remove-RetiredArtifact $child.FullName $TargetRoot $child
        }
        $null = Assert-RetirementPath $Path $TargetRoot
    }
    Remove-Item -LiteralPath $Path -Force
}
$retentionPlan = Read-RetirementPlan
if (-not $Apply) {
    $retentionPlan | ConvertTo-Json -Depth 10
    exit 0
}
# A second complete scan detects changed or newly linked artifacts before the first deletion.
$retentionRecheck = Read-RetirementPlan
if (($retentionPlan | ConvertTo-Json -Depth 10 -Compress) -cne ($retentionRecheck | ConvertTo-Json -Depth 10 -Compress)) {
    throw 'Retirement inputs changed after preview; nothing was removed'
}
$retentionProcesses = @(Get-CimInstance Win32_Process)
foreach ($entry in $retentionPlan.entries) {
    foreach ($process in $retentionProcesses) {
        if ($process.ProcessId -ne $PID -and $process.CommandLine -and
                $process.CommandLine.Replace('/', '\').IndexOf($entry.path.Replace('/', '\'), [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            throw 'An artifact still has a process referring to it; stop its writer explicitly first'
        }
    }
}
foreach ($entry in $retentionPlan.entries) {
    Remove-RetiredArtifact $entry.path $entry.path
}
[ordered]@{schema_version = 1; retired_roots = @($retentionPlan.entries).Count;
    deleted_files = $retentionPlan.files; deleted_bytes = $retentionPlan.bytes;
    completed_at_utc = [DateTime]::UtcNow.ToString('o')} | ConvertTo-Json
