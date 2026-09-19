"""Backup drift checks using generated declarations and fake Scheduler only."""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'scripts', ROOT.parent / 'fleet-guards'):
    sys.path.insert(0, str(path))
from t12_runtime_support import setup, fs, encode, read_json
from task_console import export_restore as owner
from task_console import registration as reg


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(('TASK_CONSOLE', 'TASKCONSOLE')):
            monkeypatch.delenv(key)
    for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP'):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')


def signed(archive):
    archive['receipt_revision'] = reg.revision({k: v for k, v in archive.items() if k != 'receipt_revision'})
    return archive


def xml_files(archive):
    return {hashlib.sha256(row['task_id'].encode()).hexdigest() + '.xml': row['snapshot']['value']['xml'].encode()
            for row in archive['tasks'] if row['status'] == 'COMPLETED'}


def backup(path, archive):
    path.mkdir()
    (path / 'receipt.json').write_bytes(encode(archive))
    for name, content in xml_files(archive).items():
        (path / name).write_bytes(content)
    return path


def test_task_gate_uses_controller_scope_not_removed_catalog(config_source):
    source = (config_source / 'tools/check-drift.ps1').read_text(encoding='utf-8-sig')
    task = source.split('# ---------- 2. TASK ----------')[1].split('# ---------- 3. SURFACE ----------')[0]
    assert 'check-task-backup.ps1' in task
    assert all(value not in task for value in ('TaskNames', 'Get-ScheduledTask', 'claudePat', 'taskNotBackedUp', '.BaseName'))
    assert '$allTasks' not in source and 'declared=' in source and 'checked=' in source


@pytest.mark.parametrize('name,absent', [("Odd job 'quoted' Ω Once", False), ('Zebra maintenance', True)])
def test_arbitrary_names_disabled_and_absent_remain_read_only(tmp_path, monkeypatch, name, absent):
    runtime, _ = setup(tmp_path, name=name, absent=absent)
    archive = owner.export_tasks(runtime)
    destination = backup(tmp_path / 'backup', archive)
    # Locks have already been acquired by export; no data writes may follow.
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail('backup check attempted publication or data write')
    monkeypatch.setattr(reg, 'apply', forbidden)
    monkeypatch.setattr(fs, 'atomic_replace', forbidden)
    monkeypatch.setattr(runtime.scheduler, 'prepare', forbidden)
    monkeypatch.setattr(runtime.scheduler, 'publish', forbidden)
    runtime.scheduler.transport.calls.clear()
    result = owner.check_backup(runtime, destination)
    assert result['ok'] and result['scope_count'] == result['checked_count'] == 1
    assert result['absent_count'] == int(absent)
    assert result['disabled_count'] == int(not absent)
    assert all(op == 'query' for op, _ in runtime.scheduler.transport.calls)
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('reason', ['retired', 'backup_disabled'])
def test_explicit_exclusions_are_scope_evidence(tmp_path, reason):
    runtime, task_id = setup(tmp_path)
    if reason == 'retired':
        fs.atomic_replace(tmp_path / 'private/tombstones.json', encode({task_id: {'reason': 'synthetic'}}))
    else:
        request = read_json(tmp_path / 'private/desired.json')
        request['bindings']['tasks'][task_id]['backup'] = False
        fs.atomic_replace(tmp_path / 'private/desired.json', encode(request))
    adoption = read_json(tmp_path / 'private/adoption.json')
    adoption['file_ownership'] = {p: reg.fingerprint(runtime.files.read(p)) for p in runtime.files.paths}
    fs.atomic_replace(tmp_path / 'private/adoption.json', encode(adoption))
    archive = owner.export_tasks(runtime)
    assert archive['excluded'][0]['reason'] == reason
    result = owner.check_backup(runtime, backup(tmp_path / 'backup', archive))
    assert result['scope_count'] == result['excluded_count'] == 1 and result['checked_count'] == 0


@pytest.mark.parametrize('damage', ['hash', 'snapshot_hash', 'identity', 'enabled', 'status', 'scope',
                                  'extra_scope', 'duplicate', 'name', 'generation', 'input', 'epoch',
                                  'excluded', 'empty', 'schema_bool', 'unknown_header', 'xml_missing',
                                  'xml_extra', 'xml_content', 'xml_name'])
