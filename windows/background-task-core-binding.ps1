# One resolver adapter shared by task, backup and agent entrypoints.
# The installed launcher resolves its receipt; this helper never parses pointers.
$script:BackgroundTaskCoreRuntimeNames = @('Python','ArtifactResolver','InstalledRoot','RuntimeConfig','PrivateRoot','StateRoot','VaultRoot')
$script:BackgroundTaskCoreRuntimeVariables = @('BACKGROUND_TASK_CORE_PYTHON','BACKGROUND_TASK_CORE_ARTIFACT_RESOLVER','BACKGROUND_TASK_CORE_INSTALLED_ROOT',
    'BACKGROUND_TASK_CORE_RUNTIME_CONFIG','BACKGROUND_TASK_CORE_PRIVATE_ROOT','BACKGROUND_TASK_CORE_STATE_ROOT','BACKGROUND_TASK_CORE_VAULT_ROOT')

function Test-BackgroundTaskCoreLauncherUsesBindingFile {
    param([hashtable]$Overrides = @{})
    if ($Overrides.Count) { return $false }
    foreach ($variable in $script:BackgroundTaskCoreRuntimeVariables) {
        if ([Environment]::GetEnvironmentVariable($variable, 'Process')) { return $false }
    }
    return $true
}

function Assert-BackgroundTaskCoreAbsolutePath {
    param($Value)
    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value) -or
        $Value -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+[\\/])' -or
        $Value -match '[\x00-\x1f"*?]') {
        throw 'Complete absolute reviewed background-task-core paths required'
    }
    $null = [IO.Path]::GetFullPath($Value)
}

function Get-BackgroundTaskCoreStartupMutexName {
    param([string]$BindingFile)
    Assert-BackgroundTaskCoreAbsolutePath $BindingFile
    $normalized = [IO.Path]::GetFullPath($BindingFile).Replace('/', '\').ToUpperInvariant()
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $digest = $hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($normalized)) }
    finally { $hasher.Dispose() }
    return 'Global\BackgroundTaskCoreStartup_' + ([BitConverter]::ToString($digest).Replace('-', '').ToLowerInvariant())
}

