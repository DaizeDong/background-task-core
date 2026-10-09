"""Reviewed authority acceptance using real storage and synthetic transports."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from t12_runtime_support import (setup, configured, fs, encode, read_json, observation,
                                 FakeProtector, SyntheticScheduler)
from task_console import reconcile, registration as reg, runtime_windows
from task_console.runtime import RuntimeConfig, create_runtime
from task_console.runtime_storage import digest
from tools.make_fixtures import add_creation_candidate


class Crash(BaseException):
    pass


def forbidden(*args, **kwargs):
    raise AssertionError('Synthetic reconciliation tests must not use live transports')


@pytest.fixture(autouse=True)
def no_live_transport(monkeypatch):
    for name in ('COMTransport', 'DPAPIProtector', 'powershell'):
        monkeypatch.setattr(runtime_windows, name, forbidden)


def committed(root):
    runtime, task_id = setup(root)
    plan = reg.build_plan(runtime, [task_id], migrate=True)
    result = reg.apply(plan, plan['input_revision'], runtime=runtime)
    assert result['status'] == 'committed' and result['cleaned'], result
    runtime.scheduler.transport.calls.clear()
    return runtime, task_id


def review_edit(runtime):
    path = runtime.authority.config.paths['TaskNames.ps1']
    receipt = runtime.authority.receipt()
    expected = receipt['file_ownership'][path]
    target = Path(path)
    target.write_bytes(target.read_bytes() + b'\n# Synthetic reviewed change\n')
    return {'schemaVersion': 1, 'reason': 'Accept independently reviewed synthetic edit',
            'task_ids': list(receipt['ownership']),
            'files': [{'path': path, 'expected_receipt_fingerprint': expected,
                       'reviewed_fingerprint': reg.fingerprint(runtime.files.read(path)),
                       'reason': 'Synthetic annotation preserves the task declaration'}]}


def ready(root):
    runtime, task_id = committed(root)
    review = review_edit(runtime)
    return runtime, task_id, review, reconcile.build_plan(runtime, review)


def tree(root):
    """Track bytes, identities and directories so read-only claims cover creation."""
    return {str(p.relative_to(root)): (p.read_bytes(), fs.identity(p)) if p.is_file() else None
            for p in Path(root).rglob('*')}


def existing_artifacts(root):
    return {'private': tree(Path(root) / 'private'),
            'scheduler': ((Path(root) / 'synthetic-scheduler.json').read_bytes(),
                          fs.identity(Path(root) / 'synthetic-scheduler.json'))}


def assert_query_only(runtime):
    assert {operation for operation, _ in runtime.scheduler.transport.calls} <= {'query'}


def rewrite_receipt(runtime, mutate):
    receipt = runtime.authority.receipt()
    mutate(receipt)
    path = runtime.authority.config.state_root / 'receipts' / (receipt['generation'] + '.json')
    fs.atomic_replace(path, encode(receipt))
    fs.atomic_replace(runtime.authority.pointer, encode({
        'generation': receipt['generation'], 'receipt_digest': digest(encode(receipt))}))


def test_plan_is_pure_and_ordinary_registration_still_refuses(tmp_path):
    runtime, task_id = committed(tmp_path)
    ordinary = reg.build_plan(runtime, [task_id])
    review = review_edit(runtime)
    before = tree(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    assert tree(tmp_path) == before
    assert plan['review'] == review and plan['kind'] == reconcile.KIND
    assert_query_only(runtime)
    with pytest.raises(reg.Conflict, match='file_ownership_changed'):
        reg.build_plan(runtime, [task_id])
    with pytest.raises(reg.Conflict, match='file_ownership_changed'):
        reg.apply(ordinary, ordinary['input_revision'], runtime=runtime)
    assert tree(tmp_path) == before


def test_acceptance_preserves_existing_files_running_task_and_effective_inputs(tmp_path):
    runtime, task_id = committed(tmp_path)
    controlled = reg.control('AcmeSync', 'enable', runtime=runtime)
    assert controlled['status'] == 'committed', controlled
    old_receipt = deepcopy(runtime.authority.receipt())
    old_inputs = runtime.authority._inputs(old_receipt['input_reference'])
    assert old_inputs['submitted']['bindings']['tasks'][task_id]['enabled'] is False
    assert old_inputs['request']['bindings']['tasks'][task_id]['enabled'] is True
    review = review_edit(runtime)
    tasks = read_json(tmp_path / 'synthetic-scheduler.json')
    tasks['AcmeSync']['value']['running'] = True
    fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode(tasks))
    before = existing_artifacts(tmp_path)
    runtime.scheduler.transport.calls.clear()
    plan = reconcile.build_plan(runtime, review)
    result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert result['ok'] and result['cleaned'], result
    assert existing_artifacts(tmp_path) == before
    assert_query_only(runtime)
    receipt = runtime.authority.receipt()
    assert receipt['ownership'] == old_receipt['ownership']
    assert receipt['authority'] == old_receipt['authority']
    assert runtime.authority._inputs(receipt['input_reference']) == old_inputs
    assert receipt['generation'] == result['transaction_id'] != old_receipt['generation']
    expected_files = dict(old_receipt['file_ownership'])
    expected_files[review['files'][0]['path']] = review['files'][0]['reviewed_fingerprint']
    assert receipt['file_ownership'] == expected_files
    record = runtime.journal.load(result['transaction_id'])
    assert record['steps'] == [] and 'scheduler_before' not in record
    assert all(not key.startswith('task:') for key in record['locks'])
    for path, reference in record['file_before'].items():
        assert runtime.vault.get(reference) == runtime.files.read(path)


def test_acceptance_keeps_retired_task_absent(tmp_path):
    runtime, task_id = committed(tmp_path)
    plan = reg.build_plan(runtime, [task_id], operation='retire', reason='Synthetic retirement')
    result = reg.apply(plan, plan['input_revision'], runtime=runtime)
    assert result['status'] == 'committed', result
    assert runtime.scheduler.read('AcmeSync') == {'state': 'absent'}
    old_receipt = deepcopy(runtime.authority.receipt())
    review = review_edit(runtime)
    before = existing_artifacts(tmp_path)
    runtime.scheduler.transport.calls.clear()
    plan = reconcile.build_plan(runtime, review)
    result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert result['ok'], result
    assert existing_artifacts(tmp_path) == before
    assert runtime.authority.receipt()['ownership'] == old_receipt['ownership']
    assert runtime.authority.receipt()['authority'] == old_receipt['authority']
    assert runtime.scheduler.read('AcmeSync') == {'state': 'absent'}
    assert_query_only(runtime)


@pytest.mark.parametrize('change', [
    'selected', 'unselected', 'desired', 'config', 'scheduler', 'receipt', 'pointer',
])
def test_apply_rejects_observation_drift_without_writes(tmp_path, change):
    runtime, _, _, plan = ready(tmp_path)
    config = runtime.authority.config
    if change in ('selected', 'unselected', 'desired', 'config'):
        paths = {'selected': config.paths['TaskNames.ps1'], 'unselected': config.paths['categories.json'],
                 'desired': config.desired, 'config': config.source_path}
        target = Path(paths[change])
        fs.atomic_replace(target, target.read_bytes() + b' ')
    elif change == 'scheduler':
        tasks = read_json(tmp_path / 'synthetic-scheduler.json')
        tasks['AcmeSync']['identity'] = 'task-definition:' + 'f' * 64
        fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode(tasks))
    elif change == 'receipt':
        rewrite_receipt(runtime, lambda receipt: receipt.update(synthetic_note='Reviewed later'))
    elif change == 'pointer':
        pointer = read_json(runtime.authority.pointer)
        pointer['receipt_digest'] = '0' * 64
        fs.atomic_replace(runtime.authority.pointer, encode(pointer))
    before = tree(tmp_path)
    with pytest.raises(reg.Conflict):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert tree(tmp_path) == before
    assert_query_only(runtime)


@pytest.mark.parametrize('invalid', [
    'empty', 'duplicate', 'foreign', 'wrong_old_hash', 'wrong_current_hash', 'missing_ownership',
])
def test_plan_requires_explicit_owned_review(tmp_path, invalid):
    runtime, _, review, _ = ready(tmp_path)
    if invalid == 'empty':
        review['files'] = []
    elif invalid == 'duplicate':
        review['files'].append(deepcopy(review['files'][0]))
    elif invalid == 'foreign':
        review['files'][0]['path'] = str(tmp_path / 'foreign.txt')
    elif invalid == 'wrong_old_hash':
        review['files'][0]['expected_receipt_fingerprint'] = 'sha256:' + '0' * 64
    elif invalid == 'wrong_current_hash':
        review['files'][0]['reviewed_fingerprint'] = 'sha256:' + '0' * 64
    elif invalid == 'missing_ownership':
        rewrite_receipt(runtime, lambda receipt: receipt['file_ownership'].pop(review['files'][0]['path']))
    before = tree(tmp_path)
    with pytest.raises(reg.Conflict):
        reconcile.build_plan(runtime, review)
    assert tree(tmp_path) == before
    assert_query_only(runtime)


def test_unreviewed_managed_drift_is_rejected(tmp_path):
    runtime, _, review, _ = ready(tmp_path)
    path = Path(runtime.authority.config.paths['categories.json'])
    path.write_bytes(path.read_bytes() + b' ')
    before = tree(tmp_path)
    with pytest.raises(reg.Conflict, match='unreviewed_file_drift'):
        reconcile.build_plan(runtime, review)
    assert tree(tmp_path) == before


@pytest.mark.parametrize('invalid', ['approval', 'plan'])
def test_approval_binds_the_exact_plan(tmp_path, invalid):
    runtime, _, _, plan = ready(tmp_path)
    approval = plan['approval_revision']
    if invalid == 'approval':
        approval = 'sha256:' + '0' * 64
    else:
        plan['review']['reason'] = 'Different synthetic review'
    before = tree(tmp_path)
    with pytest.raises(reg.Conflict, match='review_approval_mismatch'):
        reconcile.apply(plan, approval, runtime=runtime)
    assert tree(tmp_path) == before


def test_configuration_source_required(tmp_path):
    runtime, _, review, _ = ready(tmp_path)
    runtime.authority.config = replace(runtime.authority.config, source_path=None)
    before = tree(tmp_path)
    with pytest.raises(reg.Conflict, match='configured_authority_required'):
        reconcile.build_plan(runtime, review)
    assert tree(tmp_path) == before


def test_same_runtime_must_reread_configuration(tmp_path):
    runtime, _, review, _ = ready(tmp_path)
    source = runtime.authority.config.source_path
    document = read_json(source)
    task_id = next(iter(document['launchers']))
    document['launchers'][task_id] = 'alternate-synthetic.vbs'
    fs.atomic_replace(source, encode(document))
    before = tree(tmp_path)
    with pytest.raises(reg.Conflict, match='runtime_configuration_changed'):
        reconcile.build_plan(runtime, review)
    assert tree(tmp_path) == before


def test_review_does_not_follow_reparse_file(tmp_path):
    runtime, _, review, _ = ready(tmp_path)
    target = Path(review['files'][0]['path'])
    outside = tmp_path / 'synthetic-link-target.txt'
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError as exc:
        pytest.skip('Host cannot create a synthetic symlink: ' + str(exc.winerror))
    before = outside.read_bytes(), fs.identity(outside)
    with pytest.raises((reg.Conflict, ValueError, OSError)):
        reconcile.build_plan(runtime, review)
    assert (outside.read_bytes(), fs.identity(outside)) == before
    assert target.is_symlink()


CRASH_POINTS = [('reconcile_journal_created', 1)] + [
    ('reconcile_evidence_saved', occurrence) for occurrence in range(1, 12)
] + [(point, 1) for point in ('receipt_intent', 'receipt_written', 'pointer_published')]


@pytest.mark.parametrize('point,occurrence', CRASH_POINTS)
def test_crash_recovery_requires_approved_resume_and_is_idempotent(tmp_path, point, occurrence):
    runtime, _, _, plan = ready(tmp_path)
    before = existing_artifacts(tmp_path)
    seen = 0
    def checkpoint(value):
        nonlocal seen
        if value == point:
            seen += 1
            if seen == occurrence:
                raise Crash()
    runtime.checkpoint = checkpoint
    with pytest.raises(Crash):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert seen == occurrence
    record = next(record for record in runtime.journal.list() if 'reconcile' in record)
    transaction_id = record['transaction_id']
    assert existing_artifacts(tmp_path) == before
    fresh = configured(tmp_path)
    all_before = tree(tmp_path)
    ordinary = reg.recover(transaction_id, runtime=fresh)
    assert ordinary['action_required'] == 'reconcile-resume'
    assert not ordinary['ok']
    assert tree(tmp_path) == all_before
    with pytest.raises(reg.Conflict, match='review_approval_mismatch'):
        reconcile.resume(transaction_id, 'sha256:' + '0' * 64, runtime=fresh)
    assert tree(tmp_path) == all_before
    result = reconcile.resume(transaction_id, plan['approval_revision'], runtime=fresh)
    assert result['ok'] and result['cleaned'], result
    assert existing_artifacts(tmp_path) == before
    assert fresh.journal.pending(['authority']) == []
    assert_query_only(fresh)
    completed = tree(tmp_path)
    assert reconcile.resume(transaction_id, plan['approval_revision'], runtime=configured(tmp_path)) == result
    assert tree(tmp_path) == completed


@pytest.mark.parametrize('after', [False, True])
def test_resume_after_unjournaled_protected_write(tmp_path, monkeypatch, after):
    runtime, _, _, plan = ready(tmp_path)
    before = existing_artifacts(tmp_path)
    put = runtime.vault.put
    def interrupted(key, value):
        if not after:
            raise Crash()
        put(key, value)
        raise Crash()
    monkeypatch.setattr(runtime.vault, 'put', interrupted)
    with pytest.raises(Crash):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    record = next(record for record in runtime.journal.list() if 'reconcile' in record)
    fresh = configured(tmp_path)
    result = reconcile.resume(record['transaction_id'], plan['approval_revision'], runtime=fresh)
    assert result['ok'], result
    assert existing_artifacts(tmp_path) == before
    assert_query_only(fresh)


@pytest.mark.parametrize('tamper', [
    'ownership_writer', 'ownership_identity', 'input_reference', 'previous',
    'extra_file_before', 'extra_task_evidence', 'completion_status', 'false_cleaned_status',
])
def test_resume_rejects_tampered_completion_before_publication(tmp_path, tamper):
    runtime, task_id, _, plan = ready(tmp_path)
    def checkpoint(point):
        if point == 'receipt_intent':
            raise Crash()
    runtime.checkpoint = checkpoint
    with pytest.raises(Crash):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    record = next(record for record in runtime.journal.list() if 'reconcile' in record)
    tx = record['transaction_id']
    completion = record['completion']
    assert record['status'] == 'committing'
    target_receipt = runtime.authority.config.state_root / 'receipts' / (tx + '.json')
    assert not target_receipt.exists()
    if tamper == 'ownership_writer':
        completion['receipt']['ownership'][task_id]['writer'] = 'legacy'
    elif tamper == 'ownership_identity':
        completion['receipt']['ownership'][task_id]['identity'] = 'task-definition:' + 'f' * 64
    elif tamper == 'input_reference':
        altered = deepcopy(runtime.vault.get(record['input_after']))
        altered['value']['request']['bindings']['tasks'][task_id]['enabled'] = True
        reference = runtime.vault.put(tx + ':synthetic-altered-input', altered)
        assert runtime.authority._inputs(reference) != runtime.authority._inputs(record['input_after'])
        completion['receipt']['input_reference'] = reference
    elif tamper == 'previous':
        completion['previous'] = {'generation': 'f' * 32, 'receipt_digest': '0' * 64}
    elif tamper == 'extra_file_before':
        reference = runtime.vault.put(tx + ':synthetic-extra-file', {'state': 'absent'})
        record['file_before'][str(tmp_path / 'private/unreviewed.txt')] = reference
    elif tamper == 'extra_task_evidence':
        evidence = record['reconcile']['evidence']['tasks']
        evidence['unreviewed/task'] = evidence[task_id]
    elif tamper == 'completion_status':
        completion['status'] = 'rolled_back'
    elif tamper == 'false_cleaned_status':
        record['status'], record['cleaned'] = 'committed', True
    runtime.journal.save(tx, record)
    before = existing_artifacts(tmp_path)
    pointer_before = runtime.authority.pointer.read_bytes(), fs.identity(runtime.authority.pointer)
    receipts_before = tree(runtime.authority.config.state_root / 'receipts')
    fresh = configured(tmp_path)
    try:
        result = reconcile.resume(tx, plan['approval_revision'], runtime=fresh)
    except reg.Conflict:
        pass
    else:
        assert not result['ok'] and result['failure'], result
    assert existing_artifacts(tmp_path) == before
    assert (fresh.authority.pointer.read_bytes(), fs.identity(fresh.authority.pointer)) == pointer_before
    assert tree(fresh.authority.config.state_root / 'receipts') == receipts_before
    assert not target_receipt.exists()
    assert_query_only(fresh)


@pytest.mark.parametrize('tamper', ['epoch', 'writer', 'name', 'unknown_id'])
def test_plan_rejects_hash_consistent_but_invalid_authority(tmp_path, tamper):
    runtime, task_id = committed(tmp_path)
    if tamper == 'name':
        scheduler_path = tmp_path / 'synthetic-scheduler.json'
        tasks = read_json(scheduler_path)
        foreign_name = 'AcmeUnrelated'
        tasks[foreign_name] = observation(foreign_name, tasks['AcmeSync']['value'])
        fs.atomic_replace(scheduler_path, encode(tasks))
    def mutate(receipt):
        proof = receipt['ownership'][task_id]
        if tamper == 'epoch':
            proof['epoch'] = receipt['authority']['authority_epoch'] + 1
        elif tamper == 'writer':
            proof['writer'] = 'legacy'
        elif tamper == 'name':
            proof['name'] = foreign_name
            proof['identity'] = tasks[foreign_name]['identity']
        elif tamper == 'unknown_id':
            receipt['ownership']['unreviewed/task'] = deepcopy(proof)
    rewrite_receipt(runtime, mutate)
    # The ordinary receipt loader accepts its digest. Reconciliation must also
    # establish semantic ownership against the protected effective inputs.
    assert runtime.authority.receipt()
    review = review_edit(runtime)
    before = tree(tmp_path)
    runtime.scheduler.transport.calls.clear()
    with pytest.raises(reg.Conflict):
        reconcile.build_plan(runtime, review)
    assert tree(tmp_path) == before
    assert_query_only(runtime)


def test_cli_reads_review_and_requires_explicit_apply_approval(tmp_path, capsys):
    from task_console.__main__ import main
    runtime, _, review, _ = ready(tmp_path)
    review_path = tmp_path / 'synthetic-review.json'
    review_path.write_bytes(encode(review))
    before = tree(tmp_path)
    assert main(['reconcile-plan', '--request', str(review_path)], runtime=runtime) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['review'] == review
    assert tree(tmp_path) == before
    plan_path = tmp_path / 'synthetic-plan.json'
    plan_path.write_bytes(encode(plan))
    before = tree(tmp_path)
    assert main(['reconcile-apply', '--request', str(plan_path)], runtime=runtime) == 2
    assert 'error' in json.loads(capsys.readouterr().out)
    assert tree(tmp_path) == before
    artifacts = existing_artifacts(tmp_path)
    assert main(['reconcile-apply', '--request', str(plan_path), '--approval-revision',
                 plan['approval_revision']], runtime=runtime) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['ok'] and result['cleaned'], result
    assert existing_artifacts(tmp_path) == artifacts
    assert_query_only(runtime)


@pytest.mark.parametrize('extra', [
    ['--task-id', 'acme-maintenance/sync'], ['--operation', 'disable'],
    ['--reason', 'Unbound synthetic reason'], ['--source-binding', 'synthetic-binding.json'],
])
def test_cli_rejects_unrelated_operation_arguments(tmp_path, capsys, extra):
    from task_console.__main__ import main
    runtime, _, review, _ = ready(tmp_path)
    path = tmp_path / 'synthetic-review.json'
    path.write_bytes(encode(review))
    before = tree(tmp_path)
    assert main(['reconcile-plan', '--request', str(path), *extra], runtime=runtime) == 2
    assert 'error' in json.loads(capsys.readouterr().out)
    assert tree(tmp_path) == before


def test_cli_pending_status_and_recover_are_read_only_then_explicit_resume(tmp_path, capsys):
    from task_console.__main__ import main
    runtime, _, _, plan = ready(tmp_path)
    def checkpoint(point):
        if point == 'reconcile_journal_created':
            raise Crash()
    runtime.checkpoint = checkpoint
    with pytest.raises(Crash):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    record = next(record for record in runtime.journal.list() if 'reconcile' in record)
    tx = record['transaction_id']
    fresh = configured(tmp_path)
    before = tree(tmp_path)
    assert main(['runtime-status'], runtime=fresh) == 0
    status = json.loads(capsys.readouterr().out)
    assert status['pending_reconciliation'] == [tx]
    assert status['action_required'] == 'reconcile-resume'
    assert main(['recover', '--transaction-id', tx], runtime=fresh) == 3
    recovery = json.loads(capsys.readouterr().out)
    assert not recovery['ok'] and recovery['action_required'] == 'reconcile-resume'
    assert main(['reconcile-resume', '--transaction-id', tx], runtime=fresh) == 2
    assert 'error' in json.loads(capsys.readouterr().out)
    assert tree(tmp_path) == before
    artifacts = existing_artifacts(tmp_path)
    assert main(['reconcile-resume', '--transaction-id', tx, '--approval-revision',
                 plan['approval_revision']], runtime=fresh) == 0
    assert json.loads(capsys.readouterr().out)['ok']
    assert existing_artifacts(tmp_path) == artifacts
    assert_query_only(fresh)


def test_relative_config_source_stays_absolute_after_cwd_change(tmp_path, monkeypatch):
    runtime, _, review, _ = ready(tmp_path)
    monkeypatch.chdir(tmp_path)
    config = RuntimeConfig.read(Path('private/config.json'), private_root=tmp_path / 'private',
                                state_root=tmp_path / 'state', vault_root=tmp_path / 'vault')
    assert config.source_path == tmp_path / 'private/config.json'
    assert config.source_path.is_absolute()
    elsewhere = tmp_path / 'synthetic-other-cwd'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    fresh = create_runtime(config, transport=SyntheticScheduler(tmp_path), protector=FakeProtector())
    before = tree(tmp_path)
    assert reconcile.build_plan(fresh, review)['review'] == review
    assert tree(tmp_path) == before
    assert_query_only(fresh)


def test_old_completed_reconciliation_survives_later_registration(tmp_path):
    runtime, task_id, _, plan = ready(tmp_path)
    result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert result['ok'], result
    artifacts = existing_artifacts(tmp_path)
    next_plan = reg.build_plan(runtime, [task_id], operation='enable', approve_enable=True)
    later = reg.apply(next_plan, next_plan['input_revision'], runtime=runtime)
    assert later['status'] == 'committed' and later['cleaned'], later
    assert existing_artifacts(tmp_path)['private'] != artifacts['private']
    assert runtime.authority.receipt()['generation'] == later['transaction_id']
    before = tree(tmp_path)
    fresh = configured(tmp_path)
    assert reconcile.resume(result['transaction_id'], plan['approval_revision'], runtime=fresh) == result
    assert tree(tmp_path) == before
    assert fresh.authority.receipt()['generation'] == later['transaction_id']
    assert_query_only(fresh)


def two_task_runtime(root):
    """Extend the generated Acme request before establishing either task's authority."""
    from task_console.runtime_xml import render
    setup(root)
    request = read_json(root / 'private/desired.json')
    first, second = add_creation_candidate(request)
    second_name = request['bindings']['tasks'][second]['name']
    baseline = request['baseline']
    baseline['tasks'].append({**deepcopy(baseline['tasks'][0]), 'name': second_name})
    baseline['task_health']['tasks'] += [
        {**deepcopy(row), 'name': second_name} for row in baseline['task_health']['tasks']]
    baseline['task_names'] = "$TaskNames = @('AcmeSync', 'AcmeSyncCopy')\n"
    baseline['categories']['categories'][0]['tasks'].append(second_name)
    for name, value in [('desired.json', request), ('bindings.json', request['bindings']),
                        ('health.json', baseline['task_health']), ('categories.json', baseline['categories'])]:
        fs.atomic_replace(root / 'private' / name, encode(value))
    fs.atomic_replace(root / 'private/names.ps1', baseline['task_names'].encode())
    config = read_json(root / 'private/config.json')
    config['active_xml'][second] = None
    config['launchers'][second] = 'copy-hidden.vbs'
    fs.atomic_replace(root / 'private/config.json', encode(config))
    spec = next(spec for spec in reg._compile({'request': request})['task_specs'] if spec['task_id'] == second)
    tasks = read_json(root / 'synthetic-scheduler.json')
    tasks[second_name] = observation(second_name, render(spec, {'state': 'absent'}, False))
    fs.atomic_replace(root / 'synthetic-scheduler.json', encode(tasks))
    runtime = configured(root)
    receipt = read_json(root / 'private/adoption.json')
    receipt['ownership'][second] = {'name': second_name, 'task_path': '\\', 'epoch': 0,
                                   'writer': 'legacy', 'identity': tasks[second_name]['identity']}
    receipt['file_ownership'] = {path: reg.fingerprint(runtime.files.read(path)) for path in runtime.files.paths}
    fs.atomic_replace(root / 'private/adoption.json', encode(receipt))
    plan = reg.build_plan(runtime, [first, second], migrate=True)
    result = reg.apply(plan, plan['input_revision'], runtime=runtime)
    assert result['status'] == 'committed' and result['cleaned'], result
    runtime.scheduler.transport.calls.clear()
    return runtime, first, second