def test_receipt_and_xml_corruption_or_staleness_fails(tmp_path, damage):
    runtime, _ = setup(tmp_path)
    current = owner.export_tasks(runtime)
    archive = deepcopy(current)
    row = archive['tasks'][0]
    files = xml_files(archive)
    if damage == 'hash':
        archive['receipt_revision'] = 'sha256:' + '0' * 64
    elif damage == 'snapshot_hash':
        row['snapshot_revision'] = 'sha256:' + '0' * 64
    elif damage == 'identity':
        row['snapshot']['identity'] = row['ownership_identity'] = 'task-definition:' + '0' * 64
        row['snapshot_revision'] = reg.revision(row['snapshot'])
    elif damage == 'enabled':
        row['enabled'] = row['snapshot']['value']['enabled'] = True
        row['snapshot_revision'] = reg.revision(row['snapshot'])
    elif damage == 'status':
        row['status'] = 'FAILED'
    elif damage in ('scope', 'empty'):
        archive['tasks'] = []
        files = {}
        if damage == 'empty':
            current = signed(deepcopy(archive))
    elif damage == 'extra_scope':
        extra = deepcopy(row)
        extra['task_id'] += '-extra'
        extra['name'] += ' extra'
        archive['tasks'].append(extra)
    elif damage == 'duplicate':
        archive['excluded'] = [{**row, 'reason': 'retired'}]
    elif damage == 'name':
        row['name'] += ' renamed'
    elif damage == 'generation':
        archive['generation'] = '3' * 32
    elif damage == 'input':
        archive['input_revision'] = 'sha256:' + '0' * 64
    elif damage == 'epoch':
        archive['authority_epoch'] += 1
    elif damage == 'excluded':
        del archive['excluded']
    elif damage == 'schema_bool':
        archive['schemaVersion'] = True
    elif damage == 'unknown_header':
        archive['unknown'] = 'synthetic-only'
    elif damage == 'xml_missing':
        files.clear()
    elif damage == 'xml_extra':
        files['unexpected.xml'] = b'<Task/>'
    elif damage == 'xml_content':
        files[next(iter(files))] += b'\n'
    elif damage == 'xml_name':
        files[row['name'] + '.xml'] = files.pop(next(iter(files)))
    if damage != 'hash':
        signed(archive)
    with pytest.raises(reg.ContractError):
        owner.validate_backup(archive, current, files)


def test_running_state_is_not_definition_drift_but_raw_xml_is(tmp_path):
    runtime, _ = setup(tmp_path)
    archive = owner.export_tasks(runtime)
    current = deepcopy(archive)
    row = current['tasks'][0]
    row['snapshot']['value']['running'] = True
    row['snapshot_revision'] = reg.revision(row['snapshot'])
    signed(current)
    assert owner.validate_backup(archive, current, xml_files(archive))['ok']
    row['snapshot']['value']['xml'] += '\n'
    row['snapshot_revision'] = reg.revision(row['snapshot'])
    signed(current)
    with pytest.raises(reg.ContractError):
        owner.validate_backup(archive, current, xml_files(archive))


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'duplicate_json', 'extra_file', 'missing_runtime', 'corrupt_runtime', 'inaccessible'])
def test_bad_inputs_never_pass_or_leak_contents(tmp_path, capsys, damage):
    from task_console.task_backup_check import main
    runtime, _ = setup(tmp_path)
    archive = owner.export_tasks(runtime)
    target = backup(tmp_path / 'backup', archive)
    if damage == 'missing':
        target = tmp_path / 'missing'
    elif damage == 'corrupt':
        (target / 'receipt.json').write_text('synthetic-secret-content')
    elif damage == 'duplicate_json':
        (target / 'receipt.json').write_text('{"tasks":[],"tasks":[]}')
    elif damage == 'extra_file':
        (target / 'extra.xml').write_text('synthetic-secret-content')
    elif damage == 'missing_runtime':
        (tmp_path / 'private/adoption.json').unlink()
    elif damage == 'corrupt_runtime':
        (tmp_path / 'private/adoption.json').write_text('synthetic-secret-content')
    else:
        def inaccessible(*args, **kwargs):
            raise OSError('synthetic-secret-content')
        runtime.scheduler.transport = inaccessible
    assert main(['--backup-directory', str(target)], runtime=runtime) != 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result['ok'] is False
    assert 'synthetic-secret-content' not in output.out + output.err
    assert '<Task' not in output.out and not output.err


