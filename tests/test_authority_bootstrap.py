"""Initial authority uses synthetic roots and forbids real transport before collection."""
from pathlib import Path
from copy import deepcopy
import json
import os
import subprocess
import sys

import pytest

from t12_runtime_support import setup, configured, fs, encode, read_json, observation, parse, NS
import xml.etree.ElementTree as ET
from task_console import runtime_windows
from task_console import registration as reg
from task_console.runtime_storage import digest


def forbidden(*args, **kwargs):
    raise AssertionError('real transport forbidden')


from task_console import adoption


@pytest.fixture(autouse=True)
def no_live_transport(monkeypatch):
    # The isolated runner also installs these barriers before collection.
    monkeypatch.setattr(runtime_windows, 'COMTransport', forbidden)
    monkeypatch.setattr(runtime_windows, 'DPAPIProtector', forbidden)
    monkeypatch.setattr(runtime_windows, 'powershell', forbidden)


def initial(root, **kwargs):
    runtime, task_id = setup(root, **kwargs)
    runtime.authority.config.adoption.unlink()
    source = root / 'private/source-binding.json'
    maintained = root / 'private/maintained.py'
    maintained.write_bytes(b'# Synthetic maintained source.\n')
    request = read_json(runtime.authority.config.desired)
    source.write_bytes(encode({'schemaVersion': 1,
        'input_revision': reg.compile_plan(request)['input_revision'],
        'installed_generation': 'synthetic-generation', 'prerequisites': [],
        'tasks': {task_id: {'status': 'reviewed', 'reason_codes': []}},
        'sources': {str(runtime.authority.config.desired): digest(runtime.authority.config.desired.read_bytes()),
                    str(maintained): digest(maintained.read_bytes())}}))
    return runtime, task_id, source


def approved(runtime, source):
    plan = adoption.build_plan(runtime, source_binding=source)
    assert plan['applicable'], plan['blockers']
    return plan


def test_initial_receipt_frozen_inputs_no_scheduler_action(tmp_path):
    runtime, task_id, source = initial(tmp_path)
    plan = approved(runtime, source)
    before = {p: runtime.files.read(p) for p in runtime.files.paths}
    result = adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    assert result['status'] == 'committed' and result['migrated_tasks'] == 0
    loaded = configured(tmp_path).load()
    assert loaded['ownership'][task_id]['writer'] == 'legacy'
    assert loaded['request']['machine']['migrated_tasks'] == []
    assert 'input_reference' in runtime.authority.receipt()
    assert before == {p: runtime.files.read(p) for p in runtime.files.paths}
    assert {op for op, _ in runtime.scheduler.transport.calls} == {'query'}
    assert all(req['TaskPath'] == '\\' for _, req in runtime.scheduler.transport.calls)


@pytest.mark.parametrize('change', ['definition', 'projection', 'input', 'binding', 'source'])
def test_raced_inputs_refused(tmp_path, change):
    runtime, _, source = initial(tmp_path)
    plan = approved(runtime, source)
    config = runtime.authority.config
    if change == 'definition':
        path = tmp_path / 'synthetic-scheduler.json'
        tasks = read_json(path)
        item = tasks['AcmeSync']['value']
        tree = parse(item['xml'])
        tree.find('./{' + NS + '}Settings/{' + NS + '}Priority').text = '8'
        previous = tasks['AcmeSync']['identity']
        item['xml'] = ET.tostring(tree, encoding='unicode')
        tasks['AcmeSync'] = observation('AcmeSync', item)
        assert previous != tasks['AcmeSync']['identity']
        fs.atomic_replace(path, encode(tasks))
    else:
        path = {'projection': Path(config.paths['categories.json']), 'input': config.desired,
                'binding': source, 'source': tmp_path / 'private/maintained.py'}[change]
        fs.atomic_replace(path, path.read_bytes() + b' ')
    with pytest.raises(reg.Conflict):
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    assert not config.adoption.exists()


def test_absence_is_distinct_from_query_failure_and_running(tmp_path):
    runtime, task_id, source = initial(tmp_path, absent=True)
    plan = approved(runtime, source)
    assert plan['tasks'][0]['state'] == 'absent'
    assert plan['tasks'][0]['identity'] is None
    for response in ({'ok': False}, {'ok': True}, {'ok': True, 'snapshot': {'state': 'unknown'}}):
        runtime.scheduler.transport = lambda *args, response=response: response
        blocked = adoption.build_plan(runtime, source_binding=source)
        assert not blocked['applicable'] and blocked['tasks'][0]['state'] == 'unknown'
    runtime = configured(tmp_path)
    tasks = read_json(tmp_path / 'synthetic-scheduler.json')
    # A separate synthetic fixture supplies a complete running observation.
    other, _, _ = initial(tmp_path / 'other')
    snap = other.scheduler.read('AcmeSync')
    snap['value']['running'] = True
    tasks['AcmeSync'] = snap
    fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode(tasks))
    observed = adoption.build_plan(runtime, source_binding=source)
    # Running is a recorded observation of a payload this metadata-only
    # transaction never writes, not an epoch-zero stop condition.
    assert observed['applicable'] and not observed['blockers']
    assert observed['tasks'][0]['running'] is True
    assert observed['running_tasks'] == [task_id]