# The private companion named by BACKGROUND_TASK_CORE_CONFIG holds one launcher binding per machine,
# data/machines/<CLAUDE_MACHINE_ID>/launcher-binding.json, because every value in it is a path on that machine.
# The id is read from this process and then from HKCU\Environment, so a process started before it was
# set still finds it. An unset variable is a refusal, never a guessed location.
function Get-BackgroundTaskCoreDefaultBindingFile {
    if (-not $env:BACKGROUND_TASK_CORE_CONFIG) {
        throw 'BACKGROUND_TASK_CORE_CONFIG is not set; it names the private companion that holds data/machines/<CLAUDE_MACHINE_ID>/launcher-binding.json'
    }
    $machine = $env:CLAUDE_MACHINE_ID
    if (-not $machine) { $machine = [Environment]::GetEnvironmentVariable('CLAUDE_MACHINE_ID', 'User') }
    if (-not $machine -or -not $machine.Trim()) {
        throw 'CLAUDE_MACHINE_ID is not set; set it once per machine as a user environment variable (there is no default binding)'
    }
    return Join-Path $env:BACKGROUND_TASK_CORE_CONFIG ('data\machines\' + $machine.Trim() + '\launcher-binding.json')
}

function Read-BackgroundTaskCoreLauncherBinding {
    param(
        [string]$BindingFile = '',
        [hashtable]$Overrides = @{}
    )
    $names = $script:BackgroundTaskCoreRuntimeNames
    $variables = $script:BackgroundTaskCoreRuntimeVariables
    $runtime = @{}
    $environment = @{}
    $uiFromBinding = $false
    $allowed = @('DB','CATEGORIES','HEALTH','ALLOWLIST','HISTORY','STATUS_SNAPSHOT','CATALOG_SNAPSHOT',
        'REMINDER_CLI','REMINDER_DB','ACTION_WORKSPACE','AGENT_TASK_ID','DELETE_FOLLOWUP',
        'DELETED_ARCHIVE','SKILLS','SKILL_ARCHIVE','MEMORY','CLAUDE','REPOS','PLUGIN_CACHE',
        'SESSIONS','CONVO_CACHE','MEMORY_ARCHIVER','VISIBILITY','CODEX','IDENTITIES',
        'LLMCALL_LEDGER','LLMCALL_CHAIN') | ForEach-Object { 'BACKGROUND_TASK_CORE_' + $_ }
    $allowed += @('SCHEDULE_REMINDER_CONFIG','SCHEDULE_DB_PATH','AGENT_CENTER_RUNS','LLMCALL_CONFIG')
    if ($Overrides.Count) {
        if ($Overrides.Count -ne $names.Count) { throw 'Explicit runtime override must be a complete set' }
        foreach ($name in $names) { $runtime[$name] = $Overrides[$name] }
    } else {
        for ($i = 0; $i -lt $names.Count; $i++) {
            $value = [Environment]::GetEnvironmentVariable($variables[$i], 'Process')
            if ($value) { $runtime[$names[$i]] = $value }
        }
        if ($runtime.Count -and $runtime.Count -ne $names.Count) {
            throw 'Environment runtime override must be a complete set'
        }
        if (-not $runtime.Count) {
            if (-not $BindingFile) { $BindingFile = Get-BackgroundTaskCoreDefaultBindingFile }
            Assert-BackgroundTaskCoreAbsolutePath $BindingFile
            if (-not (Test-Path -LiteralPath $BindingFile -PathType Leaf)) {
                throw 'Launcher binding unavailable; create reviewed data/machines/<CLAUDE_MACHINE_ID>/launcher-binding.json in the private background-task-core companion, or supply -BindingFile'
            }
            $raw = Get-Content -LiteralPath $BindingFile -Raw -Encoding UTF8
            # Consume whole string tokens so strings containing colons are not mistaken for keys.
            $keys = @{}
            foreach ($token in [regex]::Matches($raw, '"(?:[^"\\]|\\.)*"\s*:|"(?:[^"\\]|\\.)*"')) {
                if ($token.Value.EndsWith(':')) {
                    $key = ('[' + $token.Value.TrimEnd(':').TrimEnd() + ']') | ConvertFrom-Json
                    if ($keys.ContainsKey($key[0])) { throw 'Duplicate launcher binding key' }
                    $keys[$key[0]] = $true
                }
            }
            $document = $raw | ConvertFrom-Json
            if ($document -isnot [pscustomobject]) { throw 'Launcher binding must be a JSON object' }
            foreach ($key in $document.PSObject.Properties.Name) {
                if ($key -cnotin @('schemaVersion','runtime','environment')) { throw 'Unknown launcher binding key' }
            }
            if (-not $document.PSObject.Properties['schemaVersion'] -or
                $document.schemaVersion -isnot [int] -or $document.schemaVersion -ne 1 -or
                -not $document.PSObject.Properties['runtime'] -or $document.runtime -isnot [pscustomobject] -or
                -not $document.PSObject.Properties['environment'] -or $document.environment -isnot [pscustomobject]) {
                throw 'Launcher binding requires schemaVersion 1, runtime and environment objects'
            }
            foreach ($property in $document.runtime.PSObject.Properties) {
                if ($property.Name -cnotin $names) { throw 'Unknown runtime binding key' }
                $runtime[$property.Name] = $property.Value
            }
            if ($runtime.Count -ne $names.Count) { throw 'Complete runtime binding required' }
            $uiFromBinding = $true
            foreach ($property in $document.environment.PSObject.Properties) {
                if ($property.Name -cnotin $allowed) { throw 'Unknown UI environment binding key' }
                $environment[$property.Name] = $property.Value
            }
        }
    }
    # Complete runtime overrides consume the existing UI environment as a set;
    # they never recover a missing DB path from the discarded JSON binding.
    if (-not $uiFromBinding) {
        foreach ($name in $allowed) {
            $value = [Environment]::GetEnvironmentVariable($name, 'Process')
            if ($value) { $environment[$name] = $value }
        }
    }
    foreach ($name in $environment.Keys) {
        $value = $environment[$name]
        if ($name -ceq 'BACKGROUND_TASK_CORE_AGENT_TASK_ID') {
            if ($value -isnot [string] -or $value -cnotmatch '\A[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*\z') {
                throw 'Agent task binding must be a registered repository/task ID'
            }
        } else { Assert-BackgroundTaskCoreAbsolutePath $value }
    }
    if (-not $environment.ContainsKey('BACKGROUND_TASK_CORE_DB') -or
        -not (Test-Path -LiteralPath $environment['BACKGROUND_TASK_CORE_DB'] -PathType Leaf)) {
        throw 'Binding must select the original existing BACKGROUND_TASK_CORE_DB; no database creation or migration'
    }
    foreach ($name in $names) { Assert-BackgroundTaskCoreAbsolutePath $runtime[$name] }
    $hasReader = $environment.ContainsKey('BACKGROUND_TASK_CORE_REMINDER_CLI')
    $hasWorkDb = $environment.ContainsKey('BACKGROUND_TASK_CORE_REMINDER_DB')
    if ($hasReader -ne $hasWorkDb) { throw 'Work view requires both reminder reader and database bindings' }
    if ($hasWorkDb -and -not (Test-Path -LiteralPath $environment['BACKGROUND_TASK_CORE_REMINDER_DB'] -PathType Leaf)) {
        throw 'Work database binding must select an existing database'
    }
    return @{Runtime=$runtime; Environment=$environment}
}

function Set-BackgroundTaskCoreWorkReader {
    # The binding's BACKGROUND_TASK_CORE_REMINDER_CLI is the work reader. Since the llmcall consolidation it
    # names the live schedule-reminder checkout's reminder.py, the same source the reminder ticks
    # run, so the console reads work through the code that writes it. It used to be overwritten here
    # with the installed generation's frozen reminder.py, which made the binding value inert.
    # A missing or non-file value fails loudly; it never falls back to a generation copy.
    param($Configuration, $Resolved)
    if (-not $Configuration.Environment.ContainsKey('BACKGROUND_TASK_CORE_REMINDER_CLI')) { return }
    $reader = $Configuration.Environment['BACKGROUND_TASK_CORE_REMINDER_CLI']
    Assert-BackgroundTaskCoreAbsolutePath $reader
    if (-not (Test-Path -LiteralPath $reader -PathType Leaf)) { throw 'Configured work reader is missing' }
    if ([IO.Path]::GetFileName($reader) -cne 'reminder.py') { throw 'Configured work reader must be reminder.py' }
}

function Assert-BackgroundTaskCoreWorkReady {
    param([string]$Url)
    $page = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 30
    $match = [regex]::Match($page.Content, '<meta name="console-token" content="([^"]+)">')
    if (-not $match.Success) { throw 'Console work readiness check: page token unavailable' }
    $headers = @{'X-Console-Token'=$match.Groups[1].Value}
    $work = Invoke-RestMethod -Uri ($Url.TrimEnd('/') + '/api/work') -Headers $headers -TimeoutSec 45
    if (-not $work -or -not $work.PSObject.Properties['available'] -or $work.available -ne $true) {
        throw 'Console work view is unavailable; inspect /api/work before treating startup as ready'
    }
}

# Compare only read-only OS metadata with the selected installed invocation.
function Get-BackgroundTaskCoreListenerIdentity {
    param([int]$Port, [string]$Python)
    try {
        $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
    } catch {
        if ($_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound*') { $listeners = @() }
        else { throw 'Listener inspection failed; refusing to start or reuse an unverified server' }
    }
    if (-not $listeners.Count) { return [pscustomobject]@{Status='absent'; ProcessId=$null} }
    $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    $matched = $false
    if ($owners.Count -eq 1 -and $Python) {
        try {
            $process = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($owners[0])" -ErrorAction Stop
            $invocationMatches = {
                param($Candidate, [string]$Executable)
                $expected = '^\s*(?:"' + [regex]::Escape($Executable) + '"|' + [regex]::Escape($Executable) +
                    ')\s+-P\s+-u\s+-m\s+background_task_core\.server\s+--port\s+' + $Port + '\s+--no-browser\s*$'
                return ($Candidate -and $Candidate.ExecutablePath -and $Candidate.CommandLine -and
                    [string]::Equals($Candidate.ExecutablePath, $Executable, [StringComparison]::OrdinalIgnoreCase) -and
                    [regex]::IsMatch($Candidate.CommandLine, $expected))
            }
            $matched = & $invocationMatches $process $Python
            if (-not $matched -and $process -and $process.ParentProcessId -gt 0) {
                # Windows venv python.exe is a live redirector; the base Python
                # child owns the socket. Require the exact selected parent and
                # unchanged invocation, including creation order to reject PID reuse.
                $parent = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($process.ParentProcessId)" -ErrorAction Stop
                $matched = $parent -and $parent.ProcessId -eq $process.ParentProcessId -and
                    $parent.CreationDate -is [datetime] -and $process.CreationDate -is [datetime] -and
                    $parent.CreationDate -le $process.CreationDate -and
                    (& $invocationMatches $parent $Python) -and
                    (& $invocationMatches $process $process.ExecutablePath)
            }
        } catch { $matched = $false }
    }
    $state = if ($matched) { 'matched' } else { 'mismatch' }
    return [pscustomobject]@{Status=$state; ProcessId=($owners -join ',')}
}

function Resolve-BackgroundTaskCoreArtifacts {
    param(
        [string]$Python = $env:BACKGROUND_TASK_CORE_PYTHON,
        [string]$ArtifactResolver = $env:BACKGROUND_TASK_CORE_ARTIFACT_RESOLVER,
        [string]$InstalledRoot = $env:BACKGROUND_TASK_CORE_INSTALLED_ROOT
    )
    foreach ($value in @($Python, $ArtifactResolver, $InstalledRoot)) {
        if (-not $value -or -not [IO.Path]::IsPathRooted($value)) { throw 'Complete absolute reviewed artifact binding required' }
    }
    foreach ($file in @($Python, $ArtifactResolver)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'Reviewed artifact binding file missing' }
    }
    $launcher = Join-Path $InstalledRoot 'launch.py'
    $raw = & $Python -I -B $launcher resolve
    if ($LASTEXITCODE -ne 0) { throw 'Installed artifacts could not be resolved' }
    $resolved = ($raw -join "`n") | ConvertFrom-Json
    $resolved.generation = $resolved.stage
    if (-not $resolved.python -or -not [IO.Path]::IsPathRooted($resolved.python) -or
        -not (Test-Path -LiteralPath $resolved.python -PathType Leaf) -or
        -not $resolved.packages -or -not $resolved.entrypoints) {
        throw 'Installed artifact closure missing'
    }
    return $resolved
}

function Resolve-BackgroundTaskCoreBinding {
    param(
        [string]$Python = $env:BACKGROUND_TASK_CORE_PYTHON,
        [string]$ArtifactResolver = $env:BACKGROUND_TASK_CORE_ARTIFACT_RESOLVER,
        [string]$InstalledRoot = $env:BACKGROUND_TASK_CORE_INSTALLED_ROOT,
        [string]$RuntimeConfig = $env:BACKGROUND_TASK_CORE_RUNTIME_CONFIG,
        [string]$PrivateRoot = $env:BACKGROUND_TASK_CORE_PRIVATE_ROOT,
        [string]$StateRoot = $env:BACKGROUND_TASK_CORE_STATE_ROOT,
        [string]$VaultRoot = $env:BACKGROUND_TASK_CORE_VAULT_ROOT,
        [switch]$ReadOnly
    )
    $values = @($Python, $ArtifactResolver, $InstalledRoot, $RuntimeConfig, $PrivateRoot, $StateRoot, $VaultRoot)
    foreach ($value in $values) {
        if (-not $value -or -not [IO.Path]::IsPathRooted($value)) { throw 'Complete absolute reviewed background-task-core binding required' }
    }
    foreach ($file in @($Python, $ArtifactResolver, $RuntimeConfig)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'Reviewed background-task-core binding file missing' }
    }
    $resolved = Resolve-BackgroundTaskCoreArtifacts -Python $Python -ArtifactResolver $ArtifactResolver -InstalledRoot $InstalledRoot
    if (-not $resolved.packages.PSObject.Properties['background-task-core'] -or
        -not $resolved.entrypoints.PSObject.Properties['background-task-core'] -or
        -not $resolved.entrypoints.PSObject.Properties['background-task-core-status']) {
        throw 'Installed background-task-core closure or entrypoints missing'
    }
    if ($ReadOnly) { return $resolved }
    $env:BACKGROUND_TASK_CORE_PYTHON = $resolved.python
    $env:BACKGROUND_TASK_CORE_ARTIFACT_RESOLVER = $ArtifactResolver
    $env:BACKGROUND_TASK_CORE_INSTALLED_ROOT = $InstalledRoot
    $env:BACKGROUND_TASK_CORE_RUNTIME_CONFIG = $RuntimeConfig
    $env:BACKGROUND_TASK_CORE_PRIVATE_ROOT = $PrivateRoot
    $env:BACKGROUND_TASK_CORE_STATE_ROOT = $StateRoot
    $env:BACKGROUND_TASK_CORE_VAULT_ROOT = $VaultRoot
    # Only installed import roots may supplement the artifact interpreter.
    $importRoots = @()
    if ($resolved.entrypoints.PSObject.Properties['reminder-linked-items']) {
        $entry = $resolved.entrypoints.'reminder-linked-items'
        if ($entry.import_root) { $importRoots += $entry.import_root }
    }
    $env:PYTHONPATH = $importRoots -join [IO.Path]::PathSeparator
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONSAFEPATH = '1'
    return $resolved
}