def test_unconfigured_runtime_fails_without_home_fallback(tmp_path, capsys):
    from task_console.task_backup_check import main
    assert main(['--backup-directory', str(tmp_path)]) != 0
    result = json.loads(capsys.readouterr().out)
    assert result['error']['code'] == 'runtime_not_configured'


def test_runtime_exception_text_is_not_output(tmp_path, monkeypatch, capsys):
    from task_console import task_backup_check as cli
    def fail():
        raise RuntimeError('synthetic-secret-content')
    monkeypatch.setattr(cli, 'load_runtime', fail)
    assert cli.main(['--backup-directory', str(tmp_path)]) != 0
    output = capsys.readouterr()
    assert 'synthetic-secret-content' not in output.out + output.err


def test_absent_is_distinct_from_disappearance_since_backup(tmp_path):
    runtime, _ = setup(tmp_path)
    target = backup(tmp_path / 'backup', owner.export_tasks(runtime))
    fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode({}))
    with pytest.raises(reg.ContractError):
        owner.check_backup(runtime, target)


def test_backup_changed_during_export_fails(tmp_path, monkeypatch):
    runtime, _ = setup(tmp_path)
    archive = owner.export_tasks(runtime)
    target = backup(tmp_path / 'backup', archive)
    original = owner.export_tasks
    def changed(runtime):
        result = original(runtime)
        (target / 'unexpected.xml').write_text('synthetic-only')
        return result
    monkeypatch.setattr(owner, 'export_tasks', changed)
    with pytest.raises(reg.ContractError, match='backup_changed_during_check'):
        owner.check_backup(runtime, target)


def test_pure_validation_does_not_open_or_write(tmp_path, monkeypatch):
    import builtins
    runtime, _ = setup(tmp_path)
    archive = owner.export_tasks(runtime)
    files = xml_files(archive)
    def forbidden(*args, **kwargs):
        pytest.fail('pure validation attempted filesystem I/O')
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(Path, 'open', forbidden)
    monkeypatch.setattr(fs, 'atomic_replace', forbidden)
    assert owner.validate_backup(archive, archive, files)['ok']


def test_check_queries_only_inside_existing_owner_locks(tmp_path, monkeypatch):
    runtime, task_id = setup(tmp_path, name='Zebra audit Once')
    target = backup(tmp_path / 'backup', owner.export_tasks(runtime))
    active = set()
    hold = runtime.locks.hold
    read = runtime.scheduler.read
    bundle = runtime.load()
    expected = {'authority', 'task:' + task_id}
    expected.update('file:' + p for p in bundle['paths'].values())
    expected.update('file:' + p for p in bundle['launchers'].values())
    @contextmanager
    def tracked(keys):
        with hold(keys):
            active.update(keys)
            try:
                yield
            finally:
                active.difference_update(keys)
    def checked(name):
        assert expected <= active
        assert name == 'Zebra audit Once'
        return read(name)
    monkeypatch.setattr(runtime.locks, 'hold', tracked)
    monkeypatch.setattr(runtime.scheduler, 'read', checked)
    assert owner.check_backup(runtime, target)['checked_count'] == 1
    assert not active


def test_valid_receipts_with_different_excluded_scope_fail(tmp_path):
    runtime, _ = setup(tmp_path)
    archive = owner.export_tasks(runtime)
    current = deepcopy(archive)
    current['excluded'] = [{'task_id': 'acme-maintenance/other', 'name': 'Zebra excluded',
                            'task_path': '\\', 'writer': 'legacy', 'ownership_identity': None,
                            'reason': 'backup_disabled'}]
    signed(current)
    assert owner.validate_backup_archive(current)
    for backed, live in ((archive, current), (current, archive)):
        with pytest.raises(reg.ContractError, match='backup_scope_or_generation_changed'):
            owner.validate_backup(backed, live, xml_files(backed))