def test_verified_absence_receipt_does_not_create_task(tmp_path):
    runtime, task_id, source = initial(tmp_path, absent=True)
    plan = approved(runtime, source)
    adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    proof = runtime.authority.receipt()['ownership'][task_id]
    assert proof['identity'] is None and proof['state'] == 'absent' and proof['enabled'] is None
    assert runtime.scheduler.read('AcmeSync') == {'state': 'absent'}
    assert {op for op, _ in runtime.scheduler.transport.calls} == {'query'}


def test_busy_double_initializer_and_existing_pointer_preserved(tmp_path):
    runtime, _, source = initial(tmp_path)
    plan = approved(runtime, source)
    with runtime.locks.hold(['authority']):
        with pytest.raises(reg.Conflict, match='busy'):
            adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    paths = [runtime.authority.config.adoption, runtime.authority.pointer]
    before = {p: p.read_bytes() for p in paths}
    with pytest.raises(reg.Conflict):
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    assert before == {p: p.read_bytes() for p in paths}


class Crash(BaseException):
    pass


@pytest.mark.parametrize('point', ['bootstrap_staged', 'bootstrap_receipt_written', 'bootstrap_pointer_written', 'bootstrap_committed'])
def test_resumed_initialization(tmp_path, point):
    runtime, _, source = initial(tmp_path)
    plan = approved(runtime, source)
    runtime.checkpoint = lambda at: (_ for _ in ()).throw(Crash()) if at == point else None
    with pytest.raises(Crash):
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    tx = runtime.journal.list()[0]['transaction_id']
    fresh = configured(tmp_path)
    result = adoption.resume(tx, plan['plan_revision'], runtime=fresh)
    assert result['status'] == 'committed' and result['cleaned']
    assert fresh.load()['request']['machine']['migrated_tasks'] == []


def test_equal_bytes_foreign_receipt_and_resume_edit_refused(tmp_path):
    runtime, _, source = initial(tmp_path)
    plan = approved(runtime, source)
    runtime.checkpoint = lambda at: (_ for _ in ()).throw(Crash()) if at == 'bootstrap_receipt_written' else None
    with pytest.raises(Crash):
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    tx = runtime.journal.list()[0]['transaction_id']
    path = runtime.authority.config.adoption
    fs.atomic_replace(path, path.read_bytes())
    with pytest.raises(reg.Conflict):
        adoption.resume(tx, plan['plan_revision'], runtime=configured(tmp_path))
    assert path.exists() and runtime.journal.load(tx)['status'] != 'committed'


def test_review_only_and_owner_blockers(tmp_path):
    runtime, task_id, source = initial(tmp_path)
    proposal = read_json(source)
    proposal['installed_generation'] = None
    proposal['tasks'][task_id] = {'status': 'unclassified', 'reason_codes': ['source_review_required']}
    source.write_bytes(encode(proposal))
    plan = adoption.build_plan(runtime, source_binding=source, review_only=True)
    assert not plan['applicable']
    assert {'review_only', 'installed_generation_required', 'owner_unclassified'} <= {b['code'] for b in plan['blockers']}
    with pytest.raises(reg.Conflict):
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)


def test_incomplete_source_scope(tmp_path):
    runtime, task_id, source = initial(tmp_path)
    proposal = read_json(source)
    proposal['tasks'] = {}
    source.write_bytes(encode(proposal))
    with pytest.raises(reg.Conflict, match='source_scope_mismatch'):
        adoption.build_plan(runtime, source_binding=source)


def test_duplicate_declared_name_refused_without_scheduler_query(tmp_path):
    runtime, task_id, source = initial(tmp_path)
    request = read_json(runtime.authority.config.desired)
    task = deepcopy(request['components'][0]['tasks'][0])
    task['id'] = 'other'
    request['components'][0]['tasks'].append(task)
    request['bindings']['tasks'][task_id.rsplit('/', 1)[0] + '/other'] = deepcopy(request['bindings']['tasks'][task_id])
    runtime.authority.config.desired.write_bytes(encode(request))
    runtime.scheduler.transport.calls.clear()
    with pytest.raises(reg.ContractError, match='duplicate_name'):
        adoption.build_plan(runtime, source_binding=source)
    assert not runtime.scheduler.transport.calls


