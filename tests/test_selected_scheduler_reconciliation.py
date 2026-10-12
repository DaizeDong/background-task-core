"""Explicit Scheduler reviews use synthetic transports and preserved authority."""
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from t12_runtime_support import configured, encode, fs, read_json, observation
from task_console import adoption, reconcile, registration as reg, runtime_windows
from task_console.runtime_storage import digest
from task_console.runtime_xml import NS, parse
from tools.make_fixtures import (reconciliation_receipt_chain, selected_scheduler_reconciliation_case,
                                 selected_scheduler_review, legacy_file_reconciliation_pending)
from test_reconciliation import Crash, assert_query_only, existing_artifacts, tree


def forbidden(*args, **kwargs):
    raise AssertionError('Synthetic Scheduler reviews must not use live transports')


@pytest.fixture(autouse=True)
def no_live_transport(monkeypatch):
    for name in ('COMTransport', 'DPAPIProtector', 'powershell'):
        monkeypatch.setattr(runtime_windows, name, forbidden)


def reviewed(root, *, with_history=False, desired_run_level='LeastPrivilege', with_passthrough=False, enabled=False):
    from task_console.runtime_xml import render

    case = selected_scheduler_reconciliation_case(root, with_history=with_history,
                                                   desired_run_level=desired_run_level,
                                                   with_passthrough=with_passthrough, enabled=enabled)
    request, config = case['request'], case['config']
    values = {'bindings': request['bindings'], 'machine': request['machine'],
              'task-health.json': request['baseline']['task_health'],
              'TaskNames.ps1': request['baseline']['task_names'],
              'categories.json': request['baseline']['categories'], 'tombstones': {}}
    for role, value in values.items():
        fs.atomic_replace(root / 'private' / config['paths'][role],
                          value.encode() if isinstance(value, str) else encode(value))
    fs.atomic_replace(root / 'private/config.json', encode(config))
    fs.atomic_replace(root / 'private/desired.json', encode(request))
    compiled = reg.compile_plan(request)
    tasks = {spec['name']: observation(spec['name'], render(spec, {'state': 'absent'}, spec['enabled']))
             for spec in compiled['task_specs']}
    fs.atomic_replace(root / 'synthetic-scheduler.json', encode(tasks))
    case['source_binding']['input_revision'] = compiled['input_revision']
    fs.atomic_replace(case['source'], encode(case['source_binding']))
    fs.atomic_replace(case['maintained'], case['maintained_bytes'])
    runtime = configured(root)
    initial = adoption.build_plan(runtime, source_binding=case['source'])
    assert initial['applicable'], initial['blockers']
    adopted = adoption.apply(initial, initial['plan_revision'], runtime=runtime)
    assert adopted['status'] == 'committed' and adopted['cleaned'], adopted
    case['generations'] = [adopted['transaction_id']]
    fs.atomic_replace(runtime.authority.config.desired, encode(case['migration_request']))
    if with_history:
        historical_name = request['bindings']['tasks'][case['historical_id']]['name']
        tasks.pop(historical_name)
        fs.atomic_replace(root / 'synthetic-scheduler.json', encode(tasks))
        case['historical_name'] = historical_name
    migration = reg.build_plan(runtime, [case['active_id']], migrate=True)
    migrated = reg.apply(migration, migration['input_revision'], runtime=runtime)
    assert migrated['status'] == 'committed' and migrated['cleaned'], migrated
    case['generations'].append(migrated['transaction_id'])
    old = runtime.authority.receipt()
    fs.atomic_replace(runtime.authority.config.desired, encode(case['desired']))
    spec = next(spec for spec in reg.compile_effective(runtime.load())['task_specs']
                if spec['task_id'] == case['active_id'])
    case['name'], case['desired_spec'] = spec['name'], spec
    tasks = read_json(root / 'synthetic-scheduler.json')
    case['old_snapshot'] = deepcopy(tasks[case['name']])
    tasks[case['name']] = observation(case['name'], runtime.render(spec, tasks[case['name']], spec['enabled']))
    assert tasks[case['name']]['identity'] != old['ownership'][case['active_id']]['identity']
    assert tasks[case['name']]['value']['spec'] == spec
    fs.atomic_replace(root / 'synthetic-scheduler.json', encode(tasks))
    path = runtime.authority.config.paths['TaskNames.ps1']
    fs.atomic_replace(path, Path(path).read_bytes() + b'\n# Synthetic reviewed annotation\n')
    review = {'schemaVersion': 1, 'reason': 'Accept reviewed synthetic file and task',
              'task_ids': [case['active_id']],
              'files': [{'path': path, 'expected_receipt_fingerprint': old['file_ownership'][path],
                         'reviewed_fingerprint': reg.fingerprint(runtime.files.read(path)),
                         'reason': 'Synthetic annotation preserves declarations'}],
              'scheduler_reviews': [selected_scheduler_review(case['active_id'], case['name'],
                                    old['ownership'][case['active_id']]['identity'], tasks[case['name']])]}
    if with_history:
        review['historical_task_ids'] = [case['historical_id']]
        review['historical_receipt_chain'] = reconciliation_receipt_chain(runtime, case['generations'])
    runtime.scheduler.transport.calls.clear()
    return runtime, case, review


