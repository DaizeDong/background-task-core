"""Historical ownership is accepted only with complete synthetic bootstrap proof."""
from copy import deepcopy
from pathlib import Path

import pytest

from t12_runtime_support import configured, encode, fs, read_json, observation
from task_console import adoption, reconcile, registration as reg, runtime_windows
from task_console.runtime_storage import digest
from task_console.runtime_xml import render
from tools.make_fixtures import add_creation_candidate, reconciliation_history_case, reconciliation_receipt_chain


def forbidden(*args, **kwargs):
    raise AssertionError('Synthetic history tests must not use live transports')


@pytest.fixture(autouse=True)
def no_live_transport(monkeypatch):
    for name in ('COMTransport', 'DPAPIProtector', 'powershell'):
        monkeypatch.setattr(runtime_windows, name, forbidden)


def history(root, *, extra_control=False):
    case = reconciliation_history_case(root)
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
    tasks = {spec['name']: observation(spec['name'], render(spec, {'state': 'absent'}, False))
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
    case['bootstrap_id'] = adopted['transaction_id']
    case['generations'] = [case['bootstrap_id']]
    fs.atomic_replace(runtime.authority.config.desired, encode(case['desired']))
    historical_name = request['bindings']['tasks'][case['historical_id']]['name']
    tasks.pop(historical_name)
    fs.atomic_replace(root / 'synthetic-scheduler.json', encode(tasks))
    plan = reg.build_plan(runtime, [case['active_id']], migrate=True)
    result = reg.apply(plan, plan['input_revision'], runtime=runtime)
    assert result['status'] == 'committed' and result['cleaned'], result
    case['migration_id'] = result['transaction_id']
    case['generations'].append(case['migration_id'])
    controlled = reg.control(request['bindings']['tasks'][case['active_id']]['name'], 'enable', runtime=runtime)
    assert controlled['status'] == 'committed' and controlled['cleaned'], controlled
    case['current_id'] = controlled['transaction_id']
    case['generations'].append(case['current_id'])
    if extra_control == 'noop':
        prior_inputs = runtime.authority._inputs(runtime.authority.receipt()['input_reference'])
        noop = reg.build_plan(runtime, [case['active_id']])
        reapplied = reg.apply(noop, noop['input_revision'], runtime=runtime)
        assert reapplied['status'] == 'committed' and reapplied['cleaned'], reapplied
        assert runtime.authority._inputs(runtime.authority.receipt()['input_reference']) == prior_inputs
        case['current_id'] = reapplied['transaction_id']
        case['generations'].append(case['current_id'])
    if extra_control:
        case['skipped_id'] = case['current_id']
        controlled = reg.control(request['bindings']['tasks'][case['active_id']]['name'], 'disable', runtime=runtime)
        assert controlled['status'] == 'committed' and controlled['cleaned'], controlled
        case['current_id'] = controlled['transaction_id']
        case['generations'].append(case['current_id'])
    case['historical_name'] = historical_name
    receipt = runtime.authority.receipt()
    assert set(receipt['ownership']) == {case['active_id'], case['historical_id']}
    inputs = runtime.authority._inputs(receipt['input_reference'])
    assert {spec['task_id'] for spec in reg._compile({'request': inputs['request']})['task_specs']} == {case['active_id']}
    path = runtime.authority.config.paths['TaskNames.ps1']
    fs.atomic_replace(path, Path(path).read_bytes() + b'\n# Synthetic reviewed annotation\n')
    review = {'schemaVersion': 1, 'reason': 'Accept reviewed synthetic annotation',
              'task_ids': [case['active_id']], 'historical_task_ids': [case['historical_id']],
              'historical_receipt_chain': reconciliation_receipt_chain(runtime, case['generations']),
              'files': [{'path': path, 'expected_receipt_fingerprint': receipt['file_ownership'][path],
                         'reviewed_fingerprint': reg.fingerprint(runtime.files.read(path)),
                         'reason': 'Synthetic annotation preserves the active declaration'}]}
    runtime.scheduler.transport.calls.clear()
    return runtime, case, review


def preserved(root):
    paths = [path for path in (root / 'private').rglob('*') if path.is_file()]
    paths.append(root / 'synthetic-scheduler.json')
    return {str(path): (path.read_bytes(), fs.identity(path)) for path in paths}


def assert_query_only(runtime):
    assert {operation for operation, _ in runtime.scheduler.transport.calls} <= {'query'}


def tree(root):
    return {str(path.relative_to(root)): (path.read_bytes(), fs.identity(path)) if path.is_file() else None
            for path in root.rglob('*')}


def receipt_path(runtime, generation):
    return runtime.authority.config.state_root / 'receipts' / (generation + '.json')


def rewrite_chain(runtime, case, mutate_receipt=None, mutate_record=None, *, reseal_bootstrap=False, review=None):
    """Keep all receipt links valid while changing a selected synthetic claim."""
    previous = None
    bootstrap_revision = None
    for generation in case['generations']:
        receipt = read_json(receipt_path(runtime, generation))
        record = runtime.journal.load(generation)
        if bootstrap_revision is not None:
            receipt['bootstrap_plan_revision'] = bootstrap_revision
        if mutate_receipt is not None:
            mutate_receipt(receipt)
        if mutate_record is not None:
            mutate_record(record, receipt)
        if 'completion' in record:
            record['completion']['receipt'] = deepcopy(receipt)
            record['completion']['previous'] = previous
        else:
            bootstrap_revision = receipt['bootstrap_plan_revision']
            if reseal_bootstrap:
                pointer = {'generation': generation, 'receipt_digest': digest(encode(receipt))}
                for index, step in enumerate(record['steps']):
                    snapshot = runtime.vault.get(step['after'])
                    snapshot['value'] = encode(pointer if index == 2 else receipt)
                    step['after'] = runtime.vault.put(generation + ':synthetic-stage-' + str(index), snapshot)
            fs.atomic_replace(runtime.authority.config.adoption, encode(receipt))
        runtime.journal.save(generation, record)
        fs.atomic_replace(receipt_path(runtime, generation), encode(receipt))
        previous = {'generation': generation, 'receipt_digest': digest(encode(receipt))}
    fs.atomic_replace(runtime.authority.pointer, encode(previous))
    assert runtime.authority.receipt()
    if review is not None:
        review['historical_receipt_chain'] = reconciliation_receipt_chain(runtime, case['generations'])


def assert_refused(runtime, root, review):
    before = tree(root)
    with pytest.raises(reg.ContractError):
        reconcile.build_plan(runtime, review)
    assert tree(root) == before
    assert_query_only(runtime)


def test_accepts_historical_bootstrap_proof_and_preserves_current_state(tmp_path):
    runtime, case, review = history(tmp_path)
    old = deepcopy(runtime.authority.receipt())
    inputs = runtime.authority._inputs(old['input_reference'])
    assert inputs['submitted']['bindings']['tasks'][case['active_id']]['enabled'] is False
    assert inputs['request']['bindings']['tasks'][case['active_id']]['enabled'] is True
    before = preserved(tmp_path)
    before_plan = tree(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    assert tree(tmp_path) == before_plan
    result = reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert result['ok'] and result['cleaned'], result
    assert preserved(tmp_path) == before
    accepted = runtime.authority.receipt()
    assert accepted['ownership'] == old['ownership']
    assert accepted['authority'] == old['authority']
    assert accepted['generation'] == result['transaction_id'] != old['generation']
    expected_files = dict(old['file_ownership'])
    expected_files[review['files'][0]['path']] = review['files'][0]['reviewed_fingerprint']
    assert accepted['file_ownership'] == expected_files
    assert runtime.authority._inputs(accepted['input_reference']) == inputs
    assert runtime.scheduler.read(case['historical_name']) == {'state': 'absent'}
    assert_query_only(runtime)
    record = runtime.journal.load(result['transaction_id'])
    assert record['steps'] == [] and 'scheduler_before' not in record
    assert all(not key.startswith('task:') for key in record['locks'])
    future = reg.build_plan(runtime, [case['active_id']])
    applied = reg.apply(future, future['input_revision'], runtime=runtime)
    assert applied['status'] == 'committed' and applied['cleaned'], applied
    assert runtime.scheduler.read(case['historical_name']) == {'state': 'absent'}
    fresh = configured(tmp_path)
    before_resume = tree(tmp_path)
    assert reconcile.resume(result['transaction_id'], plan['approval_revision'], runtime=fresh) == result
    assert tree(tmp_path) == before_resume
    assert_query_only(fresh)


@pytest.mark.parametrize('invalid', ['missing', 'empty', 'duplicate', 'selected', 'unknown', 'non_list', 'non_string'])
def test_historical_ids_must_exactly_identify_unselected_extra_proofs(tmp_path, invalid):
    runtime, case, review = history(tmp_path)
    if invalid == 'missing':
        review.pop('historical_task_ids')
    elif invalid == 'empty':
        review['historical_task_ids'] = []
    elif invalid == 'duplicate':
        review['historical_task_ids'] *= 2
    elif invalid == 'selected':
        review['task_ids'].append(case['historical_id'])
    elif invalid == 'unknown':
        review['historical_task_ids'].append(case['unknown_id'])
    elif invalid == 'non_list':
        review['historical_task_ids'] = case['historical_id']
    elif invalid == 'non_string':
        review['historical_task_ids'] = [None]
    assert_refused(runtime, tmp_path, review)


@pytest.mark.parametrize('invalid', ['missing', 'empty', 'non_list', 'omitted_row', 'reordered', 'duplicate',
                                      'extra', 'receipt_digest', 'journal_fingerprint', 'generation'])
def test_historical_receipt_chain_requires_the_exact_reviewed_sequence(tmp_path, invalid):
    runtime, case, review = history(tmp_path)
    chain = review['historical_receipt_chain']
    if invalid == 'missing':
        review.pop('historical_receipt_chain')
    elif invalid == 'empty':
        review['historical_receipt_chain'] = []
    elif invalid == 'non_list':
        review['historical_receipt_chain'] = case['current_id']
    elif invalid == 'omitted_row':
        chain.pop(1)
    elif invalid == 'reordered':
        chain.reverse()
    elif invalid == 'duplicate':
        chain.insert(1, deepcopy(chain[0]))
    elif invalid == 'extra':
        extra = deepcopy(chain[-1])
        extra['generation'] = case['missing_generation']
        chain.append(extra)
    elif invalid == 'receipt_digest':
        chain[1]['receipt_digest'] = '0' * 64
    elif invalid == 'journal_fingerprint':
        chain[1]['journal_fingerprint'] = 'sha256:' + '0' * 64
    elif invalid == 'generation':
        chain[1]['generation'] = case['missing_generation']
    assert_refused(runtime, tmp_path, review)


def restore_historical_scheduler_task(root, case):
    specs = reg.compile_plan(case['request'])['task_specs']
    historical = next(spec for spec in specs if spec['task_id'] == case['historical_id'])
    tasks = read_json(root / 'synthetic-scheduler.json')
    tasks[case['historical_name']] = observation(case['historical_name'], render(historical, {'state': 'absent'}, False))
    fs.atomic_replace(root / 'synthetic-scheduler.json', encode(tasks))


def test_historical_id_still_in_scheduler_is_refused(tmp_path):
    runtime, case, review = history(tmp_path)
    restore_historical_scheduler_task(tmp_path, case)
    assert_refused(runtime, tmp_path, review)


def test_historical_task_reappearance_after_approval_is_refused_without_writes(tmp_path):
    runtime, case, review = history(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    restore_historical_scheduler_task(tmp_path, case)
    before = tree(tmp_path)
    with pytest.raises(reg.ContractError):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    assert tree(tmp_path) == before
    assert_query_only(runtime)


@pytest.mark.parametrize('generation_key', ['current_id', 'migration_id', 'bootstrap_id'])
@pytest.mark.parametrize('missing', ['journal', 'receipt'])
def test_missing_generation_evidence_is_refused(tmp_path, generation_key, missing):
    runtime, case, review = history(tmp_path)
    generation = case[generation_key]
    path = (runtime.journal.root / (generation + '.json') if missing == 'journal'
            else receipt_path(runtime, generation))
    path.unlink()
    assert_refused(runtime, tmp_path, review)


@pytest.mark.parametrize('invalid', ['missing', 'corrupt'])
def test_middle_generation_protected_input_must_remain_available(tmp_path, invalid):
    runtime, case, review = history(tmp_path)
    middle = read_json(receipt_path(runtime, case['migration_id']))
    reference = middle['input_reference']
    assert reference != runtime.authority.receipt()['input_reference']
    _, domain, obj, _, _ = reference.split(':')
    path = runtime.vault.root / ('task-console-' + domain + '-' + obj + '.cred')
    if invalid == 'missing':
        path.unlink()
    else:
        fs.atomic_replace(path, path.read_bytes() + b'!')
    assert runtime.authority._inputs(runtime.authority.receipt()['input_reference'])
    assert_refused(runtime, tmp_path, review)


@pytest.mark.parametrize('changed', ['receipt', 'protected_input'])
def test_middle_generation_epoch_must_match_its_protected_inputs(tmp_path, changed):
    runtime, case, review = history(tmp_path)
    def mutate(receipt):
        if receipt['generation'] == case['migration_id']:
            if changed == 'receipt':
                receipt['authority']['authority_epoch'] += 1
                for proof in receipt['ownership'].values():
                    proof['epoch'] = receipt['authority']['authority_epoch']
            else:
                snapshot = runtime.vault.get(receipt['input_reference'])
                snapshot['value']['request']['machine']['authority_epoch'] += 1
                receipt['input_reference'] = runtime.vault.put(case['migration_id'] + ':changed-input-epoch', snapshot)
    def mutate_record(record, receipt):
        if record['transaction_id'] == case['migration_id']:
            record['input_after'] = receipt['input_reference']
    rewrite_chain(runtime, case, mutate, mutate_record, review=review)
    middle = read_json(receipt_path(runtime, case['migration_id']))
    inputs = runtime.authority._inputs(middle['input_reference'])
    assert middle['authority']['authority_epoch'] != inputs['request']['machine']['authority_epoch']
    assert_refused(runtime, tmp_path, review)


@pytest.mark.parametrize('target', ['bootstrap', 'same_epoch', 'no_op'])
def test_valid_ancestor_link_cannot_skip_a_real_middle_generation(tmp_path, target):
    extra_control = 'noop' if target == 'no_op' else target == 'same_epoch'
    runtime, case, review = history(tmp_path, extra_control=extra_control)
    assert reconcile.build_plan(runtime, review)['approval_revision']
    protected_before = tree(runtime.vault.root)
    ancestor_id = case['bootstrap_id'] if target == 'bootstrap' else case['generations'][-3]
    skipped_id = case['migration_id'] if target == 'bootstrap' else case['skipped_id']
    ancestor = read_json(receipt_path(runtime, ancestor_id))
    record = runtime.journal.load(case['current_id'])
    if target != 'bootstrap':
        assert ancestor['authority'] == record['completion']['receipt']['authority']
    record['completion']['previous'] = {'generation': ancestor_id,
                                        'receipt_digest': digest(encode(ancestor))}
    runtime.journal.save(case['current_id'], record)
    receipt_path(runtime, skipped_id).unlink()
    (runtime.journal.root / (skipped_id + '.json')).unlink()
    assert tree(runtime.vault.root) == protected_before
    assert runtime.authority.receipt()
    assert_refused(runtime, tmp_path, review)


@pytest.mark.parametrize('invalid', ['previous_digest', 'previous_missing', 'previous_null', 'previous_cycle',
                                      'completion_receipt', 'completion_status'])
def test_broken_intermediate_completion_is_refused(tmp_path, invalid):
    runtime, case, review = history(tmp_path)
    record = runtime.journal.load(case['migration_id'])
    completion = record['completion']
    if invalid == 'previous_digest':
        completion['previous']['receipt_digest'] = '0' * 64
    elif invalid == 'previous_missing':
        completion['previous']['generation'] = case['missing_generation']
    elif invalid == 'previous_null':
        completion['previous'] = None
    elif invalid == 'previous_cycle':
        completion['previous'] = deepcopy(runtime.authority._pointer())
    elif invalid == 'completion_receipt':
        completion['receipt']['ownership'][case['historical_id']]['identity'] = case['wrong_identity']
    elif invalid == 'completion_status':
        completion['status'] = 'rolled_back'
    runtime.journal.save(record['transaction_id'], record)
    review['historical_receipt_chain'] = reconciliation_receipt_chain(runtime, case['generations'])
    assert_refused(runtime, tmp_path, review)


def test_uncleaned_ancestor_requires_recovery_before_reconciliation(tmp_path):
    runtime, case, review = history(tmp_path)
    record = runtime.journal.load(case['migration_id'])
    record['cleaned'] = False
    runtime.journal.save(record['transaction_id'], record)
    assert_refused(runtime, tmp_path, review)


@pytest.mark.parametrize('invalid', ['identity', 'name', 'epoch', 'writer', 'alias', 'casefold_name',
                                      'state', 'enabled', 'receipt_kind'])
@pytest.mark.parametrize('generation_key', ['current_id', 'migration_id'])
def test_hash_consistent_historical_proof_changes_are_refused(tmp_path, invalid, generation_key):
    runtime, case, review = history(tmp_path)
    def mutate(receipt):
        if receipt['generation'] != case[generation_key]:
            return
        proof = receipt['ownership'][case['historical_id']]
        if invalid == 'identity':
            proof['identity'] = case['wrong_identity']
        elif invalid == 'name':
            proof['name'] = case['wrong_name']
        elif invalid == 'epoch':
            proof['epoch'] += 1
        elif invalid == 'writer':
            proof['writer'] = 'declarations'
        elif invalid == 'alias':
            receipt['ownership'][case['unknown_id']] = receipt['ownership'].pop(case['historical_id'])
        elif invalid == 'casefold_name':
            proof['name'] = receipt['ownership'][case['active_id']]['name'].swapcase()
        elif invalid == 'state':
            proof['state'] = 'absent'
        elif invalid == 'enabled':
            proof['enabled'] = not proof['enabled']
        elif invalid == 'receipt_kind':
            proof.pop('receipt_kind')
    rewrite_chain(runtime, case, mutate_receipt=mutate, review=review)
    if invalid == 'alias' and generation_key == 'current_id':
        review['historical_task_ids'] = [case['unknown_id']]
    assert_refused(runtime, tmp_path, review)


@pytest.mark.parametrize('invalid', ['id', 'name', 'path', 'identity', 'plan_digest', 'input_reference'])
def test_bootstrap_plan_must_match_original_historical_proof(tmp_path, invalid):
    runtime, case, review = history(tmp_path)
    def mutate(record, receipt):
        if record['transaction_id'] != case['bootstrap_id']:
            return
        plan = record['bootstrap']['plan']
        row = next(row for row in plan['tasks'] if row['task_id'] == case['historical_id'])
        if invalid == 'id':
            row['task_id'] = case['unknown_id']
        elif invalid == 'name':
            row['name'] = case['wrong_name']
        elif invalid == 'path':
            row['task_path'] += 'Acme\\'
        elif invalid == 'identity':
            row['identity'] = case['wrong_identity']
        elif invalid == 'plan_digest':
            plan['plan_revision'] = 'sha256:' + '0' * 64
        elif invalid == 'input_reference':
            reference = record['bootstrap']['input_reference']
            prefix, domain, obj, checksum, owner = reference.split(':')
            record['bootstrap']['input_reference'] = ':'.join((prefix, domain, obj, '0' * 64, owner))
        if invalid not in ('plan_digest', 'input_reference'):
            plan['plan_revision'] = reg.revision(adoption._stable(plan))
            receipt['bootstrap_plan_revision'] = plan['plan_revision']
    rewrite_chain(runtime, case, mutate_record=mutate, reseal_bootstrap=True, review=review)
    assert_refused(runtime, tmp_path, review)


def test_alias_consistent_through_receipts_and_plan_still_requires_original_input_declaration(tmp_path):
    runtime, case, review = history(tmp_path)
    def mutate_receipt(receipt):
        receipt['ownership'][case['unknown_id']] = receipt['ownership'].pop(case['historical_id'])
    def mutate_record(record, receipt):
        if 'bootstrap' in record:
            plan = record['bootstrap']['plan']
            next(row for row in plan['tasks'] if row['task_id'] == case['historical_id'])['task_id'] = case['unknown_id']
            plan['plan_revision'] = reg.revision(adoption._stable(plan))
            receipt['bootstrap_plan_revision'] = plan['plan_revision']
    rewrite_chain(runtime, case, mutate_receipt, mutate_record, reseal_bootstrap=True, review=review)
    review['historical_task_ids'] = [case['unknown_id']]
    assert_refused(runtime, tmp_path, review)


@pytest.mark.parametrize('mismatch', ['id', 'name'])
def test_valid_protected_bootstrap_inputs_must_declare_the_historical_task(tmp_path, mismatch):
    runtime, case, review = history(tmp_path)
    def mutate(record, receipt):
        if 'bootstrap' not in record:
            return
        snapshot = runtime.vault.get(receipt['input_reference'])
        for request in snapshot['value'].values():
            if mismatch == 'id':
                request['components'][0]['tasks'] = [task for task in request['components'][0]['tasks']
                                                     if task['id'] != case['historical_id'].rsplit('/', 1)[1]]
                request['bindings']['tasks'].pop(case['historical_id'])
            else:
                request['bindings']['tasks'][case['historical_id']]['name'] = case['wrong_name']
        reference = runtime.vault.put(case['bootstrap_id'] + ':synthetic-input-mismatch', snapshot)
        receipt['input_reference'] = record['bootstrap']['input_reference'] = reference
        plan = record['bootstrap']['plan']
        plan['input_revision'] = reg.compile_plan(snapshot['value']['request'])['input_revision']
        record['input_revision'] = plan['input_revision']
        plan['plan_revision'] = reg.revision(adoption._stable(plan))
        receipt['bootstrap_plan_revision'] = plan['plan_revision']
        assert runtime.authority._inputs(reference) == snapshot['value']
    rewrite_chain(runtime, case, mutate_record=mutate, reseal_bootstrap=True, review=review)
    assert_refused(runtime, tmp_path, review)


def test_new_compiled_declaration_without_ownership_is_never_historical(tmp_path):
    runtime, case, review = history(tmp_path)
    def mutate(receipt):
        if receipt['generation'] == case['current_id']:
            snapshot = runtime.vault.get(receipt['input_reference'])
            add_creation_candidate(snapshot['value']['request'])
            receipt['input_reference'] = runtime.vault.put(case['current_id'] + ':extra-declaration', snapshot)
    def mutate_record(record, receipt):
        if record['transaction_id'] == case['current_id']:
            record['input_after'] = receipt['input_reference']
    rewrite_chain(runtime, case, mutate, mutate_record, review=review)
    assert_refused(runtime, tmp_path, review)


class Crash(BaseException):
    pass


@pytest.mark.parametrize('point', ['reconcile_journal_created', 'reconcile_evidence_saved',
                                   'receipt_intent', 'receipt_written', 'pointer_published'])
def test_historical_reconciliation_resume_preserves_proofs_and_absence(tmp_path, point):
    runtime, case, review = history(tmp_path)
    old = deepcopy(runtime.authority.receipt())
    inputs = runtime.authority._inputs(old['input_reference'])
    before = preserved(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    def checkpoint(at):
        if at == point:
            raise Crash()
    runtime.checkpoint = checkpoint
    with pytest.raises(Crash):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    record = next(record for record in runtime.journal.list() if 'reconcile' in record)
    fresh = configured(tmp_path)
    result = reconcile.resume(record['transaction_id'], plan['approval_revision'], runtime=fresh)
    assert result['ok'] and result['cleaned'], result
    assert preserved(tmp_path) == before
    accepted = fresh.authority.receipt()
    assert accepted['ownership'] == old['ownership']
    assert accepted['authority'] == old['authority']
    assert fresh.authority._inputs(accepted['input_reference']) == inputs
    assert fresh.scheduler.read(case['historical_name']) == {'state': 'absent'}
    assert_query_only(fresh)
    terminal = tree(tmp_path)
    assert reconcile.resume(record['transaction_id'], plan['approval_revision'], runtime=configured(tmp_path)) == result
    assert tree(tmp_path) == terminal


@pytest.mark.parametrize('point', ['receipt_intent', 'pointer_published'])
def test_resume_cannot_accept_history_that_lost_bootstrap_evidence(tmp_path, point):
    runtime, case, review = history(tmp_path)
    plan = reconcile.build_plan(runtime, review)
    def checkpoint(at):
        if at == point:
            raise Crash()
    runtime.checkpoint = checkpoint
    with pytest.raises(Crash):
        reconcile.apply(plan, plan['approval_revision'], runtime=runtime)
    record = next(record for record in runtime.journal.list() if 'reconcile' in record)
    (runtime.journal.root / (case['bootstrap_id'] + '.json')).unlink()
    before = preserved(tmp_path)
    pointer_before = runtime.authority.pointer.read_bytes(), fs.identity(runtime.authority.pointer)
    receipts_before = tree(runtime.authority.config.state_root / 'receipts')
    fresh = configured(tmp_path)
    try:
        result = reconcile.resume(record['transaction_id'], plan['approval_revision'], runtime=fresh)
    except reg.ContractError:
        pass
    else:
        assert not result['ok'] and result['failure'], result
    assert preserved(tmp_path) == before
    assert (fresh.authority.pointer.read_bytes(), fs.identity(fresh.authority.pointer)) == pointer_before
    assert tree(fresh.authority.config.state_root / 'receipts') == receipts_before
    assert_query_only(fresh)
