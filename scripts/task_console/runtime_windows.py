"""Fixed, bounded COM/SecureString transport. Only explicit run requests start tasks."""
import base64
import json
import os
from pathlib import Path

from .registration import Conflict

LIMIT = 8 * 1024 * 1024


def split_arguments(command):
    """Decode Exec Arguments via CommandLineToArgvW without executing them."""
    if not isinstance(command, str) or '\x00' in command or len(command) > LIMIT:
        raise Conflict('invalid_argv_quoting', 'scheduler')
    if os.name != 'nt':
        raise Conflict('native_argv_parser_required', 'scheduler')
    import ctypes
    from ctypes import wintypes
    native = ctypes.WinDLL('shell32', use_last_error=True).CommandLineToArgvW
    native.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    native.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = ctypes.WinDLL('kernel32', use_last_error=True).LocalFree
    free.argtypes = [wintypes.HLOCAL]
    free.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    # argv[0] has special parsing rules; supply a fixed executable placeholder.
    values = native('task-console.exe ' + command, ctypes.byref(count))
    if not values:
        raise Conflict('native_argv_parser_failed', 'scheduler')
    try:
        return [values[i] for i in range(1, count.value)]
    finally:
        free(ctypes.cast(values, wintypes.HLOCAL))


def powershell(script, request, *, timeout=25):
    """Only bundled source is code. All caller values cross stdin as JSON data."""
    if os.name != 'nt':
        raise Conflict('windows_required', 'transport')
    executable = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    data = json.dumps(request, ensure_ascii=True, allow_nan=False).encode('ascii')
    if len(data) > LIMIT:
        raise Conflict('request_limit', 'transport')
    try:
        from llmcall import process as shared_process
        captured = shared_process.run(
            [str(executable), '-NoProfile', '-NonInteractive', '-InputFormat', 'Text',
             '-OutputFormat', 'Text', '-EncodedCommand', command],
            data, timeout, context=shared_process.resolve_context(),
            max_input_bytes=LIMIT, max_output_bytes=LIMIT,
        )
        if captured.error or captured.outcome != 'success':
            code = {
                'input_limit': 'request_limit', 'invalid_timeout': 'transport_timeout',
                'timeout': 'transport_timeout', 'cancelled': 'transport_cancelled',
                'cleanup_failed': 'transport_cleanup_failed',
            }.get(captured.error, 'bounded_transport_failed')
            raise Conflict(code, 'transport')
        if (captured.returncode != 0 or captured.stderr_bytes != b''
                or captured.stdout_bytes is None):
            raise Conflict('bounded_transport_failed', 'transport')
        result = json.loads(captured.stdout_bytes.decode('utf-8-sig'))
        if not isinstance(result, dict) or result.get('ok') is not True:
            code = result.get('code') if isinstance(result, dict) else None
            if code in ('busy', 'auth_pending', 'scheduler_changed', 'task_exists', 'unsupported_operation'):
                raise Conflict(code, 'scheduler')
            if code == 'scheduler_failure' and result.get('stage') in ('connect', 'folder', 'definition', 'xml', 'operation'):
                raise Conflict('scheduler_failure', result['stage'])
            raise ValueError()
        return result
    except Conflict:
        raise
    except Exception:
        raise Conflict('bounded_transport_failed', 'transport') from None


