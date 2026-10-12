"""Explicit acceptance of reviewed file drift into an existing authority.

Only new protected evidence, a journal, an immutable receipt and its pointer are
written. This operation never republishes a file or a Scheduler definition.
"""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import uuid

from fleet_guards import filesystem as fs
from . import registration as reg
from .runtime_storage import FileResources, decode, encode, read_json


KIND = 'authority-reconciliation'


def _fail(code, field='reconciliation'):
    raise reg.Conflict(code, field)


def _authority(runtime):
    authority = getattr(runtime, 'authority', None)
    if authority is None or authority.config.source_path is None:
        _fail('configured_authority_required')
    return authority


def _review(value, paths, task_ids):
    required = {'schemaVersion', 'reason', 'files', 'task_ids'}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value) - required - {'historical_task_ids', 'historical_receipt_chain'}
            or value['schemaVersion'] != 1 or not isinstance(value['reason'], str)
            or not value['reason'].strip() or not isinstance(value['files'], list) or not value['files']
            or not isinstance(value['task_ids'], list) or not value['task_ids']
            or any(not isinstance(item, str) or item not in task_ids for item in value['task_ids'])
            or len(set(value['task_ids'])) != len(value['task_ids'])):
        _fail('explicit_file_review_required')
    historical = value.get('historical_task_ids', [])
    if (not isinstance(historical, list)
            or any(not isinstance(task_id, str) or task_id not in task_ids for task_id in historical)
            or len(historical) != len(set(historical)) or set(historical) & set(value['task_ids'])):
        _fail('historical_task_review_required')
    chain = value.get('historical_receipt_chain', [])
    if (not isinstance(chain, list) or bool(chain) != bool(historical)
            or any(not isinstance(item, dict) or set(item) != {
                'generation', 'receipt_digest', 'journal_fingerprint'}
                or any(not isinstance(field, str) or not field for field in item.values()) for item in chain)
            or len({item['generation'] for item in chain}) != len(chain)):
        _fail('historical_task_review_required')
    selected = {}
    fields = {'path', 'expected_receipt_fingerprint', 'reviewed_fingerprint', 'reason'}
    for item in value['files']:
        if (not isinstance(item, dict) or set(item) != fields
                or any(not isinstance(item[k], str) or not item[k].strip() for k in fields)):
            _fail('explicit_file_review_required')
        path = str(fs.validate_path(item['path']))
        if path != item['path'] or path not in paths or path in selected:
            _fail('unowned_or_duplicate_review_path')
        selected[path] = item
    return selected


def _configuration(config):
    value = asdict(config)
    return {key: str(item) if isinstance(item, Path) else item for key, item in value.items()}


