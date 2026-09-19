"""Actual backup-check facade and isolated TASK block; no live home or Scheduler."""
import json
from pathlib import Path

import pytest

from test_export_restore_shells import CONFIG, environment, run


def summary():
    return {'schemaVersion': 1, 'operation': 'backup-check', 'status': 'COMPLETED',
            'ok': True, 'read_only': True, 'scope_count': 4, 'checked_count': 3,
            'present_count': 2, 'disabled_count': 1, 'absent_count': 1, 'excluded_count': 1}


def test_facade_uses_only_resolved_interpreter_and_binding(tmp_path):
    env, _ = environment(tmp_path)
    (tmp_path / 'response.json').write_text(json.dumps(summary()))
    target = tmp_path / 'backup'
    result = run(tmp_path, CONFIG / 'tools/check-task-backup.ps1', ['-BackupDirectory', str(target)], env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == summary()
    calls = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    assert len(calls) == 2
    assert calls[0]['arguments'] == ['-I', '-B', str(tmp_path / 'resolver.py'), '--resolve', '--root', env['TASK_CONSOLE_INSTALLED_ROOT']]
    assert calls[1]['arguments'] == ['-B', '-P', '-m', 'task_console.task_backup_check', '--backup-directory', str(target)]
    assert calls[1]['config'] == env['TASK_CONSOLE_RUNTIME_CONFIG']
    assert not target.exists()


@pytest.mark.parametrize('failure', ['unconfigured', 'resolver', 'nonzero', 'corrupt', 'failed', 'empty', 'partial', 'bool_count', 'missing_count'])
def test_facade_refuses_failed_or_incomplete_evidence_without_leak(tmp_path, failure):
    env, _ = environment(tmp_path)
    response = summary()
    if failure == 'unconfigured':
        for key in list(env):
            if key.startswith('TASK_CONSOLE'):
                del env[key]
    elif failure == 'resolver':
        (tmp_path / 'resolution.json').write_text('synthetic-secret-content')
    elif failure == 'nonzero':
        env['SYNTHETIC_EXIT'] = '3'
    elif failure == 'failed':
        response['ok'] = False
    elif failure == 'empty':
        response.update({key: 0 for key in response if key.endswith('_count')})
    elif failure == 'partial':
        response['checked_count'] = 1
    elif failure == 'bool_count':
        response['disabled_count'] = True
    elif failure == 'missing_count':
        del response['excluded_count']
    payload = 'synthetic-secret-content' if failure == 'corrupt' else json.dumps(response)
    (tmp_path / 'response.json').write_text(payload)
    result = run(tmp_path, CONFIG / 'tools/check-task-backup.ps1', ['-BackupDirectory', str(tmp_path / 'backup')], env)
    assert result.returncode != 0
    assert json.loads(result.stdout)['ok'] is False
    assert 'synthetic-secret-content' not in result.stdout + result.stderr


@pytest.mark.parametrize('failure', [False, True])
def test_actual_task_block_reports_declared_checked_and_excluded(tmp_path, failure):
    env, _ = environment(tmp_path)
    (tmp_path / 'response.json').write_text(json.dumps(summary()))
    if failure:
        env['SYNTHETIC_EXIT'] = '3'
    source = (CONFIG / 'tools/check-drift.ps1').read_text(encoding='utf-8-sig')
    block = source.split('# ---------- 2. TASK ----------')[1].split('# ---------- 3. SURFACE ----------')[0]
    repo = tmp_path / 'config'
    for name in ('tools/check-task-backup.ps1', 'claude/scripts/task-console-binding.ps1', 'sync-from-local.ps1'):
        destination = repo / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((CONFIG / name).read_bytes())
    harness = tmp_path / 'task-block.ps1'
    harness.write_text('''param([string]$RepoRoot)
$ErrorActionPreference='Stop'
$fail = New-Object System.Collections.Generic.List[string]
function Get-ScheduledTask { throw 'Separate scheduler query forbidden' }
''' + block + '''
@{failures=@($fail.ToArray()); coverage=$taskTerm} | ConvertTo-Json -Compress
''', encoding='utf-8')
    result = run(tmp_path, harness, ['-RepoRoot', str(repo)], env)
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads(result.stdout)
    if failure:
        assert len(output['failures']) == 1 and output['coverage'] == 'unverified'
    else:
        assert output['failures'] == []
        assert output['coverage'] == 'declared=4/checked=3/present=2/disabled=1/absent=1/excluded=1'