_PROTECT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
  $r = [Console]::In.ReadToEnd() | ConvertFrom-Json
  if ($r.text -isnot [string]) { throw 'type' }
  # UTF-16LE bytes protected with CurrentUser/null entropy are the unkeyed
  # ConvertFrom-SecureString .cred format, without SecureString's 65536 limit.
  # Reserve framing and DPAPI overhead inside the unchanged 8 MiB transport.
  $plainLimit = 2096128
  $cipherLimit = 8387584
  Add-Type -AssemblyName System.Security
  $scope = [Security.Cryptography.DataProtectionScope]::CurrentUser
  $unicode = New-Object System.Text.UnicodeEncoding($false, $false, $true)
  if ($r.operation -eq 'protect') {
    if ($r.text.Length -gt $plainLimit) { throw 'limit' }
    $bytes = $unicode.GetBytes($r.text)
    try {
      $cipher = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, $scope)
      $result = [BitConverter]::ToString($cipher).Replace('-', '').ToLowerInvariant()
      if ($result.Length -gt $cipherLimit) { throw 'limit' }
    } finally { if ($null -ne $bytes) { [Array]::Clear($bytes, 0, $bytes.Length) } }
  } elseif ($r.operation -eq 'unprotect') {
    if ($r.text.Length -gt $cipherLimit -or $r.text.Length -eq 0 -or
        $r.text.Length % 2 -ne 0 -or $r.text -cmatch '[^0-9a-fA-F]') { throw 'cipher' }
    $cipher = [Runtime.Remoting.Metadata.W3cXsd2001.SoapHexBinary]::Parse($r.text).Value
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect($cipher, $null, $scope)
    try {
      if ($bytes.Length % 2 -ne 0 -or $bytes.Length -gt (2 * $plainLimit)) { throw 'limit' }
      $result = $unicode.GetString($bytes)
    } finally { if ($null -ne $bytes) { [Array]::Clear($bytes, 0, $bytes.Length) } }
  } else { throw 'operation' }
  @{ok=$true; text=$result} | ConvertTo-Json -Compress
} catch { @{ok=$false; code='protector_failed'} | ConvertTo-Json -Compress }
'''


class DPAPIProtector:
    """CurrentUser DPAPI in CONFIG's unkeyed SecureString .cred byte format."""
    # ASCII envelope -> UTF-16LE -> DPAPI -> hex: approximately 4x expansion.
    # Match _PROTECT; reserve 4096 bytes for DPAPI overhead and JSON framing.
    PLAIN_LIMIT = (LIMIT - 4096) // 4
    CIPHER_LIMIT = LIMIT - 1024

    @staticmethod
    def _validate(data, *, cipher):
        if not isinstance(data, bytes):
            raise Conflict('protector_invalid', 'vault')
        limit = DPAPIProtector.CIPHER_LIMIT if cipher else DPAPIProtector.PLAIN_LIMIT
        if len(data) > limit:
            raise Conflict('protector_limit', 'vault')
        try:
            text = data.decode('ascii')
        except UnicodeError:
            raise Conflict('protector_invalid', 'vault') from None
        if cipher and (not data or len(data) % 2 or
                       data.translate(None, b'0123456789abcdefABCDEF')):
            raise Conflict('protector_invalid', 'vault')
        return text

    def _convert(self, operation, data):
        text = self._validate(data, cipher=operation == 'unprotect')
        result = powershell(_PROTECT, {'operation': operation, 'text': text})
        if not isinstance(result, dict) or not isinstance(result.get('text'), str):
            raise Conflict('protector_invalid', 'vault')
        try:
            output = result['text'].encode('ascii')
        except UnicodeError:
            raise Conflict('protector_invalid', 'vault') from None
        self._validate(output, cipher=operation == 'protect')
        return output

    def protect(self, data):
        return self._convert('protect', data)

    def unprotect(self, data):
        return self._convert('unprotect', data)


_SCHEDULER = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
  $stage = 'connect'
  $r = [Console]::In.ReadToEnd() | ConvertFrom-Json
  if ($r.schemaVersion -ne 1 -or $r.TaskPath -cne '\' -or
      [string]::IsNullOrWhiteSpace($r.TaskName) -or $r.TaskName -match '[\\/\x00-\x1f*?\[\]]') { throw 'scope' }
  $service = New-Object -ComObject 'Schedule.Service'
  $service.Connect()
  $stage = 'folder'
  if ($r.operation -ne 'prepare') { $folder = $service.GetFolder('\') }
  function Read-Exact {
    try { $task = $folder.GetTask([string]$r.TaskName) }
    catch {
      $failure = $_.Exception
      while ($null -ne $failure.InnerException) { $failure = $failure.InnerException }
      if ($failure.HResult -eq -2147024894) { return $null }
      throw
    }
    if ($task.Path -ine ('\' + [string]$r.TaskName) -or $task.State -eq 0) { throw 'scope' }
    return $task
  }
  function Reply-Task($task) {
    if ($null -eq $task) { return @{state='absent'} }
    return @{state='present'; xml=[string]$task.Xml; enabled=[bool]$task.Enabled;
             running=([int]$task.State -in @(2,4) -or $task.GetInstances(0).Count -gt 0);
             path=[string]$task.Path}
  }
  if ($r.operation -eq 'query') {
    @{ok=$true; observation=(Reply-Task (Read-Exact))} | ConvertTo-Json -Depth 20 -Compress
  } elseif ($r.operation -in @('run', 'stop', 'enable', 'disable')) {
    $stage = 'operation'
    $task = Read-Exact
    if ($null -eq $task -or $r.expected.state -ne 'present' -or
        [string]$task.Xml -cne [string]$r.expected.value.xml -or
        [bool]$task.Enabled -ne [bool]$r.expected.value.enabled) {
      @{ok=$false; code='scheduler_changed'} | ConvertTo-Json -Compress
    } else {
      $before = Reply-Task $task
      if ($r.operation -eq 'run') {
        if (-not $before.enabled -or $before.running) {
          @{ok=$false; code='busy'} | ConvertTo-Json -Compress
          exit
        }
        # This is the only payload-starting operation; query/prepare/publish
        # never call it. The caller must explicitly request run under authority.
        $instance = $task.Run($null)
        if ($null -eq $instance) { throw 'missing run acknowledgement' }
      } elseif ($r.operation -eq 'stop') {
        if ($before.running) { $task.Stop(0) }
      } else {
        $task.Enabled = ($r.operation -eq 'enable')
      }
      $after = Reply-Task (Read-Exact)
      if ($r.operation -eq 'stop') {
        $watch = [Diagnostics.Stopwatch]::StartNew()
        while ($after.state -eq 'present' -and $after.running -and $watch.ElapsedMilliseconds -lt 2000) {
          Start-Sleep -Milliseconds 100
          $after = Reply-Task (Read-Exact)
        }
      }
      if ($after.state -ne 'present') { throw 'missing control readback' }
      if ($r.operation -in @('run', 'stop') -and
          ([string]$after.xml -cne [string]$before.xml -or $after.enabled -ne $before.enabled)) {
        throw 'changed control readback'
      }
      @{ok=$true; acknowledged=$true; running=[bool]$after.running;
        enabled=[bool]$after.enabled} | ConvertTo-Json -Compress
    }
  } elseif ($r.operation -eq 'prepare') {
    $stage = 'definition'
    $definition = $service.NewTask(0)
    $stage = 'xml'
    $definition.XmlText = [string]$r.xml
    if ([int]$definition.Principal.LogonType -in @(1,6)) {
      @{ok=$false; code='auth_pending'} | ConvertTo-Json -Compress
    } else {
      @{ok=$true; xml=[string]$definition.XmlText} | ConvertTo-Json -Compress
    }
  } elseif ($r.operation -eq 'publish') {
    $task = Read-Exact
    if ($null -ne $task -and ([int]$task.State -in @(2,4) -or $task.GetInstances(0).Count -gt 0)) {
      @{ok=$false; code='busy'} | ConvertTo-Json -Compress
    } elseif (($r.expected.state -eq 'absent' -and $null -ne $task) -or
              ($r.expected.state -eq 'present' -and ($null -eq $task -or
               [string]$task.Xml -cne [string]$r.expected.value.xml -or
               [bool]$task.Enabled -ne [bool]$r.expected.value.enabled))) {
      @{ok=$false; code='scheduler_changed'} | ConvertTo-Json -Compress
    } elseif ($r.desired.state -eq 'absent') {
      if ($null -ne $task) { $folder.DeleteTask([string]$r.TaskName, 0) }
      @{ok=$true} | ConvertTo-Json -Compress
    } else {
      $definition = $service.NewTask(0)
      $definition.XmlText = [string]$r.desired.value.xml
      if ([int]$definition.Principal.LogonType -in @(1,6)) {
        @{ok=$false; code='auth_pending'} | ConvertTo-Json -Compress
      } else {
        # TASK_CREATE or TASK_UPDATE, never CREATE_OR_UPDATE. Suppress registration triggers.
        $flags = 4 + 32
        if ($r.expected.state -eq 'absent') { $flags = 2 + 32 }
        $registered = $folder.RegisterTaskDefinition([string]$r.TaskName, $definition,
            $flags, [string]$definition.Principal.UserId, $null, [int]$definition.Principal.LogonType, $null)
        @{ok=$true; observation=(Reply-Task $registered)} | ConvertTo-Json -Depth 20 -Compress
      }
    }
  } else { @{ok=$false; code='unsupported_operation'} | ConvertTo-Json -Compress }
} catch { @{ok=$false; code='scheduler_failure'; stage=$stage} | ConvertTo-Json -Compress }
'''


class COMTransport:
    """Fixed root-task registration and explicit bounded controls; no enumeration."""
    concrete = True
    native_normalization = True
    def __call__(self, operation, request):
        from .runtime_xml import normalize, observation
        if operation == 'cleanup':
            return {'ok': True}  # Preparation owns no Scheduler object.
        if operation == 'prepare':
            if request['value'] is None:
                return {'ok': True, 'snapshot': {'state': 'absent'}}
            value = normalize(request['value'])
            reply = powershell(_SCHEDULER, {**request, 'operation': operation, 'xml': value['xml']})
            value['xml'] = reply['xml']
            return {'ok': True, 'snapshot': observation(request['TaskName'], value)}
        reply = powershell(_SCHEDULER, {**request, 'operation': operation})
        if operation == 'query':
            raw = reply['observation']
            if raw['state'] == 'absent':
                return {'ok': True, 'snapshot': raw}
            if raw['path'].casefold() != ('\\' + request['TaskName']).casefold():
                raise Conflict('scheduler_scope_mismatch', 'scheduler')
            return {'ok': True, 'snapshot': observation(request['TaskName'], raw)}
        return reply
