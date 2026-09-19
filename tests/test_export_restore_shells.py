"""PowerShell startup/backup boundaries with only recorder processes and fake APIs."""
import json
import os
from pathlib import Path
import subprocess

import pytest

RUN = Path(__file__).resolve().parents[2]
PS = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def environment(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('TASK_CONSOLE', 'TASKCONSOLE'))}
    for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP'):
        env[key] = str(tmp_path)
    runtime = tmp_path / 'recorder.ps1'
    runtime.write_text('''$arguments = @($args)
@{arguments=$arguments; python=$env:TASK_CONSOLE_PYTHON; config=$env:TASK_CONSOLE_RUNTIME_CONFIG;
private=$env:TASK_CONSOLE_PRIVATE_ROOT; state=$env:TASK_CONSOLE_STATE_ROOT; vault=$env:TASK_CONSOLE_VAULT_ROOT} |
ConvertTo-Json -Compress | Add-Content -LiteralPath $env:SYNTHETIC_CALLS
if ($arguments -contains '--resolve') { Get-Content -LiteralPath $env:SYNTHETIC_RESOLUTION -Raw; exit 0 }
Get-Content -LiteralPath $env:SYNTHETIC_RESPONSE -Raw
exit ([int]$env:SYNTHETIC_EXIT)
''', encoding='utf-8')
    for name in ('private', 'state', 'vault', 'installed'):
        (tmp_path / name).mkdir()
    for name in ('resolver.py', 'private/config.json'):
        (tmp_path / name).write_text('{}', encoding='utf-8')
    resolution = dict(python=str(runtime), generation=str(tmp_path / 'installed'),
                      packages={'task-console': 'synthetic'},
                      entrypoints={'task-console': {}, 'task-console-status': {}})
    (tmp_path / 'resolution.json').write_text(json.dumps(resolution), encoding='utf-8')
    archive = {'schemaVersion': 1, 'operation': 'export', 'status': 'COMPLETED', 'ok': True,
               'generation': 'synthetic', 'receipt_revision': 'sha256:synthetic', 'tasks': [
                   {'task_id': 'acme/sync', 'name': 'AcmeSync', 'task_path': '\\', 'status': 'COMPLETED',
                    'enabled': False, 'snapshot': {'value': {'xml': '<Task><Settings><Enabled>false</Enabled></Settings></Task>'}}}], 'excluded': []}
    (tmp_path / 'response.json').write_text(json.dumps(archive), encoding='utf-8')
    env.update(TASK_CONSOLE_PYTHON=str(runtime), TASK_CONSOLE_ARTIFACT_RESOLVER=str(tmp_path / 'resolver.py'),
               TASK_CONSOLE_INSTALLED_ROOT=str(tmp_path / 'installed'), TASK_CONSOLE_RUNTIME_CONFIG=str(tmp_path / 'private/config.json'),
               TASK_CONSOLE_PRIVATE_ROOT=str(tmp_path / 'private'), TASK_CONSOLE_STATE_ROOT=str(tmp_path / 'state'),
               TASK_CONSOLE_VAULT_ROOT=str(tmp_path / 'vault'), SYNTHETIC_CALLS=str(tmp_path / 'calls.jsonl'),
               SYNTHETIC_RESOLUTION=str(tmp_path / 'resolution.json'), SYNTHETIC_RESPONSE=str(tmp_path / 'response.json'),
               SYNTHETIC_EXIT='0', TASK_CONSOLE_STATUS_SNAPSHOT='synthetic-status', TASK_CONSOLE_CATALOG_SNAPSHOT='synthetic-catalog')
    return env, archive


def run(tmp_path, script, arguments, env):
    return subprocess.run([str(PS), '-NoProfile', '-NonInteractive', '-File', str(script), *arguments],
                          cwd=tmp_path, env=env, text=True, encoding='utf-8', capture_output=True, timeout=30)


