"""T11 bootstrap repairs on synthetic roots only; no real transport or Scheduler.

Covers the three confirmed integration failures: the all-task idle gate on a
metadata-only epoch-zero bootstrap, terminal cleanup stranded by an obsolete
input identity, and ordinary recover/status handling of bootstrap journals.
"""
from copy import deepcopy
from pathlib import Path
import json
import xml.etree.ElementTree as ET

import pytest

from t12_runtime_support import configured, fs, encode, read_json, observation, parse, render, NS
from task_console import adoption, registration as reg, runtime_windows
from task_console.__main__ import main
from task_console.runtime_storage import digest
from test_authority_bootstrap import initial, approved, Crash, forbidden


@pytest.fixture(autouse=True)
def no_live_transport(monkeypatch):
    # The isolated runner installs the same barriers before collection.
    monkeypatch.setattr(runtime_windows, 'COMTransport', forbidden)
    monkeypatch.setattr(runtime_windows, 'DPAPIProtector', forbidden)
    monkeypatch.setattr(runtime_windows, 'powershell', forbidden)


def expand(root, runtime, source, count=47, running=8):
    """Declare `count` synthetic root tasks and observe `running` of them busy."""
    config = read_json(root / 'private/config.json')
    request = read_json(runtime.authority.config.desired)
    first = next(iter(request['bindings']['tasks']))
    template = deepcopy(request['components'][0]['tasks'][0])
    binding = deepcopy(request['bindings']['tasks'][first])
    ids = [first]
    for i in range(1, count):
        item = deepcopy(template)
        item['id'] = 'job' + str(i)
        request['components'][0]['tasks'].append(item)
        task_id = first.rsplit('/', 1)[0] + '/' + item['id']
        ids.append(task_id)
        request['bindings']['tasks'][task_id] = dict(deepcopy(binding), name='AcmeJob' + str(i))
        config['active_xml'][task_id] = None
    runtime.authority.config.desired.write_bytes(encode(request))
    (root / 'private/config.json').write_bytes(encode(config))
    Path(runtime.authority.config.paths['bindings']).write_bytes(encode(request['bindings']))
    tasks = read_json(root / 'synthetic-scheduler.json')
    specs = {spec['task_id']: spec for spec in reg._compile({'request': request})['task_specs']}
    for position, task_id in enumerate(ids[1:], 1):
        spec = specs[task_id]
        value = render(spec, {'state': 'absent'}, False)
        value['running'] = position <= running
        tasks[spec['name']] = observation(spec['name'], value)
    fs.atomic_replace(root / 'synthetic-scheduler.json', encode(tasks))
    owner = read_json(source)
    owner['input_revision'] = reg.compile_plan(request)['input_revision']
    owner['tasks'] = {task_id: {'status': 'reviewed', 'reason_codes': []} for task_id in ids}
    owner['sources'][str(runtime.authority.config.desired)] = digest(runtime.authority.config.desired.read_bytes())
    source.write_bytes(encode(owner))
    return configured(root), ids


def crash_at(runtime, source, point):
    plan = approved(runtime, source)
    runtime.checkpoint = lambda at: (_ for _ in ()).throw(Crash()) if at == point else None
    with pytest.raises(Crash):
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    return plan, runtime.journal.list()[0]['transaction_id']


def cli(args, runtime, capsys):
    code = main(args, runtime=runtime)
    return code, json.loads(capsys.readouterr().out)


def scheduler_state(root):
    return read_json(Path(root) / 'synthetic-scheduler.json')


def test_metadata_bootstrap_keeps_running_visible_without_all_task_idle(tmp_path):
    runtime, _, source = initial(tmp_path)
    runtime, ids = expand(tmp_path, runtime, source)
    plan = adoption.build_plan(runtime, source_binding=source)
    assert plan['task_count'] == 47 and len(plan['tasks']) == 47
    assert plan['applicable'] and plan['blockers'] == []
    assert len(plan['running_tasks']) == 8 and ids[0] not in plan['running_tasks']
    assert sum(1 for row in plan['tasks'] if row['running']) == 8
    assert all(row['state'] == 'present' and row['identity'] for row in plan['tasks'])
    before = scheduler_state(tmp_path)
    result = adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    assert result['status'] == 'committed' and result['cleaned'] and result['migrated_tasks'] == 0
    assert result['task_count'] == 47
    # Complete source authorization and the metadata proofs are still required.
    receipt = runtime.authority.receipt()
    assert set(receipt['ownership']) == set(ids)
    assert receipt['authority'] == {'authority_epoch': 0, 'migrated_tasks': []}
    # No daemon was stopped and no Scheduler payload was written.
    assert {op for op, _ in runtime.scheduler.transport.calls} == {'query'}
    assert scheduler_state(tmp_path) == before


