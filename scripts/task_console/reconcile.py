"""Explicit acceptance of reviewed resource drift into an existing authority.

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
            or set(value) - required - {'historical_task_ids', 'historical_receipt_chain', 'scheduler_reviews'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or not isinstance(value['reason'], str)
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
    _scheduler_reviews(value, task_ids)
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


def _scheduler_reviews(review, ownership):
    if 'scheduler_reviews' not in review:
        return {}
    items = review['scheduler_reviews']
    fields = {'task_id', 'name', 'expected_receipt_identity', 'reviewed_identity',
              'reviewed_fingerprint', 'reason'}
    if (not isinstance(items, list) or len(items) != 1
            or not isinstance(items[0], dict) or set(items[0]) != fields
            or any(not isinstance(value, str) or not value.strip() for value in items[0].values())):
        _fail('explicit_scheduler_review_required')
    item = items[0]
    task_id = item['task_id']
    if (task_id not in ownership or task_id not in review['task_ids']
            or task_id in review.get('historical_task_ids', [])
            or item['name'] != ownership[task_id]['name']
            or item['expected_receipt_identity'] != ownership[task_id]['identity']):
        _fail('explicit_scheduler_review_required')
    return {task_id: item}


def _validate_scheduler_snapshot(item, proof, snapshot, spec, *, launcher=None):
    stable = reg._scheduler_config(snapshot)
    if (proof.get('receipt_kind') != 'observed-root-task-definition'
            or proof.get('task_path') != '\\' or proof.get('state') != 'present'
            or type(proof.get('enabled')) is not bool or snapshot['state'] != 'present'
            or snapshot['value']['running'] is not False
            or snapshot['value']['enabled'] != proof['enabled']
            or snapshot.get('identity') == proof['identity']
            or snapshot.get('identity') != item['reviewed_identity']
            or reg.fingerprint(stable) != item['reviewed_fingerprint']):
        _fail('reviewed_scheduler_mismatch')
    if (not isinstance(spec, dict) or spec.get('name') != item['name']
            or reg.revision(snapshot['value'].get('spec')) != reg.revision(spec)):
        _fail('reviewed_scheduler_spec_mismatch')
    # Embedded TaskSpec alone cannot attest the executable XML. Rendering into
    # the observed definition is pure and exposes changes to controlled fields.
    from .runtime_xml import NS, embedded, observation, parse, preserves_xml, render
    import xml.etree.ElementTree as ET
    current = parse(snapshot['value']['xml'])
    data = current.findall('{' + NS + '}Data')
    if (len(data) != 1 or reg.revision(embedded(current).get('spec')) != reg.revision(spec)
            or observation(item['name'], snapshot['value'])['identity'] != snapshot['identity']):
        _fail('reviewed_scheduler_spec_mismatch')
    render_before = snapshot
    if not spec.get('xml_passthrough'):
        actions = current.findall('{' + NS + '}Actions')
        principals = current.findall('{' + NS + '}Principals')
        if (len(actions) != 1 or len(principals) != 1
                or [node.tag for node in actions[0] if isinstance(node.tag, str)] != ['{' + NS + '}Exec']
                or [node.tag for node in principals[0] if isinstance(node.tag, str)] != ['{' + NS + '}Principal']):
            _fail('reviewed_scheduler_spec_mismatch')
        if spec.get('timezone') != 'UTC':
            _fail('reviewed_scheduler_trigger_requires_xml')
        # Do not let an unchanged embedded trigger hide different executable XML.
        render_before = deepcopy(snapshot)
        render_before['value']['spec']['trigger'] = None
    expected = parse(render(spec, render_before, spec['enabled'], launcher)['xml'])
    # The complete Data payload remains fingerprint-bound. Its spec was checked
    # above; JSON key order is not an executable XML difference.
    current.remove(data[0])
    expected.remove(expected.find('{' + NS + '}Data'))
    # These omitted XSD fields have fixed defaults. Fill comparison copies only;
    # the reviewed fingerprint and saved Scheduler XML retain their exact bytes.
    for root in (current, expected):
        for path, field, default in (
                ('Principals/Principal', 'RunLevel', 'LeastPrivilege'),
                ('Settings', 'WakeToRun', 'false'), ('Settings', 'Enabled', 'true')):
            for parent in root.findall('/'.join('{' + NS + '}' + part for part in path.split('/'))):
                tag = '{' + NS + '}' + field
                if parent.find(tag) is None:
                    ET.SubElement(parent, tag).text = default
    current_xml, expected_xml = (ET.tostring(root, encoding='unicode') for root in (current, expected))
    if not preserves_xml(expected_xml, current_xml) or not preserves_xml(current_xml, expected_xml):
        _fail('reviewed_scheduler_spec_mismatch')


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
    if (type(receipt.get('schemaVersion')) is not int or receipt['schemaVersion'] != 1
            or type(receipt['authority'].get('authority_epoch')) is not int):
        _fail('task_ownership_invalid')
    paths = set(runtime.files.paths)
    if set(receipt['file_ownership']) != paths:
        _fail('managed_scope_changed')
    selected = _review(review, paths, receipt['ownership'])
    inputs = authority._inputs(receipt['input_reference'])
    if any(reg.revision(inputs['request']['machine'].get(key)) != reg.revision(receipt['authority'][key])
           for key in ('authority_epoch', 'migrated_tasks')):
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
                or reg.revision(proof.get('epoch')) != reg.revision(receipt['authority']['authority_epoch'])
                or proof.get('writer') != ('declarations' if task_id in receipt['authority']['migrated_tasks'] else 'legacy')):
            _fail('task_ownership_invalid')
    current_specs = {spec['task_id']: spec for spec in reg._compile(runtime.load())['task_specs']}
    scheduler_reviews = _scheduler_reviews(review, receipt['ownership'])
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
        if task_id in scheduler_reviews:
            _validate_scheduler_snapshot(scheduler_reviews[task_id], proof, snapshot,
                                         current_specs.get(task_id), launcher=config.launcher_argument(task_id))
        elif task_id in review['task_ids'] and snapshot.get('identity') != proof['identity']:
            _fail('scheduler_ownership_changed')
        tasks[task_id] = {'name': proof['name'], 'snapshot': stable}
        if task_id in scheduler_reviews:
            tasks[task_id]['observed_snapshot'] = snapshot
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
                                       if key not in scheduler_reviews
                                       and item['snapshot'].get('identity') != receipt['ownership'][key]['identity']],
    }
    if scheduler_reviews:
        baseline['reviewed_scheduler'] = {
            task_id: {'spec': deepcopy(current_specs[task_id]), 'launcher': config.launcher_argument(task_id)}
            for task_id in scheduler_reviews}
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
            or type(plan['schemaVersion']) is not int or plan['schemaVersion'] != 1 or plan['kind'] != KIND):
        _fail('invalid_reconciliation_plan')
    unsigned = {key: value for key, value in plan.items() if key != 'approval_revision'}
    if approval_revision != plan['approval_revision'] or approval_revision != reg.revision(unsigned):
        _fail('review_approval_mismatch')


def _locks(plan):
    # All supported task writers also hold authority. File locks additionally
    # match registration's resource locks without invoking task-idle semantics.
    tasks = sorted(item['task_id'] for item in plan['review'].get('scheduler_reviews', []))
    return (['authority'] + ['task:' + task_id for task_id in tasks]
            + ['file:' + path for path in sorted(plan['baseline']['files'])])


def _expected_receipt(plan, prior, journal):
    completed = deepcopy(prior)
    completed.update(generation=journal['transaction_id'], input_reference=journal['input_after'],
                     file_ownership=deepcopy(plan['baseline']['files']))
    for task_id, item in _scheduler_reviews(plan['review'], prior['ownership']).items():
        completed['ownership'][task_id]['identity'] = item['reviewed_identity']
    return completed


def _verify_reviewed_scheduler(runtime, plan):
    """Recheck selected definitions at evidence saves without reopening history."""
    authority = _authority(runtime)
    config, baseline = authority.config, plan['baseline']
    resources = FileResources([config.source_path, config.desired], config.state_root / 'stages')
    if (reg.fingerprint(reg._read(resources, str(config.source_path))) != baseline['configuration_fingerprint']
            or reg.fingerprint(reg._read(resources, str(config.desired))) != baseline['desired_fingerprint']):
        _fail('reconciliation_plan_stale')
    receipt = authority.receipt()
    if (authority._pointer() != baseline['pointer']
            or reg.revision(receipt) != baseline['receipt_revision']):
        _fail('authority_receipt_changed')
    for task_id, item in _scheduler_reviews(plan['review'], receipt['ownership']).items():
        definition = baseline['reviewed_scheduler'][task_id]
        snapshot = reg._read(runtime.scheduler, item['name'])
        _validate_scheduler_snapshot(item, receipt['ownership'][task_id], snapshot,
                                     definition['spec'], launcher=definition['launcher'])


def _verify(runtime, plan, *, receipt=None, journal=None):
    if journal is not None:
        _validate_journal(journal)
    baseline, files, tasks, inputs, prior = _collect(runtime, plan['review'], receipt=receipt)
    expected = plan['baseline']
    pointer = baseline.pop('pointer')
    observed_revision = baseline.pop('observed_receipt_revision', reg.revision(prior))
    allowed = [expected['pointer']]
    if journal is not None and 'completion' in journal:
        from .runtime_storage import digest
        reference = journal.get('input_after')
        if (reference is None
                or reg.revision(runtime.authority._inputs(reference)) != reg.revision(inputs)):
            _fail('reconciliation_evidence_changed')
        completed = _expected_receipt(plan, prior, journal)
        if reg.revision(journal['completion']) != reg.revision({
                'status': 'committed', 'receipt': completed, 'previous': expected['pointer']}):
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
    if (pointer not in allowed
            or reg.revision(baseline) != reg.revision({k: v for k, v in expected.items() if k != 'pointer'})):
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
        stored = runtime.vault.get(container[key])
        if stored != snapshot or reg.fingerprint(stored) != reg.fingerprint(snapshot):
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
        if (set(stored) != {'state', 'identity', 'value'} or stored.get('state') != 'present'
                or stored.get('identity') != 'authority-receipt-v1' or not isinstance(stored.get('value'), bytes)):
            _fail('reconciliation_evidence_changed')
        prior = decode(stored['value'])
    files, tasks, inputs, prior = _verify(runtime, plan, receipt=prior, journal=journal)
    original_checkpoint = runtime.checkpoint
    def checked_checkpoint(point):
        original_checkpoint(point)
        if point in ('receipt_intent', 'receipt_written', 'pointer_published'):
            _verify(runtime, plan, receipt=prior, journal=journal)
        elif (plan['review'].get('scheduler_reviews')
              and point in ('reconcile_evidence_saved', 'reconcile_scheduler_evidence_saved')):
            _validate_journal(journal)
            _verify_reviewed_scheduler(runtime, plan)
    runtime.checkpoint = checked_checkpoint
    try:
        _store(runtime, journal, evidence, 'receipt',
               {'state': 'present', 'identity': 'authority-receipt-v1', 'value': encode(prior)})
        snapshot = {'state': 'present', 'identity': 'effective-input-v1', 'value': inputs}
        _store(runtime, journal, journal, 'input_before', snapshot)
        _store(runtime, journal, journal, 'input_after', snapshot)
        for path, snapshot in files.items():
            _store(runtime, journal, journal['file_before'], path, snapshot)
        for task_id, item in tasks.items():
            _store(runtime, journal, evidence['tasks'], task_id, item.get('observed_snapshot', item['snapshot']))
        if plan['review'].get('scheduler_reviews') and 'scheduler_before' not in journal:
            journal['scheduler_before'] = {item['name']: evidence['tasks'][item['task_id']]
                                           for item in plan['review']['scheduler_reviews']}
            runtime.journal.save(journal['transaction_id'], journal)
            runtime.checkpoint('reconcile_scheduler_evidence_saved')
        _verify(runtime, plan, receipt=prior, journal=journal)
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
    optional = {'input_before', 'input_after', 'failure', 'completion', 'scheduler_before'}
    if (not isinstance(journal, dict) or not required <= set(journal) or set(journal) - required - optional
            or type(journal['schemaVersion']) is not int or journal['schemaVersion'] != 1
            or journal['status'] not in ('preparing', 'committing', 'committed')
            or ('completion' in journal) != (journal['status'] in ('committing', 'committed'))
            or (journal['cleaned'] and journal['status'] != 'committed')
            or type(journal['cleaned']) is not bool or not isinstance(journal['steps'], list) or journal['steps']
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
    reviews = plan['review'].get('scheduler_reviews', [])
    expected_before = {item['name']: evidence['tasks'][item['task_id']]
                       for item in reviews if item['task_id'] in evidence['tasks']}
    if ('scheduler_before' in journal
            and (not reviews or len(expected_before) != len(reviews)
                 or journal['scheduler_before'] != expected_before)):
        _fail('reconciliation_journal_invalid')
    if reviews and journal['status'] != 'preparing' and 'scheduler_before' not in journal:
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
    if (set(stored) != {'state', 'identity', 'value'} or stored.get('state') != 'present'
            or stored.get('identity') != 'authority-receipt-v1' or not isinstance(stored.get('value'), bytes)):
        _fail('reconciliation_evidence_changed')
    prior = decode(stored['value'])
    _review(plan['review'], baseline['files'], prior['ownership'])
    scheduler_reviews = _scheduler_reviews(plan['review'], prior['ownership'])
    if set(baseline.get('reviewed_scheduler', {})) != set(scheduler_reviews):
        _fail('reconciliation_evidence_changed')
    historical = plan['review'].get('historical_task_ids', [])
    if historical:
        from .authority_history import validate
        if (reg.revision(validate(runtime, prior, historical, plan['review']['historical_receipt_chain']))
                != reg.revision(baseline.get('historical_tasks'))):
            _fail('reconciliation_evidence_changed')
    inputs = runtime.authority._inputs(prior['input_reference'])
    if (reg.revision(prior) != baseline['receipt_revision']
            or reg.revision(inputs) != baseline['inputs_revision']
            or any(reg.revision(runtime.authority._inputs(journal[key])) != baseline['inputs_revision']
                   for key in ('input_before', 'input_after'))):
        _fail('reconciliation_evidence_changed')
    for path, reference in journal['file_before'].items():
        snapshot = runtime.vault.get(reference)
        if ((snapshot.get('state') == 'present' and not isinstance(snapshot.get('value'), bytes))
                or reg.fingerprint(snapshot) != baseline['files'][path]):
            _fail('reconciliation_evidence_changed')
    for task_id, reference in evidence['tasks'].items():
        snapshot = runtime.vault.get(reference)
        expected = baseline['scheduler'][task_id]
        if task_id in scheduler_reviews:
            definition = baseline['reviewed_scheduler'][task_id]
            if not isinstance(definition, dict) or set(definition) != {'spec', 'launcher'}:
                _fail('reconciliation_evidence_changed')
            _validate_scheduler_snapshot(scheduler_reviews[task_id], prior['ownership'][task_id],
                                         snapshot, definition['spec'], launcher=definition['launcher'])
            snapshot = reg._scheduler_config(snapshot)
        if (reg.fingerprint(snapshot) != expected['fingerprint']
                or expected['receipt_identity'] != prior['ownership'][task_id]['identity']
                or expected['current_identity'] != snapshot.get('identity')
                or expected['name'] != prior['ownership'][task_id]['name']):
            _fail('reconciliation_evidence_changed')
    drift = sorted(key for key, item in baseline['scheduler'].items()
                   if key not in scheduler_reviews and item['receipt_identity'] != item['current_identity'])
    if drift != baseline['unselected_scheduler_drift'] or set(drift) & set(plan['review']['task_ids']):
        _fail('reconciliation_evidence_changed')
    completed = _expected_receipt(plan, prior, journal)
    if reg.revision(journal['completion']) != reg.revision({
            'status': 'committed', 'receipt': completed, 'previous': baseline['pointer']}):
        _fail('reconciliation_completion_changed')
    receipt_path = runtime.authority.config.state_root / 'receipts' / (journal['transaction_id'] + '.json')
    if (reg.revision(read_json(receipt_path)) != reg.revision(completed)
            or runtime.authority._pointer() == baseline['pointer']):
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
        if (reg.revision(journal['reconcile']['plan']) != reg.revision(plan)
                or journal['steps'] or journal['locks'] != _locks(plan)):
            _fail('reconciliation_journal_changed')
        if set(runtime.journal.pending(['authority'])) - {transaction_id}:
            _fail('recovery_required', 'journal')
        if journal['status'] == 'committed' and journal.get('cleaned'):
            _verify_terminal(runtime, journal)
            return _result(journal)
        return _attempt(runtime, journal)