@pytest.mark.parametrize('failure', [False, True])
def test_backup_publishes_only_completed_snapshot(tmp_path, failure, config_source):
    env, archive = environment(tmp_path)
    target = tmp_path / 'scheduled-tasks'
    target.mkdir()
    (target / 'old.xml').write_text('synthetic previous generation')
    if failure:
        env['SYNTHETIC_EXIT'] = '3'
    result = run(tmp_path, config_source / 'tools/export-task-snapshot.ps1', ['-Destination', str(target)], env)
    if failure:
        assert result.returncode != 0
        assert (target / 'old.xml').read_text() == 'synthetic previous generation'
        assert not list(tmp_path.glob('.task-export-*'))
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads((target / 'receipt.json').read_text()) == archive
        assert len(list(target.glob('*.xml'))) == 1 and not (target / 'old.xml').exists()
        assert next(tmp_path.glob('.task-export-previous-*/old.xml')).read_text() == 'synthetic previous generation'
    calls = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    assert calls[0]['arguments'] == ['-I', '-B', str(tmp_path / 'resolver.py'), '--resolve', '--root', str(tmp_path / 'installed')]
    assert calls[1]['arguments'] == ['-P', '-m', 'task_console', 'export']
    assert calls[1]['config'] == env['TASK_CONSOLE_RUNTIME_CONFIG']


def test_launcher_binds_installed_runtime_and_keeps_read_only_modes(tmp_path, config_source):
    env, _ = environment(tmp_path)
    harness = tmp_path / 'launch.ps1'
    harness.write_text('''param([string]$Launcher, [string]$Mode)
$ErrorActionPreference='Stop'
$global:started=$false
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) if ($global:started) { [pscustomobject]@{OwningProcess=1234} } }
function Start-Process { param($FilePath,$WorkingDirectory,[switch]$PassThru,$WindowStyle,$ArgumentList,$RedirectStandardOutput,$RedirectStandardError)
 if ($PassThru) {
  @{file=$FilePath; arguments=$ArgumentList; window=$WindowStyle; config=$env:TASK_CONSOLE_RUNTIME_CONFIG;
    private=$env:TASK_CONSOLE_PRIVATE_ROOT; state=$env:TASK_CONSOLE_STATE_ROOT; vault=$env:TASK_CONSOLE_VAULT_ROOT;
    python=$env:TASK_CONSOLE_PYTHON; status=$env:TASK_CONSOLE_STATUS_SNAPSHOT; catalog=$env:TASK_CONSOLE_CATALOG_SNAPSHOT} |
    ConvertTo-Json -Compress | Set-Content -LiteralPath $env:SYNTHETIC_LAUNCH
  $global:started=$true
  [pscustomobject]@{Id=1234; HasExited=$false}
 }
}
function Start-Sleep { param($Milliseconds) }
function Stop-Process { throw 'no actual process exists' }
if ($Mode -eq 'status') { & $Launcher -Status }
elseif ($Mode -eq 'stop') { & $Launcher -Stop }
else { & $Launcher -Tab }
''', encoding='utf-8')
    env['SYNTHETIC_LAUNCH'] = str(tmp_path / 'launch.json')
    launcher = str(config_source / 'claude/scripts/task-console.ps1')
    result = run(tmp_path, harness, ['-Launcher', launcher], env)
    assert result.returncode == 0, result.stdout + result.stderr
    launch = json.loads((tmp_path / 'launch.json').read_text(encoding='utf-8-sig'))
    assert launch['file'] == launch['python'] == env['TASK_CONSOLE_PYTHON']
    assert launch['window'] == 'Hidden'
    assert launch['arguments'] == ['-P', '-u', '-m', 'task_console.launch_server', '--port', '8787', '--no-browser']
    for key in ('config', 'private', 'state', 'vault'):
        assert launch[key]
    assert launch['status'] == 'synthetic-status' and launch['catalog'] == 'synthetic-catalog'
    before = (tmp_path / 'calls.jsonl').read_bytes()
    for mode in ('status', 'stop'):
        assert run(tmp_path, harness, ['-Launcher', launcher, '-Mode', mode], env).returncode == 0
    assert (tmp_path / 'calls.jsonl').read_bytes() == before