def test_later_selected_task_mutation_retains_the_idle_gate(tmp_path):
    runtime, _, source = initial(tmp_path)
    runtime, ids = expand(tmp_path, runtime, source)
    plan = adoption.build_plan(runtime, source_binding=source)
    adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    tasks = scheduler_state(tmp_path)
    selected = runtime.load()['ownership'][ids[0]]['name']
    unselected = {name: value for name, value in tasks.items() if name != selected}
    # The idle first task migrates while eight unrelated daemons keep running.
    migration = reg.build_plan(runtime, [ids[0]], migrate=True)
    applied = reg.apply(migration, migration['input_revision'], runtime=runtime)
    assert applied['status'] == 'committed' and applied['cleaned']
    assert runtime.load()['request']['machine']['migrated_tasks'] == [ids[0]]
    current = scheduler_state(tmp_path)
    assert all(current[name] == value for name, value in unselected.items())
    # Registration changing that task's own definition still demands quiescence.
    current[selected]['value']['running'] = True
    fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode(current))
    with pytest.raises(reg.Conflict, match='busy'):
        reg.build_plan(configured(tmp_path), [ids[0]], operation='disable')


def test_running_volatility_between_approval_and_publication_is_accepted(tmp_path):
    runtime, task_id, source = initial(tmp_path)
    plan = approved(runtime, source)
    assert plan['running_tasks'] == [] and plan['tasks'][0]['running'] is False
    tasks = scheduler_state(tmp_path)
    tasks['AcmeSync']['value']['running'] = True
    fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode(tasks))
    result = adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    assert result['status'] == 'committed' and result['cleaned']
    assert runtime.authority.receipt()['ownership'][task_id]['state'] == 'present'
    assert {op for op, _ in runtime.scheduler.transport.calls} == {'query'}


@pytest.mark.parametrize('drift', ['definition', 'enabled', 'absence'])
def test_metadata_drift_refused_even_while_running_is_volatile(tmp_path, drift):
    runtime, _, source = initial(tmp_path)
    plan = approved(runtime, source)
    tasks = scheduler_state(tmp_path)
    if drift == 'absence':
        tasks.pop('AcmeSync')
    else:
        item = tasks['AcmeSync']['value']
        if drift == 'definition':
            tree = parse(item['xml'])
            tree.find('./{' + NS + '}Settings/{' + NS + '}Priority').text = '8'
            item['xml'] = ET.tostring(tree, encoding='unicode')
        else:
            spec = reg._compile({'request': read_json(runtime.authority.config.desired)})['task_specs'][0]
            item = render(spec, {'state': 'absent'}, True)
        item['running'] = True  # Volatile state changes alongside the metadata.
        tasks['AcmeSync'] = observation('AcmeSync', item)
    fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode(tasks))
    with pytest.raises(reg.Conflict, match='bootstrap_plan_changed'):
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    assert not runtime.authority.config.adoption.exists()
    assert not runtime.authority.pointer.exists()