def _collect(runtime, review, *, receipt=None):
    authority = _authority(runtime)
    config = authority.config
    from .runtime import RuntimeConfig
    if RuntimeConfig.read(config.source_path, private_root=config.private_root,
                          state_root=config.state_root, vault_root=config.vault_root) != config:
        _fail('runtime_configuration_changed')
    receipt = authority.receipt() if receipt is None else receipt
    if authority._pointer() is None or 'input_reference' not in receipt:
        _fail('existing_authority_required')
    paths = set(runtime.files.paths)
    if set(receipt['file_ownership']) != paths:
        _fail('managed_scope_changed')
    selected = _review(review, paths, receipt['ownership'])
    inputs = authority._inputs(receipt['input_reference'])
    if inputs['request']['machine'].get('authority_epoch') != receipt['authority']['authority_epoch']:
        _fail('input_authority_mismatch')
    if inputs['request']['machine'].get('migrated_tasks') != receipt['authority']['migrated_tasks']:
        _fail('input_authority_mismatch')
    specs = {spec['task_id']: spec for spec in reg._compile({'request': inputs['request']})['task_specs']}
    historical = set(review.get('historical_task_ids', []))
    if (set(specs) - set(receipt['ownership'])
            or set(receipt['ownership']) - set(specs) != historical):
        _fail('task_ownership_scope_changed')
    history = None
    if historical:
        from .authority_history import validate
        history = validate(runtime, receipt, historical, review['historical_receipt_chain'])
    for task_id, proof in receipt['ownership'].items():
        if task_id in historical:
            continue  # Its bootstrap proof matches the explicitly reviewed receipt chain.
        if (proof['name'] != specs[task_id]['name']
                or proof.get('epoch') != receipt['authority']['authority_epoch']
                or proof.get('writer') != ('declarations' if task_id in receipt['authority']['migrated_tasks'] else 'legacy')):
            _fail('task_ownership_invalid')
    reg._compile(runtime.load())
    config_files = FileResources([config.source_path, config.desired], config.state_root / 'stages')
    source = reg._read(config_files, str(config.source_path))
    desired = reg._read(config_files, str(config.desired))
    if source['state'] != 'present' or desired['state'] != 'present':
        _fail('runtime_configuration_missing')
    # Reparse/path checks apply to every resource, including unchanged ones.
    files = {path: reg._read(runtime.files, path) for path in sorted(paths)}
    for path, snapshot in files.items():
        actual, expected = reg.fingerprint(snapshot), receipt['file_ownership'][path]
        if path in selected:
            item = selected[path]
            if (snapshot['state'] != 'present' or expected == actual
                    or item['expected_receipt_fingerprint'] != expected
                    or item['reviewed_fingerprint'] != actual):
                _fail('reviewed_file_mismatch')
        elif expected != actual:
            _fail('unreviewed_file_drift')
    tasks = {}
    for task_id, proof in sorted(receipt['ownership'].items()):
        snapshot = reg._read(runtime.scheduler, proof['name'])
        stable = reg._scheduler_config(snapshot)
        if task_id in historical and snapshot['state'] != 'absent':
            _fail('historical_task_present')
        if task_id in review['task_ids'] and snapshot.get('identity') != proof['identity']:
            _fail('scheduler_ownership_changed')
        tasks[task_id] = {'name': proof['name'], 'snapshot': stable}
    baseline = {
        'pointer': authority._pointer(), 'receipt_revision': reg.revision(receipt),
        'configuration_revision': reg.revision(_configuration(config)),
        'configuration_fingerprint': reg.fingerprint(source),
        'desired_fingerprint': reg.fingerprint(desired), 'inputs_revision': reg.revision(inputs),
        'files': {path: reg.fingerprint(snapshot) for path, snapshot in files.items()},
        'scheduler': {key: {'name': item['name'], 'fingerprint': reg.fingerprint(item['snapshot']),
                            'receipt_identity': receipt['ownership'][key]['identity'],
                            'current_identity': item['snapshot'].get('identity')}
                      for key, item in tasks.items()},
        'unselected_scheduler_drift': [key for key, item in tasks.items()
                                       if item['snapshot'].get('identity') != receipt['ownership'][key]['identity']],
    }
    if history is not None:
        baseline['historical_tasks'] = history
    # Observe the authority again after potentially slow Scheduler reads.
    if reg.revision(authority.receipt()) != reg.revision(receipt):
        # A resumed completion may have already published its new pointer. Its
        # caller verifies that exact target instead of accepting any generation.
        baseline['observed_receipt_revision'] = reg.revision(authority.receipt())
    return baseline, files, tasks, inputs, receipt


def build_plan(runtime, review):
    """Pure read-only plan; approval is the digest of all reviewed observations."""
    runtime = reg._use(runtime)
    if runtime.journal.pending(['authority']):
        _fail('recovery_required', 'journal')
    baseline, *_ = _collect(runtime, review)
    if 'observed_receipt_revision' in baseline:
        _fail('authority_pointer_changed')
    result = {'schemaVersion': 1, 'kind': KIND, 'review': deepcopy(review), 'baseline': baseline}
    result['approval_revision'] = reg.revision(result)
    return result


def _approved(plan, approval_revision):
    if (not isinstance(plan, dict)
            or set(plan) != {'schemaVersion', 'kind', 'review', 'baseline', 'approval_revision'}
            or plan['schemaVersion'] != 1 or plan['kind'] != KIND):
        _fail('invalid_reconciliation_plan')
    unsigned = {key: value for key, value in plan.items() if key != 'approval_revision'}
    if approval_revision != plan['approval_revision'] or approval_revision != reg.revision(unsigned):
        _fail('review_approval_mismatch')


def _locks(plan):
    # All supported task writers also hold authority. File locks additionally
    # match registration's resource locks without invoking task-idle semantics.
    return ['authority'] + ['file:' + path for path in sorted(plan['baseline']['files'])]


def _verify(runtime, plan, *, receipt=None, journal=None):
    baseline, files, tasks, inputs, prior = _collect(runtime, plan['review'], receipt=receipt)
    expected = plan['baseline']
    pointer = baseline.pop('pointer')
    observed_revision = baseline.pop('observed_receipt_revision', reg.revision(prior))
    allowed = [expected['pointer']]
    if journal is not None and 'completion' in journal:
        from .runtime_storage import digest
        reference = journal.get('input_after')
        if reference is None or runtime.authority._inputs(reference) != inputs:
            _fail('reconciliation_evidence_changed')
        completed = deepcopy(prior)
        completed.update(generation=journal['transaction_id'], input_reference=reference,
                         file_ownership=deepcopy(expected['files']))
        if journal['completion'] != {'status': 'committed', 'receipt': completed,
                                     'previous': expected['pointer']}:
            _fail('reconciliation_completion_changed')
        target = {'generation': journal['transaction_id'], 'receipt_digest': digest(encode(completed))}
        allowed.append(target)
        if pointer == target:
            if observed_revision != reg.revision(completed):
                _fail('authority_receipt_changed')
        elif observed_revision != expected['receipt_revision']:
            _fail('authority_receipt_changed')
    elif observed_revision != expected['receipt_revision']:
        _fail('authority_receipt_changed')
    if pointer not in allowed or baseline != {k: v for k, v in expected.items() if k != 'pointer'}:
        _fail('reconciliation_plan_stale')
    return files, tasks, inputs, prior


