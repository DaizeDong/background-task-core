"""Verify unchanged, initially adopted proofs for explicitly reviewed absent tasks."""
from copy import deepcopy

from . import registration as reg
from .runtime_storage import FileResources, decode, digest, encode, identifier


def _fail():
    raise reg.Conflict('historical_task_evidence_invalid', 'historical_task_ids')


def _without_epoch(proof):
    return {key: value for key, value in proof.items() if key != 'epoch'}


def _proofs(receipt, expected):
    ownership = receipt.get('ownership')
    authority = receipt.get('authority')
    if (not isinstance(ownership, dict) or not isinstance(authority, dict)
            or type(authority.get('authority_epoch')) is not int):
        _fail()
    names = []
    for proof in ownership.values():
        if not isinstance(proof, dict) or not isinstance(proof.get('name'), str):
            _fail()
        names.append(proof['name'].casefold())
    if len(names) != len(set(names)):
        _fail()
    for task_id, original in expected.items():
        proof = ownership.get(task_id)
        if (not isinstance(proof, dict) or type(proof.get('epoch')) is not int
                or proof.get('epoch') != authority.get('authority_epoch')
                or _without_epoch(proof) != original):
            _fail()


def _bootstrap(runtime, receipt, journal, expected, inputs):
    from . import adoption

    bootstrap = journal.get('bootstrap')
    if not isinstance(bootstrap, dict) or 'completion' in journal:
        _fail()
    plan = bootstrap.get('plan')
    if not isinstance(plan, dict):
        _fail()
    adoption._approval(plan, receipt.get('bootstrap_plan_revision'))
    if (receipt.get('authority') != {'authority_epoch': 0, 'migrated_tasks': []}
            or bootstrap.get('input_reference', receipt.get('input_reference')) != receipt.get('input_reference')):
        _fail()
    rows = plan.get('tasks')
    if (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
            or len({row.get('task_id') for row in rows}) != len(rows)):
        _fail()
    indexed = {row.get('task_id'): row for row in rows}
    if inputs['request'] != inputs['submitted']:
        _fail()
    specs = {spec['task_id']: spec for spec in reg._compile({'request': inputs['request']})['task_specs']}
    if set(indexed) != set(specs) or set(indexed) != set(receipt['ownership']):
        _fail()
    for task_id, original in expected.items():
        row, spec = indexed[task_id], specs[task_id]
        proof = {'name': row.get('name'), 'task_path': row.get('task_path'), 'writer': 'legacy',
                 'identity': row.get('identity'), 'enabled': row.get('enabled'),
                 'state': row.get('state'), 'receipt_kind': 'observed-root-task-definition'}
        if (proof != original or proof['name'] != spec['name'] or proof['task_path'] != '\\'
                or proof['state'] != 'present' or not isinstance(proof['identity'], str)):
            _fail()
    config = runtime.authority.config
    paths = [str(config.adoption), str(config.state_root / 'receipts' / (receipt['generation'] + '.json')),
             str(runtime.authority.pointer)]
    steps = journal['steps']
    if len(steps) != 3 or [step.get('key') for step in steps] != paths:
        _fail()
    for step in steps:
        if (step.get('kind') != 'files' or step.get('phase') != 'done'
                or runtime.vault.get(step['before']) != {'state': 'absent'}):
            _fail()
    # Protected publication images bind the bootstrap receipt independently of
    # editable journal prose, including the original protected input reference.
    for step in steps[:2]:
        if reg._document(runtime.vault.get(step['after'])) != receipt:
            _fail()
    pointer = {'generation': receipt['generation'], 'receipt_digest': digest(encode(receipt))}
    if reg._document(runtime.vault.get(steps[2]['after'])) != pointer:
        _fail()
    return reg.revision(inputs)