def test_terminal_committed_bootstrap_retires_after_source_identity_replaced(tmp_path):
    runtime, _, source = initial(tmp_path)
    plan, tx = crash_at(runtime, source, 'bootstrap_committed')
    runtime = configured(tmp_path)
    assert runtime.load()['authority_generation'] == tx
    maintained = tmp_path / 'private/maintained.py'
    previous = fs.identity(maintained)
    fs.atomic_replace(maintained, maintained.read_bytes())  # equal bytes, new identity
    assert fs.identity(maintained) != previous
    assert len(list((tmp_path / 'state/stages').glob('*.json'))) == 3
    record = runtime.journal.load(tx)
    # The original explicit approval is still the only key to this transaction.
    with pytest.raises(reg.Conflict, match='approval_revision_required'):
        adoption.resume(tx, 'sha256:' + '0' * 64, runtime=runtime)
    assert runtime.journal.load(tx) == record
    result = adoption.resume(tx, plan['plan_revision'], runtime=runtime)
    assert result['ok'] and result['cleaned'] and result['transaction_id'] == tx
    assert not list((tmp_path / 'state/stages').glob('*.json'))
    assert not list(tmp_path.rglob('.t12-stage-*'))
    assert runtime.journal.pending(['authority']) == []
    assert adoption.resume(tx, plan['plan_revision'], runtime=configured(tmp_path))['cleaned']
    migration = reg.build_plan(runtime, [next(iter(runtime.load()['ownership']))], migrate=True)
    assert reg.apply(migration, migration['input_revision'], runtime=runtime)['status'] == 'committed'


@pytest.mark.parametrize('target', ['adoption', 'pointer', 'receipt'])
def test_terminal_retirement_preserves_foreign_equal_byte_authority(tmp_path, target):
    runtime, _, source = initial(tmp_path)
    plan, tx = crash_at(runtime, source, 'bootstrap_committed')
    runtime = configured(tmp_path)
    config = runtime.authority.config
    path = {'adoption': config.adoption, 'pointer': runtime.authority.pointer,
            'receipt': config.state_root / 'receipts' / (tx + '.json')}[target]
    before = path.read_bytes()
    fs.atomic_replace(path, before)  # equal bytes, foreign identity
    foreign = fs.identity(path)
    with pytest.raises(reg.Conflict):
        adoption.resume(tx, plan['plan_revision'], runtime=runtime)
    assert path.read_bytes() == before and fs.identity(path) == foreign
    assert len(list((tmp_path / 'state/stages').glob('*.json'))) == 3
    assert not runtime.journal.load(tx)['cleaned']


@pytest.mark.parametrize('point', ['bootstrap_staged', 'bootstrap_receipt_written',
                                   'bootstrap_pointer_written', 'bootstrap_committed'])
def test_generic_recover_and_status_are_safe_and_actionable(tmp_path, capsys, point):
    runtime, _, source = initial(tmp_path)
    plan, tx = crash_at(runtime, source, point)
    runtime = configured(tmp_path)
    record = runtime.journal.load(tx)
    code, status = cli(['runtime-status'], runtime, capsys)
    assert code == 0 and status['pending'] == [tx] and status['pending_bootstrap'] == [tx]
    assert status['action_required'] == 'adoption-resume'
    assert status['initialized'] is (point != 'bootstrap_staged')
    code, refusal = cli(['recover', '--transaction-id', tx], runtime, capsys)
    assert code == 3 and refusal['kind'] == 'initial-adoption'
    assert refusal['transaction_id'] == tx and refusal['action_required'] == 'adoption-resume'
    assert refusal['failure'] == {'code': 'bootstrap_recovery_required', 'field': 'journal'}
    assert refusal['ok'] is False and 'adoption-resume' in refusal['message']
    # Recognized before any rollback, cleanup or result construction.
    assert runtime.journal.load(tx) == record
    assert (point != 'bootstrap_staged') is runtime.authority.config.adoption.exists()
    # The named command still requires the original explicit approval.
    assert main(['adoption-resume', '--transaction-id', tx], runtime=runtime) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'invalid_arguments'
    assert runtime.journal.load(tx) == record
    code, repaired = cli(['adoption-resume', '--transaction-id', tx,
                          '--approval-revision', plan['plan_revision']], runtime, capsys)
    assert code == 0 and repaired['cleaned'] and repaired['transaction_id'] == tx
    code, status = cli(['runtime-status'], runtime, capsys)
    assert code == 0 and status['pending'] == [] and status['pending_bootstrap'] == []
    assert status['action_required'] is None and status['authority'] == 'available'
    assert status['generation'] == tx and status['authority_epoch'] == 0
    code, done = cli(['recover', '--transaction-id', tx], runtime, capsys)
    assert code == 0 and done['ok'] is True and done['action_required'] is None
