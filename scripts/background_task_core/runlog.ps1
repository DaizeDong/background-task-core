# Emit event XML; Python owns timestamp, task name and return-code normalization.
[CmdletBinding()]
param([int]$Days = 60, [int]$MaxEvents = 20000)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$channel = 'Microsoft-Windows-TaskScheduler/Operational'
try {
    $cfg = Get-WinEvent -ListLog $channel -ErrorAction Stop
} catch {
    @{ enabled = $false; reason = "Cannot read channel: $($_.Exception.Message)"; events = @() } |
        ConvertTo-Json -Compress
    exit 0
}
if (-not $cfg.IsEnabled) {
    @{ enabled = $false; reason = 'Task Scheduler history is disabled; enabling it starts new history.'; events = @() } |
        ConvertTo-Json -Compress
    exit 0
}
$since = (Get-Date).AddDays(-$Days)
$xml = [System.Collections.Generic.List[string]]::new()
$dropped = 0
$partial = $false
$reason = $null
$seen = 0
try {
    Get-WinEvent -FilterHashtable @{ LogName = $channel; Id = @(100,102,111,201,203,329); StartTime = $since } -MaxEvents $MaxEvents -ErrorAction Stop |
        ForEach-Object {
            $seen++
            try { $xml.Add($_.ToXml()) } catch { $dropped++ }
        }
} catch {
    # Use the stable error ID rather than a localized Windows message.
    if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') {
        $partial = $true
        $reason = "Event read failed: $($_.Exception.Message)"
    }
}
@{ enabled = $true; reason = $reason; partial = $partial
   since = $since.ToString('yyyy-MM-dd'); dropped = $dropped
   truncated = ($seen -ge $MaxEvents)
   oldestRecordId = $cfg.OldestRecordNumber; recordCount = $cfg.RecordCount
   xml = @($xml.ToArray()) } | ConvertTo-Json -Depth 4 -Compress