def test_installer_has_no_outer_scheduler_mutation(config_source):
    proposed = (config_source / 'install.ps1').read_text(encoding='utf-8-sig')
    assert 'Register-LegacyRestoreTask' not in proposed and 'Register-ScheduledTask' not in proposed
    assert 'restore-task-snapshot.ps1' in proposed and "status -ne 'COMPLETED'" in proposed
    source = (config_source / 'sync-from-local.ps1').read_text(encoding='utf-8-sig')
    assert '$TaskNames = @(' not in source and 'Export-ScheduledTask' not in source


def test_linked_reader_requires_real_owner_attestation(monkeypatch):
    import importlib.util
    import sqlite3
    import uuid
    monkeypatch.syspath_prepend(str(RUN / 'schedule-reminder/skills/schedule-reminder/scripts'))
    monkeypatch.syspath_prepend(str(RUN / 'task-console/scripts'))
    import store
    import reminder_link_review as owner
    from task_console import linked_items
    spec = importlib.util.spec_from_file_location('synthetic_link_fixtures', RUN / 'task-console/tools/make_fixtures.py')
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    root = Path(os.environ['TEMP']) / ('link-seam-' + uuid.uuid4().hex)
    root.mkdir(parents=True)
    path = root / 'synthetic.sqlite3'
    store.init_db(str(path))
    item = store.add_item('SYNTHETIC_LINK_SEAM', db_path=str(path))['id']
    task = 'acme-maintenance/sync'
    monkeypatch.setenv('TASK_CONSOLE_REMINDER_DB', str(path))
    assert linked_items.read_linked_items([task])['code'] == 'linked_schema_unreviewed'
    request = fixtures.example_request()
    review = owner.build_review(db_path=path, task_request=request, decisions={
        'schemaVersion': 1, 'links': {item: task}, 'unmapped_items': 'reviewed-unlinked'})
    owner.apply_review(review, review['review_revision'], db_path=path, task_request=request)
    with sqlite3.connect(path) as conn:
        before = list(conn.iterdump())
    assert linked_items.read_linked_items([task]) == {
        'status': 'available', 'items': {task: [{'id': item, 'state': 'pending'}]}}
    with sqlite3.connect(path) as conn:
        assert list(conn.iterdump()) == before


@pytest.mark.parametrize('failure', [None, 'legacy_xml', 'disabled_state', 'missing_task'])
def test_restore_shell_consumes_only_completed_per_task_receipts(tmp_path, failure, config_source):
    env, _ = environment(tmp_path)
    task = {'task_id': 'acme/sync', 'name': 'AcmeSync', 'enabled': False}
    plan = {'schemaVersion': 1, 'intent': {'operation': 'restore'}, 'plan_revision': 'sha256:synthetic',
            'task_ids': ['acme/sync'], 'authority_epoch': 0, 'changes': {'tasks': [task]}}
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan), encoding='utf-8')
    receipt = {'schemaVersion': 1, 'operation': 'restore', 'status': 'COMPLETED', 'ok': True,
               'approval_revision': plan['plan_revision'], 'generation': 'synthetic-tx', 'tasks_registered': True,
               'authority_epoch': 0, 'migrated_tasks': [], 'tasks': [{**task, 'task_path': '\\', 'identity': 'synthetic-definition',
                   'status': 'COMPLETED', 'writer': 'legacy', 'transaction_id': 'synthetic-tx'}]}
    if failure == 'legacy_xml':
        receipt['legacy_xml'] = [{'name': 'AcmeSync', 'xml': '<Task/>'}]
    elif failure == 'disabled_state':
        receipt['tasks'][0]['enabled'] = True
    elif failure == 'missing_task':
        receipt['tasks'] = []
    (tmp_path / 'response.json').write_text(json.dumps(receipt), encoding='utf-8')
    result = run(tmp_path, config_source / 'tools/restore-task-snapshot.ps1',
                 ['-Plan', str(path), '-ApprovalRevision', plan['plan_revision']], env)
    assert (result.returncode == 0) is (failure is None), result.stdout + result.stderr
    if failure is None:
        assert json.loads(result.stdout) == receipt