def _predecessor(runtime, previous, journal, previous_inputs):
    """Check the protected before-state against the explicitly reviewed predecessor."""
    before = runtime.authority._inputs(journal['input_before'])
    if isinstance(journal.get('reconcile'), dict):
        reference = journal['reconcile']['evidence']['receipt']
        snapshot = runtime.vault.get(reference)
        if (snapshot.get('state') != 'present' or snapshot.get('identity') != 'authority-receipt-v1'
                or decode(snapshot['value']) != previous or before != previous_inputs):
            _fail()
    else:
        from .runtime import _submitted_changes
        request = _submitted_changes(previous_inputs['submitted'], before['submitted'], previous_inputs['request'])
        request['machine'].update(previous['authority'])
        if before['request'] != request:
            _fail()
        for path, reference in journal.get('file_before', {}).items():
            if reg.fingerprint(runtime.vault.get(reference)) != previous['file_ownership'].get(path):
                _fail()
        names = {proof['name']: proof for proof in previous['ownership'].values()}
        for name, reference in journal.get('scheduler_before', {}).items():
            snapshot = runtime.vault.get(reference)
            if name not in names or snapshot.get('identity') != names[name]['identity']:
                _fail()
    return reg.revision(before)


def validate(runtime, receipt, task_ids, reviewed_chain):
    """Verify an explicitly reviewed history sequence and its protected evidence."""
    expected = {task_id: _without_epoch(receipt['ownership'][task_id]) for task_id in task_ids}
    config = runtime.authority.config
    pointer = {'generation': receipt['generation'], 'receipt_digest': digest(encode(receipt))}
    seen, files, inputs_seen = set(), {}, {}
    first = deepcopy(receipt)
    child = None
    while True:
        if not isinstance(pointer, dict) or set(pointer) != {'generation', 'receipt_digest'}:
            _fail()
        generation = identifier(pointer['generation'])
        if generation in seen or len(seen) >= len(reviewed_chain):
            _fail()
        reviewed = reviewed_chain[len(seen)]
        if {key: reviewed[key] for key in ('generation', 'receipt_digest')} != pointer:
            _fail()
        seen.add(generation)
        receipt_path = config.state_root / 'receipts' / (generation + '.json')
        journal_path = config.state_root / 'journals' / (generation + '.json')
        resources = FileResources([receipt_path, journal_path], config.state_root / 'stages')
        documents = []
        for path in (receipt_path, journal_path):
            snapshot = reg._read(resources, str(path))
            if snapshot['state'] != 'present':
                _fail()
            documents.append(decode(snapshot['value']))
            files[str(path)] = reg.fingerprint(snapshot)
        current, journal = documents
        if not isinstance(current, dict) or files[str(journal_path)] != reviewed['journal_fingerprint']:
            _fail()
        runtime.journal._validate(generation, journal)
        if (current.get('generation') != generation or current.get('domain') != config.domain
                or digest(encode(current)) != pointer['receipt_digest']
                or (len(seen) == 1 and current != first)
                or journal.get('status') not in ('committed', 'rolled_back')
                or journal.get('cleaned') is not True):
            _fail()
        _proofs(current, expected)
        inputs = runtime.authority._inputs(current['input_reference'])
        machine = inputs['request']['machine']
        if current['authority'] != {key: machine.get(key) for key in ('authority_epoch', 'migrated_tasks')}:
            _fail()
        inputs_seen[generation] = {'effective': reg.revision(inputs)}
        if child is not None:
            inputs_seen[child['transaction_id']]['before'] = _predecessor(runtime, current, child, inputs)
        if 'bootstrap' in journal:
            if journal['status'] != 'committed' or len(seen) != len(reviewed_chain):
                _fail()
            inputs_revision = _bootstrap(runtime, current, journal, expected, inputs)
            return {'task_ids': sorted(task_ids), 'adoption_generation': generation,
                    'initial_inputs_revision': inputs_revision, 'inputs': inputs_seen, 'files': files}
        completion = journal.get('completion')
        reference = journal.get('input_after' if journal['status'] == 'committed' else 'input_before')
        if (not isinstance(completion, dict) or set(completion) != {'status', 'receipt', 'previous'}
                or completion['status'] != journal['status'] or completion['receipt'] != current
                or reference != current.get('input_reference')):
            _fail()
        pointer = completion['previous']
        child = journal