def _result(journal):
    return {'schemaVersion': 1, 'kind': KIND, 'transaction_id': journal['transaction_id'],
            'status': journal['status'], 'cleaned': journal.get('cleaned', False),
            'ok': journal['status'] == 'committed' and journal.get('cleaned', False),
            'action_required': None if journal.get('cleaned') else 'reconcile-resume',
            'failure': journal.get('failure')}


def _store(runtime, journal, container, key, snapshot):
    if key in container:
        if runtime.vault.get(container[key]) != snapshot:
            _fail('reconciliation_evidence_changed')
        return
    container[key] = reg._secure(runtime, journal['transaction_id'] + ':reconcile', snapshot)
    runtime.journal.save(journal['transaction_id'], journal)
    runtime.checkpoint('reconcile_evidence_saved')


def _continue(runtime, journal):
    _validate_journal(journal)
    plan = journal['reconcile']['plan']
    evidence = journal['reconcile']['evidence']
    prior = None
    if 'receipt' in evidence:
        stored = runtime.vault.get(evidence['receipt'])
        if stored.get('state') != 'present' or stored.get('identity') != 'authority-receipt-v1':
            _fail('reconciliation_evidence_changed')
        prior = decode(stored['value'])
    files, tasks, inputs, prior = _verify(runtime, plan, receipt=prior, journal=journal)
    _store(runtime, journal, evidence, 'receipt',
           {'state': 'present', 'identity': 'authority-receipt-v1', 'value': encode(prior)})
    snapshot = {'state': 'present', 'identity': 'effective-input-v1', 'value': inputs}
    _store(runtime, journal, journal, 'input_before', snapshot)
    _store(runtime, journal, journal, 'input_after', snapshot)
    for path, snapshot in files.items():
        _store(runtime, journal, journal['file_before'], path, snapshot)
    for task_id, item in tasks.items():
        _store(runtime, journal, evidence['tasks'], task_id, item['snapshot'])
    _verify(runtime, plan, receipt=prior, journal=journal)
    original_checkpoint = runtime.checkpoint
    def checked_checkpoint(point):
        original_checkpoint(point)
        if point in ('receipt_intent', 'receipt_written', 'pointer_published'):
            _verify(runtime, plan, receipt=prior, journal=journal)
    runtime.checkpoint = checked_checkpoint
    try:
        runtime.finish(journal, 'committed')
    finally:
        runtime.checkpoint = original_checkpoint
    _verify(runtime, plan, receipt=prior, journal=journal)
    journal['cleaned'] = True  # No staging objects or Scheduler cleanup exist.
    journal.pop('failure', None)
    runtime.journal.save(journal['transaction_id'], journal)
    return _result(journal)


def _validate_journal(journal):
    required = {'schemaVersion', 'transaction_id', 'status', 'locks', 'steps', 'cleaned', 'file_before', 'reconcile'}
    optional = {'input_before', 'input_after', 'failure', 'completion'}
    if (not required <= set(journal) or set(journal) - required - optional
            or journal['status'] not in ('preparing', 'committing', 'committed')
            or ('completion' in journal) != (journal['status'] in ('committing', 'committed'))
            or (journal['cleaned'] and journal['status'] != 'committed')
            or type(journal['cleaned']) is not bool or journal['steps']
            or set(journal['reconcile']) != {'plan', 'evidence'}):
        _fail('reconciliation_journal_invalid')
    plan = journal['reconcile']['plan']
    evidence = journal['reconcile']['evidence']
    if (not isinstance(evidence, dict) or not {'tasks'} <= set(evidence)
            or set(evidence) - {'tasks', 'receipt'}
            or not isinstance(evidence['tasks'], dict)
            or not set(evidence['tasks']) <= set(plan['baseline']['scheduler'])
            or not isinstance(journal['file_before'], dict)
            or not set(journal['file_before']) <= set(plan['baseline']['files'])
            or journal['locks'] != _locks(plan)):
        _fail('reconciliation_journal_invalid')


