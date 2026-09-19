"""Periodic authority and catalog checks using the public synthetic generator."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from test_observation_collection import case, NOW, TASK, write
from task_console import observations, registration
from task_console.runtime_storage import ResourceLocks


@pytest.fixture
def live(case, monkeypatch):
    config, path, request, capture = case
    config.pop('request')
    root = Path(config['private_dir']).parent
    config['runtime'] = {key: str(root / key) for key in
                        ('runtime_config', 'private_root', 'state_root', 'vault_root')}
    write(Path(config['runtime']['runtime_config']), {'synthetic': True})
    write(path, config)
    request['machine'].update(authority_epoch=1, migrated_tasks=[TASK])
    bundle = {'request': request, 'authority_generation': 'a' * 32}
    runtime = SimpleNamespace(load=lambda: deepcopy(bundle),
                              locks=ResourceLocks(root / 'locks'),
                              journal=SimpleNamespace(pending=lambda keys: []))
    monkeypatch.setattr(observations, 'load_runtime', lambda binding: runtime, raising=False)
    return config, path, bundle, capture, runtime


def test_each_pass_uses_current_authority_and_preserves_migration(live):
    config, path, bundle, capture, _ = live
    original = deepcopy(bundle)
    first = observations.publish(path, capture, now=NOW)
    snapshot = observations.read_json(config['snapshot'])
    assert first['authority_generation'] == snapshot['authority_generation'] == 'a' * 32
    assert snapshot['input_revision'] == registration.revision(bundle['request'])
    assert snapshot['authority_epoch'] == 1
    assert bundle == original
    bundle['authority_generation'] = 'b' * 32
    bundle['request']['machine']['authority_epoch'] = 2
    second = observations.publish(path, capture, now=NOW + 1)
    assert second['authority_generation'] == 'b' * 32
    assert observations.read_json(config['snapshot'])['authority_epoch'] == 2


@pytest.mark.parametrize('change', ['generation', 'revision', 'pending', 'binding', 'runtime_config', 'worker'])
def test_changed_inputs_never_replace_previous_snapshot(live, change):
    config, path, bundle, capture, runtime = live
    observations.publish(path, capture, now=NOW)
    before = Path(config['snapshot']).read_bytes()

    def race(binding, facts, **kwargs):
        result = observations.collect(binding, facts, **kwargs)
        if change == 'generation':
            bundle['authority_generation'] = 'b' * 32
        elif change == 'revision':
            bundle['request']['bindings']['tasks'][TASK]['enabled'] = False
        elif change == 'pending':
            runtime.journal.pending = lambda keys: ['pending']
        elif change == 'binding':
            write(path, {**config, 'max_age_seconds': 600})
        elif change == 'runtime_config':
            write(Path(config['runtime']['runtime_config']), {'synthetic': 'changed'})
        else:
            result['input_revision'] = 'sha256:' + '0' * 64
        return result

    with pytest.raises(ValueError):
        observations.publish(path, capture, now=NOW + 1, collect_snapshot=race)
    assert Path(config['snapshot']).read_bytes() == before


def test_runtime_binding_cannot_fall_back_to_export(live):
    config, path, _, _, _ = live
    config['request'] = str(Path(config['private_dir']) / 'old.json')
    write(path, config)
    with pytest.raises(ValueError, match='choose_one_authority_source'):
        observations.load_binding(path)


def test_capture_occurs_after_authority_load(live):
    _, path, _, capture, runtime = live
    calls = []
    load = runtime.load
    runtime.load = lambda: (calls.append('load'), load())[1]
    def capture_once():
        calls.append('capture')
        return capture
    observations.publish(path, capture_once, now=NOW)
    assert calls == ['load', 'capture', 'load']


def test_pending_authority_refuses_before_capture(live):
    config, path, _, _, runtime = live
    runtime.journal.pending = lambda keys: ['pending']
    with pytest.raises(ValueError, match='authority_pending'):
        observations.publish(path, lambda: pytest.fail('capture before authority'), now=NOW)
    assert not Path(config['snapshot']).exists()


@pytest.mark.parametrize('root_key', ['private_root', 'state_root', 'vault_root', 'runtime_config'])
def test_snapshot_cannot_overwrite_runtime_roots(live, root_key):
    config, path, _, _, _ = live
    config['runtime'][root_key] = config['snapshot']
    write(path, config)
    with pytest.raises(ValueError, match='snapshot_overwrites_runtime'):
        observations.load_binding(path)


def test_catalog_pin_drift_blocks_cached_catalog(live):
    config, path, _, capture, _ = live
    catalog_path = write(Path(config['private_dir']) / 'catalog.json', {'repo_roots': []})
    config['catalog_request'] = str(catalog_path)
    config['catalog_pins'] = {str(catalog_path): hashlib.sha256(catalog_path.read_bytes()).hexdigest()}
    write(path, config)
    observations.publish(path, capture, now=NOW)
    before = Path(config['snapshot']).read_bytes()
    write(catalog_path, {'repo_roots': [{'path': 'changed'}]})
    with pytest.raises(ValueError, match='catalog_input_changed'):
        observations.publish(path, capture, now=NOW + 1)
    assert Path(config['snapshot']).read_bytes() == before


def test_periodic_requires_runtime_before_any_capture(case, monkeypatch, capsys):
    _, path, _, _ = case
    monkeypatch.setattr(observations.process, 'run', lambda *a, **k: pytest.fail('unexpected child'))
    assert observations.main(['--binding', str(path), '--periodic',
                              '--powershell', str(path.parent / 'powershell.exe')]) == 2
    assert json.loads(capsys.readouterr().err)['reason_code'] == 'runtime_binding_required'


def test_runtime_loader_uses_explicit_config_and_retains_authority(case, monkeypatch):
    from t12_runtime_support import setup, FakeProtector
    from task_console import runtime_windows
    config, path, _, capture = case
    root = path.parent / 'runtime-fixture'
    runtime, task = setup(root)
    receipt_path = root / 'private/adoption.json'
    receipt = observations.read_json(receipt_path)
    receipt['authority'].update(authority_epoch=1, migrated_tasks=[task])
    write(receipt_path, receipt)
    # The test synthesizes existing authority, never calls adoption or migration.
    def forbidden(*args, **kwargs):
        pytest.fail('collector used Scheduler mutation/DPAPI transport')
    monkeypatch.setattr(runtime_windows, 'COMTransport', lambda: forbidden)
    monkeypatch.setattr(runtime_windows, 'DPAPIProtector', FakeProtector)
    config.pop('request')
    config['runtime'] = dict(runtime_config=str(root / 'private/config.json'),
                            private_root=str(root / 'private'), state_root=str(root / 'state'),
                            vault_root=str(root / 'vault'))
    write(path, config)
    before = receipt_path.read_bytes()
    result = observations.publish(path, capture, now=NOW)
    assert result['authority_epoch'] == 1 and result['authority_generation'] == '2' * 32
    assert receipt_path.read_bytes() == before


def test_explicit_catalog_inputs_preserve_native_evidence_and_unknowns(live):
    from skill_smith.catalog import discover
    config, path, _, capture, _ = live
    root = path.parent / 'catalog-root'
    skill = root / 'acme'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: acme\ndescription: Synthetic test skill\n---\n', encoding='utf-8')
    request = {'skill_roots': [{'path': str(root), 'namespace': 'acme', 'client': 'codex', 'scope': 'user'}],
               'repo_roots': [], 'plugin_registries': [], 'plugin_descriptors': []}
    record = discover(request)['records'][0]
    entry = record['entrypoints'][0]
    observation = {key: entry[key] for key in ('kind', 'name', 'client', 'scope')}
    observation.update(source_id=record['source_id'], discovered='yes', compatible='unknown',
                       observed_at='2026-01-01T00:00:00Z')
    native = write(root / 'native.json', {'entrypoints': [observation]})
    private = write(root / 'private.json', {'bindings': [
        {'id': 'acme-connector', 'kind': 'app_connector', 'connector': 'acme', 'authenticated': 'unknown'}]})
    request.update(runtime_discovery=str(native), private_bindings=str(private))
    catalog = write(root / 'request.json', request)
    config['catalog_request'] = str(catalog)
    config['catalog_pins'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (catalog, native, private)}
    write(path, config)
    observations.publish(path, capture, now=NOW)
    result = observations.read_json(config['snapshot'])['catalog']
    assert all(result['coverage'][key]['status'] == 'checked' for key in
               ('repo_roots', 'plugin_descriptors', 'runtime_discovery', 'private_bindings'))
    skill_record = next(r for r in result['records'] if r['kind'] == 'skill')
    matched = skill_record['entrypoints'][0]
    assert matched['status']['discovered'] == 'yes'
    assert matched['status']['compatible'] == 'unknown'
    assert '2026-01-01T00:00:00Z' in json.dumps(matched['evidence'])
    assert next(r for r in result['records'] if r['kind'] == 'app_connector')['status']['authenticated'] == 'unknown'


CAPTURE_HARNESS = r'''
param([string]$Collector, [switch]$Zero)
$ErrorActionPreference = 'Stop'
function Get-ScheduledTask {
  if ($Zero) { return }
  [pscustomobject]@{TaskName='AcmeSync'; Description='synthetic'; State='Ready'; Triggers=@();
    Actions=@([pscustomobject]@{Execute='must-not-execute'; Arguments='synthetic'});
    Settings=[pscustomobject]@{StartWhenAvailable=$false; RestartCount=0; ExecutionTimeLimit='PT7M';
      MultipleInstances='IgnoreNew'; DisallowStartIfOnBatteries=$false; StopIfGoingOnBatteries=$false};
    Principal=[pscustomobject]@{RunLevel='Limited'; UserId='AcmeService'}}
}
function Get-ScheduledTaskInfo {
  [pscustomobject]@{LastTaskResult=0; LastRunTime=(Get-Date).AddMinutes(-1);
    NextRunTime=(Get-Date).AddHours(1); NumberOfMissedRuns=0}
}
function Get-CimInstance { throw 'synthetic query denied' }
function Get-Process { @() }
function Get-NetTCPConnection { throw 'synthetic query denied' }
function Start-ScheduledTask { throw 'forbidden task start' }
function Register-ScheduledTask { throw 'forbidden registration' }
function Start-Process { throw 'forbidden payload' }
& $Collector -CaptureOnly
'''


@pytest.mark.skipif(os.name != 'nt', reason='Windows source PowerShell capture')
@pytest.mark.parametrize('zero', [False, True])
def test_periodic_cli_source_workers_and_existing_capture_only(live, monkeypatch, capsys, zero):
    config, path, bundle, _, _ = live
    repo = Path(observations.__file__).resolve().parents[2]
    run_root = repo.parent
    harness = path.parent / 'capture.ps1'
    harness.write_text(CAPTURE_HARNESS, encoding='utf-8-sig')
    bootstrap = path.parent / 'source_worker.py'
    roots = [str(run_root / p) for p in ('task-console/scripts', 'fleet-guards', 'llmcall',
                                         'skill-smith/skills/skill-smith/scripts')]
    bootstrap.write_text('import sys\nsys.path[:0] = ' + repr(roots) + '\n'
                         'from task_console import observations\n'
                         'assert observations.__file__ == ' + repr(observations.__file__) + '\n'
                         'raise SystemExit(observations.main(sys.argv[1:]))\n', encoding='utf-8')
    real_run = observations.process.run
    commands = []
    def source_run(command, prompt, timeout, **kwargs):
        commands.append(command)
        if command[-1] == '-CaptureOnly':
            assert kwargs['max_input_bytes'] == 0
            command = [*command[:4], str(harness), '-Collector', command[4]]
            if zero:
                command.append('-Zero')
        else:
            assert command[-1] == '--worker'
            command = [sys.executable, '-I', '-B', str(bootstrap), '--worker']
        return real_run(command, prompt, timeout, **kwargs)
    monkeypatch.setattr(observations.process, 'run', source_run)
    # The raw pass must suppress inherited publication bindings.
    monkeypatch.setenv('TASK_CONSOLE_OBSERVATION_BINDING', 'must-not-read')
    monkeypatch.setenv('TASK_CONSOLE_OBSERVATION_PYTHON', 'must-not-launch')
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    assert observations.main(['--binding', str(path), '--periodic', '--powershell', str(shell)]) == (2 if zero else 0)
    output = capsys.readouterr()
    if zero:
        assert json.loads(output.err)['reason_code'] == 'capture_failed'
        assert not Path(config['snapshot']).exists() and len(commands) == 1
        return
    receipt = json.loads(output.out)
    assert receipt['published'] and len(commands) == 2
    snapshot = observations.read_json(config['snapshot'])
    assert snapshot['authority_generation'] == bundle['authority_generation']
    assert snapshot['tasks'][0]['observations']['scheduler']['last_rc'] == 0


@pytest.mark.skipif(os.name != 'nt', reason='Windows collection-only monitor')
@pytest.mark.parametrize('mode', ['ok', 'missing_python', 'bad_output', 'failed', 'missing_binding',
                                 'bad_count', 'missing_provenance'])
def test_monitor_collection_only_has_no_downstream_side_effects(tmp_path, mode, config_source):
    monitor = config_source / 'claude/scripts/task-health-monitor.ps1'
    stub = tmp_path / 'synthetic-python.ps1'
    receipt = {'schemaVersion': 1, 'published': True, 'authority_generation': 'a' * 32,
               'input_revision': 'sha256:' + 'b' * 64, 'sha256': 'c' * 64, 'tasks': 1, 'subjects': 1,
               'authority_epoch': 1, 'effective_request_sha256': 'd' * 64}
    if mode == 'failed':
        receipt = {'schemaVersion': 1, 'published': False, 'reason_code': 'authority_changed'}
    elif mode == 'bad_count':
        receipt['tasks'] = 'not-a-count'
    elif mode == 'missing_provenance':
        receipt.pop('effective_request_sha256')
    output = 'synthetic bad output' if mode == 'bad_output' else json.dumps(receipt)
    stub.write_text("if ($args -notcontains '--periodic' -or $args -notcontains '-I') { throw 'wrong command' }\n"
                    "Write-Output '" + output + "'\nexit " + ('2' if mode == 'failed' else '0'), encoding='utf-8-sig')
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    args = [str(shell), '-NoProfile', '-NonInteractive', '-File', str(monitor), '-CollectionOnly',
            '-ObservationPython', str(stub if mode != 'missing_python' else tmp_path / 'missing.exe'),
            '-ObservationBinding', '' if mode == 'missing_binding' else str(tmp_path / 'binding.json'),
            '-LogDir', str(tmp_path / 'must-not-create-logs'), '-Config', str(tmp_path / 'must-not-read')]
    result = subprocess.run(args, capture_output=True, timeout=20, encoding='utf-8')
    assert result.returncode == (0 if mode == 'ok' else 2), result.stderr
    actual = json.loads(result.stdout)
    assert actual['published'] == (mode == 'ok')
    if mode == 'failed':
        assert actual['reason_code'] == 'authority_changed'
    assert not (tmp_path / 'must-not-create-logs').exists()
    assert 'synthetic bad output' not in result.stdout + result.stderr


@pytest.mark.skipif(os.name != 'nt', reason='Windows bounded source worker cleanup')
@pytest.mark.parametrize('mode,reason', [('hung', 'worker_timeout'), ('stdout', 'worker_output_limit'),
                                       ('stderr', 'worker_output_limit'), ('dependency', 'dependency_unavailable')])
def test_periodic_worker_failures_preserve_snapshot_and_clean_synthetic_children(live, monkeypatch, mode, reason):
    from test_observation_collection import fake_catalog_worker
    config, path, _, capture, _ = live
    observations.publish(path, capture, now=NOW)
    output = Path(config['snapshot'])
    before = output.read_bytes()
    config['catalog_request'] = str(write(path.parent / 'catalog.json', {}))
    write(path, config)
    command, heartbeat = fake_catalog_worker(path.parent, mode)
    script = Path(command[3])
    run_root = Path(observations.__file__).resolve().parents[3]
    roots = [str(run_root / p) for p in ('task-console/scripts', 'fleet-guards', 'llmcall',
                                         'skill-smith/skills/skill-smith/scripts')]
    script.write_text('import sys\nsys.path[:0] = ' + repr(roots) + '\n' + script.read_text(), encoding='utf-8')
    real_run = observations.process.run
    def source_worker(cmd, prompt, timeout, **kwargs):
        assert cmd[-1] == '--worker'
        kwargs['max_output_bytes'] = 4096
        return real_run(command, prompt, min(timeout, 6), **kwargs)
    monkeypatch.setattr(observations.process, 'run', source_worker)
    with pytest.raises(observations.CollectionFailure, match=reason):
        observations.publish(path, capture, now=NOW, collect_snapshot=observations.collect_supervised)
    assert output.read_bytes() == before
    if mode != 'dependency':
        assert heartbeat.exists() and heartbeat.stat().st_size > 0
        size = heartbeat.stat().st_size
        time.sleep(0.2)
        assert heartbeat.stat().st_size == size
