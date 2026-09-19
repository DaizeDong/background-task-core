"""Controller export/restore with synthetic Scheduler and protected storage only."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'scripts', ROOT.parent / 'fleet-guards', ROOT.parent / 'llmcall'):
    sys.path.insert(0, str(path))
from t12_runtime_support import setup, configured, fs, encode, read_json
from task_console import registration as reg
from task_console.controller import Controller


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(('TASK_CONSOLE', 'TASKCONSOLE')):
            monkeypatch.delenv(key)
    for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP'):
        monkeypatch.setenv(key, str(tmp_path))


def test_export_exact_disabled_xml_and_no_registration(tmp_path):
    runtime, task_id = setup(tmp_path)
    expected = runtime.scheduler.read('AcmeSync')
    result = Controller(runtime).export_tasks()
    assert result['status'] == 'COMPLETED'
    row = result['tasks'][0]
    assert row['task_id'] == task_id and row['snapshot'] == expected
    assert row['enabled'] is False and row['task_path'] == '\\'
    assert result['generation'] == runtime.load()['authority_generation']
    assert all(op == 'query' for op, _ in runtime.scheduler.transport.calls)


def test_legacy_repeated_controls_advance_receipts(tmp_path):
    runtime, task_id = setup(tmp_path)
    owner = Controller(runtime)
    for verb in ('enable', 'disable', 'enable', 'disable', 'retire', 'retire'):
        result = owner.action('AcmeSync', verb, reason='synthetic retirement')
        assert result['ok'], result
        bundle = runtime.load()
        assert task_id not in bundle['request']['machine']['migrated_tasks']
        observed = runtime.scheduler.read('AcmeSync')
        if verb == 'retire':
            assert observed == {'state': 'absent'}
            assert bundle['ownership'][task_id]['identity'] is None
        else:
            assert bundle['ownership'][task_id]['identity'] == observed['identity']
    result = owner.export_tasks()
    assert result['tasks'] == [] and result['excluded'][0]['task_id'] == task_id


@pytest.mark.parametrize('migrated', [False, True])
def test_restore_explicit_operation_preserves_disabled(tmp_path, migrated):
    runtime, task_id = setup(tmp_path)
    if migrated:
        proposal = reg.build_plan(runtime, [task_id], migrate=True)
        assert reg.apply(proposal, proposal['input_revision'], runtime=runtime)['ok']
    owner = Controller(runtime)
    archive = owner.export_tasks()
    proposal = owner.restore_plan(archive, [task_id])
    assert proposal['intent']['operation'] == 'restore'
    result = owner.restore(proposal, proposal['plan_revision'])
    assert result['status'] == 'COMPLETED' and result['tasks_registered']
    assert result['tasks'][0]['status'] == 'COMPLETED'
    assert result['tasks'][0]['enabled'] is False
    assert 'legacy_xml' not in result and 'xml' not in json.dumps(result)
    assert (task_id in runtime.load()['request']['machine']['migrated_tasks']) is migrated
    assert runtime.scheduler.read('AcmeSync')['value']['enabled'] is False


def test_export_absent_is_distinct_from_query_failure(tmp_path):
    runtime, _ = setup(tmp_path, absent=True)
    owner = Controller(runtime)
    assert owner.export_tasks()['tasks'][0]['status'] == 'ABSENT'
    runtime.scheduler.read = lambda name: (_ for _ in ()).throw(OSError('synthetic failure'))
    result = owner.export_tasks()
    assert result['status'] == 'INCOMPLETE' and result['tasks'][0]['status'] == 'FAILED'


def test_restore_requires_exact_approval(tmp_path):
    runtime, task_id = setup(tmp_path)
    owner = Controller(runtime)
    proposal = owner.restore_plan(owner.export_tasks(), [task_id])
    with pytest.raises(reg.Conflict, match='restore_approval_required'):
        owner.restore(proposal, 'sha256:wrong')
    assert all(op == 'query' for op, _ in runtime.scheduler.transport.calls)


def test_direct_apply_cannot_bypass_restore_approval(tmp_path):
    runtime, task_id = setup(tmp_path)
    owner = Controller(runtime)
    plan = owner.restore_plan(owner.export_tasks(), [task_id])
    with pytest.raises(reg.Conflict, match='restore_approval_required'):
        reg.apply(plan, plan['input_revision'], runtime=runtime)


def test_absent_target_requires_reviewed_rebinding_not_taskname(tmp_path):
    source, task_id = setup(tmp_path / 'source')
    archive = Controller(source).export_tasks()
    target, _ = setup(tmp_path / 'target', absent=True)
    owner = Controller(target)
    with pytest.raises(reg.Conflict, match='reviewed_rebinding_required'):
        owner.restore_plan(archive, [task_id])
    row = archive['tasks'][0]
    evidence = {task_id: {'task_id': task_id, 'source_revision': row['snapshot_revision'],
                'target_revision': reg.fingerprint({'state': 'absent'}),
                'target_principal': row['snapshot']['value']['spec']['principal'],
                'generation': target.load()['authority_generation'], 'approved': True,
                'reason': 'Synthetic fresh-machine review'}}
    plan = owner.restore_plan(archive, [task_id], rebinding=evidence)
    result = owner.restore(plan, plan['plan_revision'])
    assert result['tasks_registered'] and result['tasks'][0]['enabled'] is False


def test_unknown_scope_and_disabled_run_refuse(tmp_path):
    runtime, task_id = setup(tmp_path)
    owner = Controller(runtime)
    with pytest.raises(reg.Conflict, match='task_disabled'):
        owner.action('AcmeSync', 'run')
    with pytest.raises(reg.Conflict, match='invalid_task_selection'):
        owner.restore_plan(owner.export_tasks(), ['unknown/task'])
    path = tmp_path / 'private/adoption.json'
    receipt = read_json(path)
    receipt['ownership'][task_id]['task_path'] = '\\Vendor\\'
    fs.atomic_replace(path, encode(receipt))
    with pytest.raises(reg.Conflict, match='task_receipt_invalid'):
        owner.export_tasks()


@pytest.mark.parametrize('response,expected', [
    (None, 'unavailable'),
    ({'status': 'error', 'items': None, 'code': 'read_failed'}, 'error'),
    ({'status': 'available', 'items': {'acme-maintenance/sync': [{'id': 'synthetic-item', 'state': 'running'}]}}, 'available'),
])
def test_retirement_linked_reader_never_converts_unavailable_to_zero(tmp_path, response, expected):
    runtime, _ = setup(tmp_path)
    calls = []
    if response is not None:
        def reader(ids):
            calls.append(ids)
            return deepcopy(response)
        runtime.linked_work_items = reader
    result = Controller(runtime).action('AcmeSync', 'retire', reason='synthetic')
    assert result['ok'] and result['linked_work_items_status'] == expected
    if expected != 'available':
        assert result['linked_work_items'] is None
    else:
        assert calls == [['acme-maintenance/sync'], ['acme-maintenance/sync']]
        assert result['linked_work_items']['acme-maintenance/sync'][0]['state'] == 'running'


def test_legacy_mutation_callback_must_convert_explicitly(tmp_path):
    runtime, _ = setup(tmp_path)
    with pytest.raises(reg.Conflict, match='legacy_declarative_conversion_required'):
        Controller(runtime).action('AcmeSync', 'enable', legacy=lambda: pytest.fail('unlocked legacy mutation'))


@pytest.mark.parametrize('checkpoint', ['published', 'receipt_written'])
def test_interrupted_restore_recovery_and_retry(tmp_path, checkpoint):
    runtime, task_id = setup(tmp_path)
    owner = Controller(runtime)
    plan = owner.restore_plan(owner.export_tasks(), [task_id])
    class Interrupted(BaseException):
        pass
    fired = False
    def interrupt(point):
        nonlocal fired
        if point == checkpoint and not fired:
            fired = True
            raise Interrupted()
    runtime.checkpoint = interrupt
    with pytest.raises(Interrupted):
        owner.restore(plan, plan['plan_revision'])
    recovered = configured(tmp_path)
    record = recovered.journal.list()[0]
    owner = Controller(recovered)
    result = owner.restore_recover(record['transaction_id'], plan['plan_revision'])
    if checkpoint == 'receipt_written':
        assert result['status'] == 'COMPLETED'
        assert owner.restore_recover(record['transaction_id'], plan['plan_revision']) == result
    else:
        assert result['transaction']['status'] == 'rolled_back'
        retry = owner.restore_plan(owner.export_tasks(), [task_id])
        assert owner.restore(retry, retry['plan_revision'])['status'] == 'COMPLETED'


def test_cli_export_restore_receipts(tmp_path, capsys):
    from task_console.__main__ import main
    runtime, task_id = setup(tmp_path)
    assert main(['export'], runtime=runtime) == 0
    archive = json.loads(capsys.readouterr().out)
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'archive': archive, 'task_ids': [task_id]}), encoding='utf-8')
    assert main(['restore-plan', '--request', str(request)], runtime=runtime) == 0
    plan = json.loads(capsys.readouterr().out)
    request.write_text(json.dumps(plan), encoding='utf-8')
    assert main(['restore', '--request', str(request), '--approval-revision', plan['plan_revision']], runtime=runtime) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['tasks'][0]['status'] == 'COMPLETED' and 'legacy_xml' not in receipt


def test_export_holds_all_resource_locks_and_uses_literal_name(tmp_path):
    name = 'Acme "quoted"; $literal\u96ea'
    runtime, task_id = setup(tmp_path, name=name)
    read = runtime.scheduler.read
    def locked_read(observed):
        assert observed == name
        for key in ('authority', 'task:' + task_id, 'file:' + runtime.load()['paths']['machine']):
            with pytest.raises(reg.Conflict, match='busy'):
                with runtime.locks.hold([key]):
                    pytest.fail('export released a required lock')
        return read(observed)
    runtime.scheduler.read = locked_read
    assert Controller(runtime).export_tasks()['tasks'][0]['name'] == name


def test_changed_task_after_restore_review_never_rehashes_ownership(tmp_path):
    runtime, task_id = setup(tmp_path)
    owner = Controller(runtime)
    plan = owner.restore_plan(owner.export_tasks(), [task_id])
    tasks = read_json(tmp_path / 'synthetic-scheduler.json')
    tasks['AcmeSync']['identity'] = 'task-definition:' + 'b' * 64
    fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode(tasks))
    before = read_json(tmp_path / 'private/adoption.json')
    with pytest.raises(reg.Conflict, match='scheduler_ownership_changed'):
        owner.restore(plan, plan['plan_revision'])
    assert read_json(tmp_path / 'private/adoption.json') == before
    assert runtime.journal.list() == []


def test_new_migrated_principal_requires_exact_review(tmp_path):
    runtime, task_id = setup(tmp_path)
    migrate = reg.build_plan(runtime, [task_id], migrate=True)
    assert reg.apply(migrate, migrate['input_revision'], runtime=runtime)['ok']
    owner = Controller(runtime)
    archive = owner.export_tasks()
    desired = read_json(tmp_path / 'private/desired.json')
    principal = deepcopy(desired['bindings']['tasks'][task_id]['principal'])
    principal['user_id'] = 'AcmeReplacementService'
    desired['bindings']['tasks'][task_id]['principal'] = principal
    fs.atomic_replace(tmp_path / 'private/desired.json', encode(desired))
    with pytest.raises(reg.Conflict, match='reviewed_rebinding_required'):
        owner.restore_plan(archive, [task_id])
    evidence = {task_id: {'task_id': task_id, 'source_revision': archive['tasks'][0]['snapshot_revision'],
                'target_revision': reg.fingerprint(reg._scheduler_config(runtime.scheduler.read('AcmeSync'))),
                'target_principal': principal, 'generation': runtime.load()['authority_generation'],
                'approved': True, 'reason': 'Synthetic principal review'}}
    proposal = owner.restore_plan(archive, [task_id], rebinding=evidence)
    assert owner.restore(proposal, proposal['plan_revision'])['tasks_registered']
    assert runtime.scheduler.read('AcmeSync')['value']['spec']['principal'] == principal


def test_password_restore_reports_auth_pending_before_any_stage(tmp_path):
    from task_console.export_restore import validate_restore_target
    from task_console.runtime_xml import parse, NS
    import xml.etree.ElementTree as ET
    runtime, task_id = setup(tmp_path)
    bundle = runtime.load()
    spec = reg._compile(bundle)['task_specs'][0]
    source = runtime.scheduler.read('AcmeSync')
    tree = parse(source['value']['xml'])
    tree.find('./{' + NS + '}Principals/{' + NS + '}Principal/{' + NS + '}LogonType').text = 'Password'
    source['value']['xml'] = ET.tostring(tree, encoding='unicode')
    spec['principal']['logon_type'] = 'Password'
    row = {'task_id': task_id, 'name': spec['name'], 'task_path': '\\', 'status': 'COMPLETED',
           'snapshot': source, 'snapshot_revision': reg.revision(source), 'enabled': False}
    with pytest.raises(reg.Conflict, match='auth_pending'):
        validate_restore_target(bundle, {'restore_tasks': {task_id: row}, 'rebinding': {}}, spec, source)
    assert runtime.journal.list() == []


def test_retired_task_cannot_return_from_old_export(tmp_path):
    runtime, task_id = setup(tmp_path)
    owner = Controller(runtime)
    archive = owner.export_tasks()
    assert owner.action('AcmeSync', 'retire', reason='synthetic retirement')['ok']
    with pytest.raises(reg.Conflict, match='retired_task'):
        owner.restore_plan(archive, [task_id])
    assert owner.export_tasks()['tasks'] == []


def test_mixed_restore_preserves_legacy_projection_and_writer(tmp_path):
    from task_console.runtime_xml import observation, render
    runtime, first = setup(tmp_path)
    second = 'acme-maintenance/audit'
    request = read_json(tmp_path / 'private/desired.json')
    request['components'][0]['tasks'].append({**deepcopy(request['components'][0]['tasks'][0]), 'id': 'audit'})
    request['bindings']['tasks'][second] = {**deepcopy(request['bindings']['tasks'][first]), 'name': 'AcmeAudit'}
    baseline = request['baseline']
    baseline['tasks'].append({**deepcopy(baseline['tasks'][0]), 'name': 'AcmeAudit'})
    baseline['task_health']['tasks'] += [{**deepcopy(row), 'name': 'AcmeAudit'} for row in baseline['task_health']['tasks']]
    baseline['task_names'] = "$TaskNames = @('AcmeSync', 'AcmeAudit')\n"
    baseline['categories']['categories'][0]['tasks'].append('AcmeAudit')
    for name, value in [('desired.json', request), ('bindings.json', request['bindings']),
                        ('health.json', baseline['task_health']), ('categories.json', baseline['categories'])]:
        fs.atomic_replace(tmp_path / 'private' / name, encode(value))
    fs.atomic_replace(tmp_path / 'private/names.ps1', baseline['task_names'].encode())
    config = read_json(tmp_path / 'private/config.json')
    config['active_xml'][second] = None
    config['launchers'][second] = 'audit-hidden.vbs'
    fs.atomic_replace(tmp_path / 'private/config.json', encode(config))
    spec = next(s for s in reg._compile({'request': request})['task_specs'] if s['task_id'] == second)
    tasks = read_json(tmp_path / 'synthetic-scheduler.json')
    tasks['AcmeAudit'] = observation('AcmeAudit', render(spec, {'state': 'absent'}, False))
    fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode(tasks))
    runtime = configured(tmp_path)
    adoption = read_json(tmp_path / 'private/adoption.json')
    adoption['ownership'][second] = {'name': 'AcmeAudit', 'task_path': '\\', 'epoch': 0,
                                     'writer': 'legacy', 'identity': tasks['AcmeAudit']['identity']}
    adoption['file_ownership'] = {p: reg.fingerprint(runtime.files.read(p)) for p in runtime.files.paths}
    fs.atomic_replace(tmp_path / 'private/adoption.json', encode(adoption))
    migration = reg.build_plan(runtime, [first], migrate=True)
    assert reg.apply(migration, migration['input_revision'], runtime=runtime)['ok']
    before = [row for row in read_json(tmp_path / 'private/health.json')['tasks'] if row['name'] == 'AcmeAudit']
    owner = Controller(runtime)
    plan = owner.restore_plan(owner.export_tasks(), [first, second])
    result = owner.restore(plan, plan['plan_revision'])
    assert result['tasks_registered'] and len(result['tasks']) == 2
    assert runtime.load()['request']['machine']['migrated_tasks'] == [first]
    assert runtime.load()['ownership'][second]['writer'] == 'legacy'
    after = [row for row in read_json(tmp_path / 'private/health.json')['tasks'] if row['name'] == 'AcmeAudit']
    assert before == after