@pytest.mark.parametrize('target', ['adoption', 'pointer', 'generation'])
def test_foreign_authority_never_overwritten(tmp_path, target):
    runtime, _, source = initial(tmp_path)
    plan = approved(runtime, source)
    config = runtime.authority.config
    path = {'adoption': config.adoption, 'pointer': runtime.authority.pointer,
            'generation': config.state_root / 'receipts' / ('f' * 32 + '.json')}[target]
    fs.atomic_replace(path, b'{"foreign":true}')
    before = path.read_bytes()
    with pytest.raises(reg.Conflict):
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    assert path.read_bytes() == before
    assert not runtime.journal.list()


def test_resume_rechecks_projection_and_retains_receipt(tmp_path):
    runtime, _, source = initial(tmp_path)
    plan = approved(runtime, source)
    runtime.checkpoint = lambda at: (_ for _ in ()).throw(Crash()) if at == 'bootstrap_receipt_written' else None
    with pytest.raises(Crash):
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    tx = runtime.journal.list()[0]['transaction_id']
    path = Path(runtime.authority.config.paths['categories.json'])
    fs.atomic_replace(path, path.read_bytes() + b' ')
    with pytest.raises(reg.Conflict, match='bootstrap_plan_changed'):
        adoption.resume(tx, plan['plan_revision'], runtime=configured(tmp_path))
    assert runtime.authority.config.adoption.exists()
    assert runtime.journal.pending(['authority']) == [tx]


def test_cli_plan_requires_explicit_approval_and_apply(tmp_path, capsys):
    from task_console.__main__ import main
    runtime, _, source = initial(tmp_path)
    assert main(['adoption-plan', '--source-binding', str(source)], runtime=runtime) == 0
    plan = json.loads(capsys.readouterr().out)
    path = tmp_path / 'plan.json'
    path.write_bytes(encode(plan))
    assert main(['adoption-apply', '--request', str(path)], runtime=runtime) == 2
    assert 'error' in json.loads(capsys.readouterr().out)
    assert not runtime.authority.config.adoption.exists()
    assert main(['adoption-apply', '--request', str(path), '--approval-revision', plan['plan_revision']], runtime=runtime) == 0
    assert json.loads(capsys.readouterr().out)['migrated_tasks'] == 0


def test_later_migration_remains_owned_by_registration(tmp_path):
    runtime, task_id, source = initial(tmp_path)
    initial_plan = approved(runtime, source)
    adoption.apply(initial_plan, initial_plan['plan_revision'], runtime=runtime)
    assert runtime.load()['request']['machine']['migrated_tasks'] == []
    migration = reg.build_plan(runtime, [task_id], migrate=True)
    result = reg.apply(migration, migration['input_revision'], runtime=runtime)
    assert result['status'] == 'committed', result
    assert runtime.load()['request']['machine']['migrated_tasks'] == [task_id]


@pytest.mark.parametrize('point', ['bootstrap_receipt_written', 'bootstrap_pointer_written'])
def test_process_exit_then_fresh_process_resume(tmp_path, point):
    runtime, _, _ = initial(tmp_path)
    child = subprocess.run([sys.executable, str(Path(__file__)), str(tmp_path), point], capture_output=True, timeout=60)
    assert child.returncode == 86, child.stderr.decode(errors='replace')
    child = subprocess.run([sys.executable, str(Path(__file__)), str(tmp_path), 'resume'], capture_output=True, timeout=60)
    assert child.returncode == 0, child.stderr.decode(errors='replace')
    assert configured(tmp_path).load()['request']['machine']['migrated_tasks'] == []


if __name__ == '__main__':
    # This subprocess barrier is installed before fixture collection or runtime construction.
    runtime_windows.COMTransport = forbidden
    runtime_windows.DPAPIProtector = forbidden
    runtime_windows.powershell = forbidden
    root, operation = Path(sys.argv[1]), sys.argv[2]
    runtime = configured(root)
    if operation == 'resume':
        plan = read_json(root / 'approved-plan.json')
        tx = runtime.journal.list()[0]['transaction_id']
        adoption.resume(tx, plan['plan_revision'], runtime=runtime)
    else:
        plan = approved(runtime, root / 'private/source-binding.json')
        (root / 'approved-plan.json').write_bytes(encode(plan))
        runtime.checkpoint = lambda at: os._exit(86) if at == operation else None
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)
