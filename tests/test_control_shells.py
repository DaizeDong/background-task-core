"""Compatibility shells invoke only a disposable argv/JSON recorder here."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def invoke(tmp_path, script, arguments=(), *, code=0, name='AcmeSync', verb='stop'):
    recorder = tmp_path / 'recorder.ps1'
    recorder.write_text('''[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$body = @($input) -join "`n"
@{schemaVersion=1; arguments=@($args); input=$body} | ConvertTo-Json -Compress
exit ([int]$env:SYNTHETIC_EXIT)
''', encoding='utf-8')
    env = {key: value for key, value in os.environ.items() if not key.startswith(('TASK_CONSOLE_', 'TASKCONSOLE_'))}
    for key in ('HOME', 'USERPROFILE', 'TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA'):
        env[key] = str(tmp_path)
    env.update(SYNTHETIC_EXIT=str(code), TASKCONSOLE_NAME=name, TASKCONSOLE_VERB=verb)
    result = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-File', str(script),
                             '-Python', str(recorder), *arguments], env=env, cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    return result.returncode, json.loads(result.stdout)


@pytest.mark.parametrize('relative', [
    'tools/register-profile-sync.ps1',
    'claude/scripts/register-homecoming-task.ps1',
])
def test_registration_shell_preview_and_exact_apply(tmp_path, relative, config_source):
    script = config_source / relative
    code, result = invoke(tmp_path, script, ['-TaskId', 'acme-maintenance/sync', '-Migrate'])
    assert code == 0
    assert result['arguments'] == ['-m', 'task_console', 'registration-plan', '--task-id', 'acme-maintenance/sync', '--migrate']
    code, result = invoke(tmp_path, script, ['-Apply', '-Request', 'synthetic plan.json', '-ExpectedRevision', 'sha256:synthetic'], code=3)
    assert code == 3
    assert result['arguments'] == ['-m', 'task_console', 'apply', '--request', 'synthetic plan.json', '--expected-revision', 'sha256:synthetic']


@pytest.mark.parametrize('name', ['AcmeSync', 'Acme "quoted"; $literal', 'Acme\u96ea'])
def test_act_shell_uses_json_and_preserves_binary_failure(tmp_path, name):
    code, result = invoke(tmp_path, ROOT / 'scripts/task_console/act.ps1', name=name, code=3)
    assert code == 1
    assert result['arguments'] == ['-m', 'task_console', 'control']
    assert json.loads(result['input']) == {'name': name, 'verb': 'stop'}


def test_configured_authority_arguments_are_forwarded(tmp_path):
    values = ['-RuntimeConfig', str(tmp_path / 'private/config.json'), '-PrivateRoot', str(tmp_path / 'private'),
              '-StateRoot', str(tmp_path / 'state'), '-VaultRoot', str(tmp_path / 'vault')]
    code, result = invoke(tmp_path, ROOT / 'scripts/task_console/act.ps1', values)
    assert code == 0
    assert result['arguments'][3:] == [
        '--runtime-config', values[1], '--private-root', values[3], '--state-root', values[5], '--vault-root', values[7]]


def test_shells_have_no_scheduler_or_installation_implementation(config_source):
    for path in [ROOT / 'scripts/task_console/act.ps1', config_source / 'tools/register-profile-sync.ps1',
                 config_source / 'claude/scripts/register-homecoming-task.ps1']:
        source = path.read_text(encoding='utf-8')
        assert 'ScheduledTask' not in source
        assert 'Schedule.Service' not in source
        assert 'Copy-Item' not in source and 'New-Item' not in source


def test_shell_returns_json_when_controller_is_missing(tmp_path):
    missing = tmp_path / 'missing-python.exe'
    env = {key: value for key, value in os.environ.items() if not key.startswith(('TASK_CONSOLE_', 'TASKCONSOLE_'))}
    env.update(TASKCONSOLE_NAME='AcmeSync', TASKCONSOLE_VERB='stop', USERPROFILE=str(tmp_path), HOME=str(tmp_path))
    reply = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-File',
                            str(ROOT / 'scripts/task_console/act.ps1'), '-Python', str(missing)],
                           env=env, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert reply.returncode == 1
    result = json.loads(reply.stdout)
    assert result['ok'] is False and result['error']['code'] == 'controller_unavailable'


def test_fixed_com_script_parses_without_execution(tmp_path):
    from task_console.runtime_windows import _SCHEDULER
    source = tmp_path / 'fixed-com.ps1'
    source.write_text(_SCHEDULER, encoding='utf-8')
    parser = tmp_path / 'parse-only.ps1'
    parser.write_text('''param([string]$Source)
$tokens = $null; $errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
@{errors=@($errors).Count} | ConvertTo-Json -Compress
if (@($errors).Count) { exit 1 }
''', encoding='utf-8')
    reply = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-File', str(parser), '-Source', str(source)],
                           cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert reply.returncode == 0 and json.loads(reply.stdout)['errors'] == 0
    assert _SCHEDULER.count('$task.Run($null)') == 1
    assert "$service.GetFolder('\\')" in _SCHEDULER
    assert '$folder.GetTask([string]$r.TaskName)' in _SCHEDULER
