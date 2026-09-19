<# Compatibility delegate. TASKCONSOLE_NAME/VERB remain literal arguments.
   Authority is mandatory in the shared CLI, including legacy tasks.
   Exit 0 = acknowledged/applied; exit 1 = refused, failed or uncertain.
#>
[CmdletBinding()]
param(
    [Alias('AuthorityBundle')][string]$RuntimeConfig = $env:TASK_CONSOLE_RUNTIME_CONFIG,
    [string]$PrivateRoot = $env:TASK_CONSOLE_PRIVATE_ROOT,
    [string]$StateRoot = $env:TASK_CONSOLE_STATE_ROOT,
    [string]$VaultRoot = $env:TASK_CONSOLE_VAULT_ROOT,
    [string]$Python = $env:TASK_CONSOLE_PYTHON
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
if (-not $Python) { $Python = 'python' }
try {
    $arguments = @('-m', 'task_console', 'control')
    $payload = @{name=$env:TASKCONSOLE_NAME; verb=$env:TASKCONSOLE_VERB} | ConvertTo-Json -Compress
    if ($RuntimeConfig -or $PrivateRoot -or $StateRoot -or $VaultRoot) {
        $arguments += @('--runtime-config', $RuntimeConfig, '--private-root', $PrivateRoot,
                        '--state-root', $StateRoot, '--vault-root', $VaultRoot)
    }
    $output = $payload | & $Python @arguments 2>$null
    $code = $LASTEXITCODE
    $document = ($output -join "`n") | ConvertFrom-Json
    if ($null -eq $document -or $document.schemaVersion -ne 1) { throw 'Missing controller JSON' }
    $output
    if ($code -ne 0) { exit 1 }
    exit 0
} catch {
    @{schemaVersion=1; ok=$false; message='Task controller unavailable'; before=$null; after=$null;
      error=@{code='controller_unavailable'; field='runtime'}} | ConvertTo-Json -Compress
    exit 1
}