def _verify_terminal(runtime, journal):
    """Check immutable completion evidence without requiring old files to persist."""
    plan = journal['reconcile']['plan']
    baseline = plan['baseline']
    evidence = journal['reconcile']['evidence']
    if (set(evidence) != {'receipt', 'tasks'}
            or set(evidence['tasks']) != set(baseline['scheduler'])
            or set(journal['file_before']) != set(baseline['files'])):
        _fail('reconciliation_evidence_changed')
    stored = runtime.vault.get(evidence['receipt'])
    if stored.get('state') != 'present' or stored.get('identity') != 'authority-receipt-v1':
        _fail('reconciliation_evidence_changed')
    prior = decode(stored['value'])
    _review(plan['review'], baseline['files'], prior['ownership'])
    historical = plan['review'].get('historical_task_ids', [])
    if historical:
        from .authority_history import validate
        if validate(runtime, prior, historical, plan['review']['historical_receipt_chain']) != baseline.get('historical_tasks'):
            _fail('reconciliation_evidence_changed')
    inputs = runtime.authority._inputs(prior['input_reference'])
    if (reg.revision(prior) != baseline['receipt_revision']
            or reg.revision(inputs) != baseline['inputs_revision']
            or any(runtime.authority._inputs(journal[key]) != inputs for key in ('input_before', 'input_after'))):
        _fail('reconciliation_evidence_changed')
    for path, reference in journal['file_before'].items():
        if reg.fingerprint(runtime.vault.get(reference)) != baseline['files'][path]:
            _fail('reconciliation_evidence_changed')
    for task_id, reference in evidence['tasks'].items():
        snapshot = runtime.vault.get(reference)
        expected = baseline['scheduler'][task_id]
        if (reg.fingerprint(snapshot) != expected['fingerprint']
                or expected['receipt_identity'] != prior['ownership'][task_id]['identity']
                or expected['current_identity'] != snapshot.get('identity')
                or expected['name'] != prior['ownership'][task_id]['name']):
            _fail('reconciliation_evidence_changed')
    drift = sorted(key for key, item in baseline['scheduler'].items()
                   if item['receipt_identity'] != item['current_identity'])
    if drift != baseline['unselected_scheduler_drift'] or set(drift) & set(plan['review']['task_ids']):
        _fail('reconciliation_evidence_changed')
    completed = deepcopy(prior)
    completed.update(generation=journal['transaction_id'], input_reference=journal['input_after'],
                     file_ownership=deepcopy(baseline['files']))
    if journal['completion'] != {'status': 'committed', 'receipt': completed, 'previous': baseline['pointer']}:
        _fail('reconciliation_completion_changed')
    receipt_path = runtime.authority.config.state_root / 'receipts' / (journal['transaction_id'] + '.json')
    if read_json(receipt_path) != completed or runtime.authority._pointer() == baseline['pointer']:
        _fail('reconciliation_completion_unpublished')
    runtime.authority.receipt()  # Validate the current generation, possibly a later transaction.


def _attempt(runtime, journal):
    try:
        return _continue(runtime, journal)
    except Exception as exc:
        journal['failure'] = exc.to_dict() if isinstance(exc, reg.Conflict) else {'code': 'reconciliation_failed'}
        # Never compensate a reconciliation: its before-images contain precisely
        # the reviewed external edits, not an earlier state we may restore.
        runtime.journal.save(journal['transaction_id'], journal)
        return _result(journal)


def apply(plan, approval_revision, *, runtime=None):
    runtime = reg._use(runtime)
    _approved(plan, approval_revision)
    locks = _locks(plan)
    with runtime.locks.hold(locks):
        if runtime.journal.pending(['authority']):
            _fail('recovery_required', 'journal')
        _verify(runtime, plan)
        transaction_id = uuid.uuid4().hex
        journal = {'schemaVersion': 1, 'transaction_id': transaction_id, 'status': 'preparing',
                   'locks': locks, 'steps': [], 'cleaned': False, 'file_before': {},
                   'reconcile': {'plan': deepcopy(plan), 'evidence': {'tasks': {}}}}
        runtime.journal.create(transaction_id, journal)
        runtime.checkpoint('reconcile_journal_created')
        return _attempt(runtime, journal)


def resume(transaction_id, approval_revision, *, runtime=None):
    runtime = reg._use(runtime)
    journal = runtime.journal.load(transaction_id)
    if not isinstance(journal.get('reconcile'), dict):
        _fail('reconciliation_journal_required')
    plan = journal['reconcile']['plan']
    _approved(plan, approval_revision)
    if journal['locks'] != _locks(plan) or journal['steps']:
        _fail('reconciliation_journal_invalid')
    with runtime.locks.hold(journal['locks']):
        journal = runtime.journal.load(transaction_id)
        _validate_journal(journal)
        if journal['reconcile']['plan'] != plan or journal['steps'] or journal['locks'] != _locks(plan):
            _fail('reconciliation_journal_changed')
        if set(runtime.journal.pending(['authority'])) - {transaction_id}:
            _fail('recovery_required', 'journal')
        if journal['status'] == 'committed' and journal.get('cleaned'):
            _verify_terminal(runtime, journal)
            return _result(journal)
        return _attempt(runtime, journal)