def external_task_edit(root, runtime, task_id, label):
    from task_console.runtime_xml import parse, node, text
    import xml.etree.ElementTree as ET
    name = runtime.authority.receipt()['ownership'][task_id]['name']
    path = root / 'synthetic-scheduler.json'
    tasks = read_json(path)
    value = deepcopy(tasks[name]['value'])
    xml = parse(value['xml'])
    text(node(xml, 'RegistrationInfo'), 'Description', label)
    value['xml'] = ET.tostring(xml, encoding='unicode')
    tasks[name] = observation(name, value)
    fs.atomic_replace(path, encode(tasks))


def test_selected_scheduler_drift_refuses_reconciliation(tmp_path):
    runtime, first, _ = two_task_runtime(tmp_path)
    external_task_edit(tmp_path, runtime, first, 'Synthetic target drift')
    review = review_edit(runtime)
    review['task_ids'] = [first]
    before = tree(tmp_path)
    with pytest.raises(reg.Conflict, match='scheduler_ownership_changed'):
        reconcile.build_plan(runtime, review)
    assert tree(tmp_path) == before
    assert_query_only(runtime)


def test_unselected_scheduler_drift_is_reported_but_never_accepted(tmp_path):
    runtime, first, second = two_task_runtime(tmp_path)
    old_proofs = deepcopy(runtime.authority.receipt()['ownership'])
    external_task_edit(tmp_path, runtime, second, 'Synthetic non-target edit')
    review = review_edit(runtime)
    review['task_ids'] = [first]
    before = existing_artifacts(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    assert plan['baseline']['unselected_scheduler_drift'] == [second]
    observed = plan['baseline']['scheduler'][second]
    assert observed['receipt_identity'] == old_proofs[second]['identity']
    assert observed['current_identity'] != observed['receipt_identity']
    result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert result['ok'], result
    assert existing_artifacts(tmp_path) == before
    assert runtime.authority.receipt()['ownership'] == old_proofs
    assert_query_only(runtime)
    next_plan = reg.build_plan(runtime, [first], operation='enable', approve_enable=True)
    later = reg.apply(next_plan, next_plan['input_revision'], runtime=runtime)
    assert later['status'] == 'committed' and later['cleaned'], later
    assert runtime.authority.receipt()['ownership'][second] == old_proofs[second]
    with pytest.raises(reg.Conflict, match='scheduler_ownership_changed'):
        reg.build_plan(runtime, [second])


def test_unselected_scheduler_second_change_invalidates_reviewed_plan(tmp_path):
    runtime, first, second = two_task_runtime(tmp_path)
    external_task_edit(tmp_path, runtime, second, 'First synthetic external edit')
    review = review_edit(runtime)
    review['task_ids'] = [first]
    plan = reconcile.build_plan(runtime, review)
    external_task_edit(tmp_path, runtime, second, 'Later synthetic external edit')
    before = tree(tmp_path)
    with pytest.raises(reg.Conflict, match='reconciliation_plan_stale'):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert tree(tmp_path) == before
    assert_query_only(runtime)


def test_unselected_drift_scope_survives_completion_crash_and_resume(tmp_path):
    runtime, first, second = two_task_runtime(tmp_path)
    old_proofs = deepcopy(runtime.authority.receipt()['ownership'])
    external_task_edit(tmp_path, runtime, second, 'Synthetic non-target edit before recovery')
    review = review_edit(runtime)
    review['task_ids'] = [first]
    plan = reconcile.build_plan(runtime, review)
    assert plan['baseline']['unselected_scheduler_drift'] == [second]
    before = existing_artifacts(tmp_path)
    def checkpoint(point):
        if point == 'receipt_intent':
            raise Crash()
    runtime.checkpoint = checkpoint
    with pytest.raises(Crash):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    record = next(record for record in runtime.journal.list() if 'reconcile' in record)
    tx = record['transaction_id']
    fresh = configured(tmp_path)
    result = reconcile.resume(tx, plan['approval_revision'], runtime=fresh)
    assert result['ok'] and result['cleaned'], result
    saved_plan = fresh.journal.load(tx)['reconcile']['plan']
    assert saved_plan == plan
    assert saved_plan['review']['task_ids'] == [first]
    assert saved_plan['baseline']['unselected_scheduler_drift'] == [second]
    assert fresh.authority.receipt()['ownership'] == old_proofs
    assert existing_artifacts(tmp_path) == before
    assert_query_only(fresh)
    completed = tree(tmp_path)
    terminal = configured(tmp_path)
    assert reconcile.resume(tx, plan['approval_revision'], runtime=terminal) == result
    assert terminal.authority.receipt()['ownership'] == old_proofs
    assert tree(tmp_path) == completed
    assert_query_only(terminal)


@pytest.mark.parametrize('marker', ['missing', 'non_dict'])
@pytest.mark.parametrize('point,status', [
    ('reconcile_journal_created', 'preparing'), ('receipt_intent', 'committing'),
])
def test_ordinary_recover_rejects_lost_reconciliation_marker_before_writing(tmp_path, marker, point, status):
    from task_console.contracts import ContractError
    runtime, _, _, plan = ready(tmp_path)
    def checkpoint(value):
        if value == point:
            raise Crash()
    runtime.checkpoint = checkpoint
    with pytest.raises(Crash):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    record = next(record for record in runtime.journal.list() if 'reconcile' in record)
    assert record['status'] == status
    tx = record['transaction_id']
    if marker == 'missing':
        record.pop('reconcile')
    else:
        record['reconcile'] = 'invalid synthetic operation marker'
    runtime.journal.save(tx, record)
    before = tree(tmp_path)
    fresh = configured(tmp_path)
    with pytest.raises(ContractError):
        reg.recover(tx, runtime=fresh)
    assert tree(tmp_path) == before
    assert fresh.scheduler.transport.calls == []


@pytest.mark.parametrize('reader_state', ['unavailable', 'error'])
@pytest.mark.parametrize('point,expected_status', [
    ('published', 'rolled_back'), ('receipt_intent', 'committed'),
])
def test_unavailable_linked_reader_retire_recovery(tmp_path, reader_state, point, expected_status):
    runtime, task_id = committed(tmp_path)
    def failed_reader(task_ids):
        raise OSError('Synthetic linked reader failure')
    runtime.linked_work_items = None if reader_state == 'unavailable' else failed_reader
    plan = reg.build_plan(runtime, [task_id], operation='retire', reason='Synthetic retirement')
    assert plan['linked_work_items'] is None
    assert plan['linked_work_items_status'] == reader_state
    before = {path: Path(path).read_bytes() for path in runtime.files.paths}
    def checkpoint(value):
        if value == point:
            raise Crash()
    runtime.checkpoint = checkpoint
    with pytest.raises(Crash):
        reg.apply(plan, plan['input_revision'], runtime=runtime)
    pending = runtime.journal.pending(['authority'])
    assert len(pending) == 1
    tx = pending[0]
    assert runtime.journal.load(tx)['linked_work_items'] is None
    fresh = configured(tmp_path)
    fresh.linked_work_items = forbidden
    result = reg.recover(tx, runtime=fresh)
    assert result['status'] == expected_status and result['cleaned'], result
    assert result['linked_work_items'] is None
    assert result['linked_work_items_status'] == reader_state
    assert result['linked_work_items_code'] == plan['linked_work_items_code']
    assert fresh.journal.pending(['authority']) == []
    if expected_status == 'rolled_back':
        assert {path: Path(path).read_bytes() for path in fresh.files.paths} == before
        assert fresh.scheduler.read('AcmeSync')['state'] == 'present'
    else:
        assert fresh.scheduler.read('AcmeSync') == {'state': 'absent'}
    assert reg.recover(tx, runtime=configured(tmp_path)) == result


@pytest.mark.parametrize('task_ids', [None, [], ['acme-maintenance/sync'] * 2, ['unreviewed/task']])
def test_review_requires_nonempty_unique_owned_task_ids(tmp_path, task_ids):
    runtime, _, review, _ = ready(tmp_path)
    if task_ids is None:
        review.pop('task_ids')
    else:
        review['task_ids'] = task_ids
    before = tree(tmp_path)
    with pytest.raises(reg.Conflict):
        reconcile.build_plan(runtime, review)
    assert tree(tmp_path) == before