def test_accepts_only_reviewed_identity_and_preserves_all_other_state(tmp_path):
    runtime, case, review = reviewed(tmp_path, with_history=True)
    old = deepcopy(runtime.authority.receipt())
    unselected = dict(case, name=old['ownership'][case['unselected_id']]['name'])
    change_scheduler(tmp_path, unselected, 'action')
    old_inputs = runtime.authority._inputs(old['input_reference'])
    old_records = {key: runtime.journal.load(key) for key in case['generations']}
    old_receipts = {key: read_json(runtime.authority.config.state_root / 'receipts' / (key + '.json'))
                    for key in case['generations']}
    before = existing_artifacts(tmp_path)
    before_plan = tree(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    assert plan['baseline']['unselected_scheduler_drift'] == sorted([case['unselected_id'], case['historical_id']])
    assert tree(tmp_path) == before_plan
    result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert result['ok'] and result['cleaned'], result
    assert existing_artifacts(tmp_path) == before
    expected = deepcopy(old)
    accepted = runtime.authority.receipt()
    expected['ownership'][case['active_id']]['identity'] = review['scheduler_reviews'][0]['reviewed_identity']
    expected.update(generation=result['transaction_id'], input_reference=accepted['input_reference'])
    expected['file_ownership'][review['files'][0]['path']] = review['files'][0]['reviewed_fingerprint']
    assert accepted == expected
    assert runtime.authority._inputs(accepted['input_reference']) == old_inputs
    assert {key: runtime.journal.load(key) for key in case['generations']} == old_records
    assert {key: read_json(runtime.authority.config.state_root / 'receipts' / (key + '.json'))
            for key in case['generations']} == old_receipts
    assert runtime.scheduler.read(case['historical_name']) == {'state': 'absent'}
    record = runtime.journal.load(result['transaction_id'])
    reference = record['reconcile']['evidence']['tasks'][case['active_id']]
    assert record['scheduler_before'] == {case['name']: reference}
    assert runtime.vault.get(reference)['value']['running'] is False
    assert record['steps'] == []
    assert [key for key in record['locks'] if key.startswith('task:')] == ['task:' + case['active_id']]
    assert_query_only(runtime)
    terminal = tree(tmp_path)
    assert reconcile.resume(result['transaction_id'], plan['approval_revision'], runtime=configured(tmp_path)) == result
    assert tree(tmp_path) == terminal


def change_scheduler(root, case, change, review=None):
    tasks = read_json(root / 'synthetic-scheduler.json')
    snapshot = tasks[case['name']]
    if change == 'missing':
        tasks.pop(case['name'])
    else:
        value = deepcopy(snapshot['value'])
        if change == 'running':
            value['running'] = True
        elif change == 'enabled':
            value['enabled'] = not value['enabled']
            from task_console.runtime_xml import normalize
            value = normalize(value)
        elif change == 'spec':
            value['spec']['timeout_seconds'] += 1
            from task_console.runtime_xml import normalize
            value = normalize(value)
        elif change in ('action', 'principal', 'xml_setting', 'trigger'):
            xml = parse(value['xml'])
            path, text = {'action': ('Actions/Exec/Command', case['wrong_action']),
                          'principal': ('Principals/Principal/UserId', case['wrong_principal']),
                          'xml_setting': ('Settings/ExecutionTimeLimit', 'PT901S'),
                          'trigger': ('Triggers/CalendarTrigger/StartBoundary', case['wrong_trigger_start'])}[change]
            xml.find('./' + '/'.join('{' + NS + '}' + part for part in path.split('/'))).text = text
            value['xml'] = ET.tostring(xml, encoding='unicode')
        elif change in ('omitted_defaults', 'omitted_run_level'):
            xml = parse(value['xml'])
            fields = [('Principals/Principal', 'RunLevel')]
            if change == 'omitted_defaults':
                fields.append(('Settings', 'WakeToRun'))
                if value['enabled']:
                    fields.append(('Settings', 'Enabled'))
            for path, field in fields:
                parent = xml.find('./' + '/'.join('{' + NS + '}' + part for part in path.split('/')))
                parent.remove(parent.find('{' + NS + '}' + field))
            value['xml'] = ET.tostring(xml, encoding='unicode')
        else:
            raise AssertionError(change)
        tasks[case['name']] = observation(case['name'], value)
        if change in ('action', 'principal', 'xml_setting', 'trigger', 'omitted_defaults', 'omitted_run_level'):
            assert tasks[case['name']]['value']['spec'] == snapshot['value']['spec']
            assert parse(value['xml']).findtext('{' + NS + '}Data') == parse(snapshot['value']['xml']).findtext('{' + NS + '}Data')
    fs.atomic_replace(root / 'synthetic-scheduler.json', encode(tasks))
    if review is not None and change != 'missing':
        review['scheduler_reviews'] = [selected_scheduler_review(case['active_id'], case['name'],
            review['scheduler_reviews'][0]['expected_receipt_identity'], tasks[case['name']])]


def assert_plan_refused(runtime, root, review, *, code=None):
    before = tree(root)
    with pytest.raises(reg.ContractError, match=code):
        reconcile.build_plan(runtime, review)
    assert tree(root) == before
    assert_query_only(runtime)


@pytest.mark.parametrize('invalid', ['omitted', 'empty', 'not_list', 'not_object', 'duplicate', 'extra_field',
                                      'missing_field', 'blank_reason', 'unknown', 'unselected', 'historical',
                                      'name', 'old_identity', 'new_identity', 'fingerprint', 'unchanged_identity',
                                      'review_schema_bool'])
def test_scheduler_review_contract_refuses_invalid_selection_and_evidence(tmp_path, invalid):
    runtime, case, review = reviewed(tmp_path, with_history=invalid == 'historical')
    item = review['scheduler_reviews'][0]
    if invalid == 'omitted':
        review.pop('scheduler_reviews')
    elif invalid == 'empty':
        review['scheduler_reviews'] = []
    elif invalid == 'not_list':
        review['scheduler_reviews'] = item
    elif invalid == 'not_object':
        review['scheduler_reviews'] = [None]
    elif invalid == 'duplicate':
        review['scheduler_reviews'].append(deepcopy(item))
    elif invalid == 'extra_field':
        item['extra'] = True
    elif invalid == 'missing_field':
        item.pop('reason')
    elif invalid == 'blank_reason':
        item['reason'] = ' '
    elif invalid in ('unknown', 'unselected', 'historical'):
        key = {'unknown': 'unknown_id', 'unselected': 'unselected_id', 'historical': 'historical_id'}[invalid]
        item['task_id'] = case[key]
        if invalid != 'unknown':
            proof = runtime.authority.receipt()['ownership'][case[key]]
            item.update(name=proof['name'], expected_receipt_identity=proof['identity'])
        if invalid == 'historical':
            review['task_ids'].append(case[key])
    elif invalid == 'name':
        item['name'] = case['wrong_name']
    elif invalid == 'old_identity':
        item['expected_receipt_identity'] = case['wrong_identity']
    elif invalid == 'new_identity':
        item['reviewed_identity'] = case['wrong_identity']
    elif invalid == 'fingerprint':
        item['reviewed_fingerprint'] = 'sha256:' + '0' * 64
    elif invalid == 'unchanged_identity':
        item['reviewed_identity'] = item['expected_receipt_identity']
    elif invalid == 'review_schema_bool':
        review['schemaVersion'] = True
    assert_plan_refused(runtime, tmp_path, review,
                        code='scheduler_ownership_changed' if invalid == 'omitted' else None)


@pytest.mark.parametrize('change', ['missing', 'running', 'enabled', 'spec', 'action', 'principal', 'xml_setting', 'trigger'])
def test_even_explicit_current_fingerprint_cannot_accept_unsafe_or_unintended_definition(tmp_path, change):
    runtime, case, review = reviewed(tmp_path)
    change_scheduler(tmp_path, case, change, review)
    assert_plan_refused(runtime, tmp_path, review)


@pytest.mark.parametrize('enabled', [False, True])
def test_omitted_xml_defaults_are_accepted_with_exact_reviewed_raw_evidence(tmp_path, enabled):
    runtime, case, review = reviewed(tmp_path, enabled=enabled)
    assert runtime.authority.receipt()['ownership'][case['active_id']]['enabled'] is enabled
    change_scheduler(tmp_path, case, 'omitted_defaults', review)
    reviewed_xml = read_json(tmp_path / 'synthetic-scheduler.json')[case['name']]['value']['xml']
    xml = parse(reviewed_xml)
    assert xml.find('./{' + NS + '}Principals/{' + NS + '}Principal/{' + NS + '}RunLevel') is None
    assert xml.find('./{' + NS + '}Settings/{' + NS + '}WakeToRun') is None
    if enabled:
        assert xml.find('./{' + NS + '}Settings/{' + NS + '}Enabled') is None
    before = existing_artifacts(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert result['ok'] and result['cleaned'], result
    assert existing_artifacts(tmp_path) == before
    assert runtime.authority.receipt()['ownership'][case['active_id']]['identity'] == review['scheduler_reviews'][0]['reviewed_identity']
    record = runtime.journal.load(result['transaction_id'])
    protected = runtime.vault.get(record['scheduler_before'][case['name']])
    assert protected['value']['xml'] == reviewed_xml
    assert protected['value']['enabled'] is enabled
    assert_query_only(runtime)


def test_omitted_run_level_cannot_claim_highest_available(tmp_path):
    runtime, case, review = reviewed(tmp_path, desired_run_level='HighestAvailable')
    assert case['desired_spec']['principal']['run_level'] == 'HighestAvailable'
    change_scheduler(tmp_path, case, 'omitted_run_level', review)
    assert_plan_refused(runtime, tmp_path, review)


def test_explicit_xml_passthrough_accepts_declared_non_utc_trigger(tmp_path):
    runtime, case, review = reviewed(tmp_path, with_passthrough=True)
    assert case['desired_spec']['timezone'] != 'UTC'
    assert case['desired_spec']['xml_passthrough']['xml']
    before = existing_artifacts(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert result['ok'] and result['cleaned'], result
    assert existing_artifacts(tmp_path) == before
    assert reconcile.resume(result['transaction_id'], plan['approval_revision'], runtime=runtime) == result
    assert_query_only(runtime)


def test_non_utc_trigger_requires_explicit_xml_passthrough(tmp_path):
    from task_console.runtime_xml import normalize

    runtime, case, review = reviewed(tmp_path, with_passthrough=True)
    desired = read_json(runtime.authority.config.desired)
    desired['bindings']['tasks'][case['active_id']].pop('xml_passthrough')
    fs.atomic_replace(runtime.authority.config.desired, encode(desired))
    tasks = read_json(tmp_path / 'synthetic-scheduler.json')
    value = tasks[case['name']]['value']
    value['spec']['xml_passthrough'] = None
    tasks[case['name']] = observation(case['name'], normalize(value))
    expected = next(spec for spec in reg.compile_effective(runtime.load())['task_specs']
                    if spec['task_id'] == case['active_id'])
    assert tasks[case['name']]['value']['spec'] == expected
    fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode(tasks))
    review['scheduler_reviews'] = [selected_scheduler_review(case['active_id'], case['name'],
        review['scheduler_reviews'][0]['expected_receipt_identity'], tasks[case['name']])]
    assert_plan_refused(runtime, tmp_path, review)


@pytest.mark.parametrize('field,value', [('enabled', None), ('enabled', 0), ('state', 'absent'),
                                         ('receipt_kind', None)])
def test_review_requires_a_complete_present_owned_proof(tmp_path, field, value):
    runtime, case, review = reviewed(tmp_path)
    receipt = runtime.authority.receipt()
    if value is None:
        receipt['ownership'][case['active_id']].pop(field)
    else:
        receipt['ownership'][case['active_id']][field] = value
    path = runtime.authority.config.state_root / 'receipts' / (receipt['generation'] + '.json')
    fs.atomic_replace(path, encode(receipt))
    fs.atomic_replace(runtime.authority.pointer, encode({'generation': receipt['generation'],
                                                        'receipt_digest': digest(encode(receipt))}))
    assert_plan_refused(runtime, tmp_path, review)


@pytest.mark.parametrize('change', ['scheduler', 'desired', 'reason'])
def test_apply_rechecks_exact_review_and_current_desired_without_writes(tmp_path, change):
    runtime, case, review = reviewed(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    approval = plan['approval_revision']
    if change == 'scheduler':
        change_scheduler(tmp_path, case, 'action')
    elif change == 'desired':
        desired = read_json(runtime.authority.config.desired)
        desired['bindings']['tasks'][case['active_id']]['timeout_seconds'] += 1
        fs.atomic_replace(runtime.authority.config.desired, encode(desired))
    else:
        plan['review']['scheduler_reviews'][0]['reason'] += ' Changed after approval.'
    before = tree(tmp_path)
    with pytest.raises(reg.ContractError):
        reconcile.apply(plan, approval, runtime=runtime)
    assert tree(tmp_path) == before
    assert_query_only(runtime)


def test_plan_schema_boolean_is_rejected_even_with_matching_approval(tmp_path):
    runtime, _, review = reviewed(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    plan['schemaVersion'] = True
    plan['approval_revision'] = reg.revision({key: value for key, value in plan.items()
                                            if key != 'approval_revision'})
    before = tree(tmp_path)
    with pytest.raises(reg.ContractError):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert tree(tmp_path) == before
    assert_query_only(runtime)


CHECKPOINTS = [('reconcile_journal_created', 1)] + [
    ('reconcile_evidence_saved', occurrence) for occurrence in range(1, 14)
] + [(point, 1) for point in ('reconcile_scheduler_evidence_saved', 'receipt_intent', 'receipt_written', 'pointer_published')]


@pytest.mark.parametrize('point,occurrence', CHECKPOINTS)
def test_scheduler_race_at_every_checkpoint_is_detected(tmp_path, point, occurrence):
    runtime, case, review = reviewed(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    seen = 0
    changed = None
    def checkpoint(at):
        nonlocal seen, changed
        if at == point:
            seen += 1
            if seen == occurrence:
                change_scheduler(tmp_path, case, 'running')
                changed = existing_artifacts(tmp_path)
    runtime.checkpoint = checkpoint
    try:
        result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    except reg.ContractError:
        pass
    else:
        assert not result['ok'] and result['failure'], result
    assert seen == occurrence
    assert changed is not None and existing_artifacts(tmp_path) == changed
    assert_query_only(runtime)


@pytest.mark.parametrize('point', ['reconcile_journal_created', 'reconcile_evidence_saved',
                                   'reconcile_scheduler_evidence_saved',
                                   'receipt_intent', 'receipt_written', 'pointer_published'])
def test_crash_resume_preserves_reviewed_identity_and_full_snapshot(tmp_path, point):
    runtime, case, review = reviewed(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    before = existing_artifacts(tmp_path)
    runtime.checkpoint = lambda at: (_ for _ in ()).throw(Crash()) if at == point else None
    with pytest.raises(Crash):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    record = next(record for record in runtime.journal.list() if 'reconcile' in record)
    fresh = configured(tmp_path)
    result = reconcile.resume(record['transaction_id'], plan['approval_revision'], runtime=fresh)
    assert result['ok'] and result['cleaned'], result
    assert existing_artifacts(tmp_path) == before
    assert fresh.authority.receipt()['ownership'][case['active_id']]['identity'] == review['scheduler_reviews'][0]['reviewed_identity']
    completed = fresh.journal.load(record['transaction_id'])
    reference = completed['scheduler_before'][case['name']]
    assert reference == completed['reconcile']['evidence']['tasks'][case['active_id']]
    assert fresh.vault.get(reference)['value']['running'] is False
    assert_query_only(fresh)


@pytest.mark.parametrize('terminal', [False, True])
@pytest.mark.parametrize('tamper', ['missing', 'extra', 'wrong_ref', 'running_missing', 'running_true',
                                     'running_nonbool', 'selection', 'unselected_proof', 'epoch',
                                     'selected_enabled_type', 'input_type', 'journal_schema_bool',
                                     'steps_mapping', 'prior_receipt_value'])
def test_resume_rejects_scheduler_before_and_completion_tampering(tmp_path, terminal, tamper):
    runtime, case, review = reviewed(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    if terminal:
        result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
        assert result['ok'], result
        transaction_id = result['transaction_id']
    else:
        runtime.checkpoint = lambda at: (_ for _ in ()).throw(Crash()) if at == 'receipt_intent' else None
        with pytest.raises(Crash):
            reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
        transaction_id = next(record['transaction_id'] for record in runtime.journal.list() if 'reconcile' in record)
    record = runtime.journal.load(transaction_id)
    before_refs = record['scheduler_before']
    if tamper == 'missing':
        before_refs.clear()
    elif tamper == 'extra':
        name = runtime.authority.receipt()['ownership'][case['unselected_id']]['name']
        before_refs[name] = before_refs[case['name']]
    elif tamper == 'wrong_ref' or tamper.startswith('running_'):
        snapshot = runtime.vault.get(before_refs[case['name']])
        if tamper == 'running_missing':
            snapshot['value'].pop('running')
        elif tamper == 'running_true':
            snapshot['value']['running'] = True
        elif tamper == 'running_nonbool':
            snapshot['value']['running'] = 0
        reference = runtime.vault.put(transaction_id + ':synthetic-tampered-task', snapshot)
        before_refs[case['name']] = reference
        if tamper != 'wrong_ref':
            record['reconcile']['evidence']['tasks'][case['active_id']] = reference
    elif tamper == 'selection':
        changed_plan = record['reconcile']['plan']
        changed_plan['review']['task_ids'] = [case['unselected_id']]
        changed_plan['approval_revision'] = reg.revision({key: value for key, value in changed_plan.items()
                                                        if key != 'approval_revision'})
    elif tamper == 'unselected_proof':
        record['completion']['receipt']['ownership'][case['unselected_id']]['identity'] = case['wrong_identity']
    elif tamper == 'epoch':
        record['completion']['receipt']['authority']['authority_epoch'] += 1
    elif tamper == 'selected_enabled_type':
        proof = record['completion']['receipt']['ownership'][case['active_id']]
        assert proof['enabled'] is False
        proof['enabled'] = 0
    elif tamper == 'input_type':
        snapshot = runtime.vault.get(record['input_after'])
        binding = snapshot['value']['request']['bindings']['tasks'][case['active_id']]
        assert binding['enabled'] is False
        binding['enabled'] = 0
        reference = runtime.vault.put(transaction_id + ':synthetic-input-tamper', snapshot)
        record['input_after'] = reference
        record['completion']['receipt']['input_reference'] = reference
        if terminal:
            completed = record['completion']['receipt']
            fs.atomic_replace(runtime.authority.config.state_root / 'receipts' / (transaction_id + '.json'),
                              encode(completed))
            fs.atomic_replace(runtime.authority.pointer, encode({'generation': transaction_id,
                                                                 'receipt_digest': digest(encode(completed))}))
    elif tamper == 'journal_schema_bool':
        record['schemaVersion'] = True
    elif tamper == 'steps_mapping':
        record['steps'] = {}
    elif tamper == 'prior_receipt_value':
        from task_console.runtime_storage import decode
        snapshot = runtime.vault.get(record['reconcile']['evidence']['receipt'])
        assert isinstance(snapshot['value'], bytes)
        snapshot['value'] = decode(snapshot['value'])
        record['reconcile']['evidence']['receipt'] = runtime.vault.put(
            transaction_id + ':synthetic-prior-receipt-tamper', snapshot)
    if tamper == 'steps_mapping':
        fs.atomic_replace(runtime.journal.root / (transaction_id + '.json'), encode(record))
    else:
        runtime.journal.save(transaction_id, record)
    before = existing_artifacts(tmp_path)
    pointer = runtime.authority.pointer.read_bytes(), fs.identity(runtime.authority.pointer)
    receipts = tree(runtime.authority.config.state_root / 'receipts')
    fresh = configured(tmp_path)
    try:
        result = reconcile.resume(transaction_id, record['reconcile']['plan']['approval_revision'], runtime=fresh)
    except reg.ContractError:
        pass
    else:
        assert not result['ok'] and result['failure'], result
    assert existing_artifacts(tmp_path) == before
    assert (fresh.authority.pointer.read_bytes(), fs.identity(fresh.authority.pointer)) == pointer
    assert tree(fresh.authority.config.state_root / 'receipts') == receipts
    assert_query_only(fresh)


@pytest.mark.parametrize('terminal', [False, True])
def test_resume_rejects_file_bytes_replaced_by_digest_mapping(tmp_path, terminal):
    runtime, _, review = reviewed(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    if terminal:
        result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
        assert result['ok'], result
    else:
        runtime.checkpoint = lambda at: (_ for _ in ()).throw(Crash()) if at == 'receipt_intent' else None
        with pytest.raises(Crash):
            reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    record = next(record for record in runtime.journal.list() if 'reconcile' in record)
    path = review['files'][0]['path']
    original = runtime.vault.get(record['file_before'][path])
    assert isinstance(original['value'], bytes)
    changed = deepcopy(original)
    changed['value'] = {'bytes_sha256': digest(original['value'])}
    assert changed != original and reg.fingerprint(changed) == reg.fingerprint(original)
    record['file_before'][path] = runtime.vault.put(record['transaction_id'] + ':synthetic-file-tamper', changed)
    runtime.journal.save(record['transaction_id'], record)
    before = existing_artifacts(tmp_path)
    pointer = runtime.authority.pointer.read_bytes(), fs.identity(runtime.authority.pointer)
    receipts = tree(runtime.authority.config.state_root / 'receipts')
    fresh = configured(tmp_path)
    try:
        result = reconcile.resume(record['transaction_id'], plan['approval_revision'], runtime=fresh)
    except reg.ContractError:
        pass
    else:
        assert not result['ok'] and result['failure'], result
    assert existing_artifacts(tmp_path) == before
    assert (fresh.authority.pointer.read_bytes(), fs.identity(fresh.authority.pointer)) == pointer
    assert tree(fresh.authority.config.state_root / 'receipts') == receipts
    assert_query_only(fresh)


def test_lock_boundary_plan_type_change_is_rejected_before_publication(tmp_path, monkeypatch):
    runtime, _, review = reviewed(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    runtime.checkpoint = lambda at: (_ for _ in ()).throw(Crash()) if at == 'receipt_intent' else None
    with pytest.raises(Crash):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    transaction_id = next(record['transaction_id'] for record in runtime.journal.list() if 'reconcile' in record)
    fresh = configured(tmp_path)
    original_hold = fresh.locks.hold
    injected = False
    @contextmanager
    def hold(keys):
        nonlocal injected
        with original_hold(keys):
            record = fresh.journal.load(transaction_id)
            record['reconcile']['plan']['schemaVersion'] = True
            fresh.journal.save(transaction_id, record)
            injected = True
            yield
    monkeypatch.setattr(fresh.locks, 'hold', hold)
    before = existing_artifacts(tmp_path)
    pointer = fresh.authority.pointer.read_bytes(), fs.identity(fresh.authority.pointer)
    receipts = tree(fresh.authority.config.state_root / 'receipts')
    with pytest.raises(reg.ContractError):
        reconcile.resume(transaction_id, plan['approval_revision'], runtime=fresh)
    assert injected
    assert existing_artifacts(tmp_path) == before
    assert (fresh.authority.pointer.read_bytes(), fs.identity(fresh.authority.pointer)) == pointer
    assert tree(fresh.authority.config.state_root / 'receipts') == receipts
    assert_query_only(fresh)


def test_terminal_resume_rejects_old_receipt_type_change_after_later_registration(tmp_path):
    runtime, case, review = reviewed(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert result['ok'], result
    later = reg.build_plan(runtime, [case['active_id']])
    applied = reg.apply(later, later['input_revision'], runtime=runtime)
    assert applied['status'] == 'committed' and applied['cleaned'], applied
    path = runtime.authority.config.state_root / 'receipts' / (result['transaction_id'] + '.json')
    receipt = read_json(path)
    assert receipt['ownership'][case['active_id']]['enabled'] is False
    receipt['ownership'][case['active_id']]['enabled'] = 0
    fs.atomic_replace(path, encode(receipt))
    fresh = configured(tmp_path)
    assert fresh.authority.receipt()['generation'] == applied['transaction_id']
    before = tree(tmp_path)
    with pytest.raises(reg.ContractError):
        reconcile.resume(result['transaction_id'], plan['approval_revision'], runtime=fresh)
    assert tree(tmp_path) == before
    assert_query_only(fresh)


@pytest.mark.parametrize('later', ['running', 'registration'])
def test_terminal_resume_uses_immutable_evidence_after_legitimate_later_activity(tmp_path, later):
    runtime, case, review = reviewed(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert result['ok'], result
    if later == 'running':
        change_scheduler(tmp_path, case, 'running')
    else:
        future = reg.build_plan(runtime, [case['active_id']])
        applied = reg.apply(future, future['input_revision'], runtime=runtime)
        assert applied['status'] == 'committed' and applied['cleaned'], applied
    fresh = configured(tmp_path)
    before = tree(tmp_path)
    assert reconcile.resume(result['transaction_id'], plan['approval_revision'], runtime=fresh) == result
    assert tree(tmp_path) == before
    assert_query_only(fresh)


@pytest.mark.parametrize('stage', ['preparing', 'committing', 'committed'])
def test_pre_feature_file_only_journal_resumes_without_new_fields_or_task_locks(tmp_path, stage):
    runtime, case, review = reviewed(tmp_path)
    review.pop('scheduler_reviews')
    tasks = read_json(tmp_path / 'synthetic-scheduler.json')
    tasks[case['name']] = deepcopy(case['old_snapshot'])
    tasks[case['name']]['value']['running'] = True
    fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode(tasks))
    plan, record = legacy_file_reconciliation_pending(runtime, review, stage=stage)
    assert set(plan['baseline']) == {'pointer', 'receipt_revision', 'configuration_revision',
                                     'configuration_fingerprint', 'desired_fingerprint', 'inputs_revision',
                                     'files', 'scheduler', 'unselected_scheduler_drift'}
    assert all(not key.startswith('task:') for key in record['locks'])
    if stage != 'preparing':
        assert record['completion']['status'] == 'committed'
        assert record['reconcile']['evidence']['tasks']
        assert all('running' not in runtime.vault.get(reference)['value']
                   for reference in record['reconcile']['evidence']['tasks'].values())
    runtime.journal.create(record['transaction_id'], record)
    before = existing_artifacts(tmp_path)
    result = reconcile.resume(record['transaction_id'], plan['approval_revision'], runtime=runtime)
    assert result['ok'] and result['cleaned'], result
    assert existing_artifacts(tmp_path) == before
    completed = runtime.journal.load(record['transaction_id'])
    assert 'scheduler_before' not in completed
    assert completed['reconcile']['plan'] == plan
    assert all(not key.startswith('task:') for key in completed['locks'])
    assert_query_only(runtime)
