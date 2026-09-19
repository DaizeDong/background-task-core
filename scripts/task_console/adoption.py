"""Explicit initial authority preparation in the existing task-console runtime.

Planning observes only declared literal root tasks. Apply publishes authority
files with the existing transaction stores; it never calls Scheduler mutation.
Stored observations are review-only. Cooperative locks cannot freeze an external
Scheduler editor; callers must provide the same quiescence boundary as T12.
"""
from copy import deepcopy
from pathlib import Path
import re
import uuid

from fleet_guards import filesystem as fs
from . import allowlist, registration as reg
from .compiler import plan as compile_plan
from .runtime_storage import FileResources, digest, encode, identifier
from .runtime_xml import observation
from .scheduler_windows import task_name

ROLES = {'bindings', 'machine', 'task-health.json', 'TaskNames.ps1', 'categories.json', 'tombstones'}


def _files(runtime, paths):
    return FileResources(paths, runtime.authority.config.state_root / 'stages')


def _config(runtime):
    config = runtime.authority.config
    return {'domain': config.domain, 'private_root': str(config.private_root),
            'state_root': str(config.state_root), 'vault_root': str(config.vault_root),
            'desired': str(config.desired), 'adoption': str(config.adoption),
            'paths': config.paths, 'active_xml': config.active_xml, 'launchers': config.launchers}


def _code(value):
    if not isinstance(value, str) or not re.fullmatch('[a-z][a-z0-9_]{0,79}', value):
        raise reg.Conflict('invalid_prerequisite_code', 'source_binding')
    return value


def _collect(runtime, source_binding, review_only):
    config = runtime.authority.config
    source_binding = fs.validate_path(source_binding, root=config.private_root)
    inputs = _files(runtime, [config.desired, source_binding])
    desired = reg._read(inputs, str(config.desired))
    request = reg._document(desired)
    compiled = compile_plan(request)
    if request['machine']['migrated_tasks'] or request['machine']['authority_epoch'] != 0:
        raise reg.Conflict('initial_authority_required', 'machine')
    specs = compiled['task_specs']
    ids = {spec['task_id'] for spec in specs}
    names = [task_name(spec['name']) for spec in specs]
    if not ids or len(ids) != len(specs) or len({n.casefold() for n in names}) != len(names):
        raise reg.Conflict('duplicate_task_mapping', 'tasks')
    if set(config.paths) != ROLES or set(config.active_xml) != ids or not set(config.launchers) <= ids:
        raise reg.Conflict('resource_scope_mismatch', 'runtime')
    source_snapshot = reg._read(inputs, str(source_binding))
    binding = reg._document(source_snapshot)
    if (not isinstance(binding, dict) or set(binding) != {'schemaVersion', 'input_revision',
            'installed_generation', 'prerequisites', 'tasks', 'sources'} or binding['schemaVersion'] != 1
            or binding['input_revision'] != compiled['input_revision']):
        raise reg.Conflict('source_binding_invalid', 'source_binding')
    if not isinstance(binding['tasks'], dict) or set(binding['tasks']) != ids:
        raise reg.Conflict('source_scope_mismatch', 'source_binding')
    blockers = []
    def block(code, task_id=None):
        blockers.append({'code': code, **({'task_id': task_id} if task_id else {})})
    if review_only:
        block('review_only')
    generation = binding['installed_generation']
    if generation is None:
        block('installed_generation_required')
    elif not isinstance(generation, str) or not generation.strip():
        raise reg.Conflict('source_binding_invalid', 'installed_generation')
    if not isinstance(binding['prerequisites'], list):
        raise reg.Conflict('source_binding_invalid', 'prerequisites')
    for code in binding['prerequisites']:
        block(_code(code))
    for task_id, owner in binding['tasks'].items():
        if (not isinstance(owner, dict) or set(owner) != {'status', 'reason_codes'}
                or owner['status'] not in ('reviewed', 'blocked', 'unclassified')
                or not isinstance(owner['reason_codes'], list)):
            raise reg.Conflict('source_binding_invalid', 'owner')
        if owner['status'] != 'reviewed':
            block('owner_' + owner['status'], task_id)
        for code in owner['reason_codes']:
            block(_code(code), task_id)
    sources = binding['sources']
    if not isinstance(sources, dict) or not sources:
        raise reg.Conflict('source_evidence_required', 'source_binding')
    source_files = _files(runtime, sources)
    source_proofs = {}
    for path, expected in sorted(sources.items()):
        if not Path(path).is_absolute() or not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
            raise reg.Conflict('source_binding_invalid', 'sources')
        snapshot = reg._read(source_files, path)
        source_proofs[path] = reg.fingerprint(snapshot)
        if snapshot['state'] != 'present' or digest(snapshot['value']) != expected:
            block('source_changed')
    resources = list(config.paths.values()) + list(config.launchers.values()) + [p for p in config.active_xml.values() if p]
    before = {p: reg._read(runtime.files, p) for p in sorted(set(resources))}
    # Reuse the existing bounded JSON and literal allowlist parsers on actual
    # projection bytes, rather than trusting the request's stored baseline.
    documents = {role: reg._document(before[path]) for role, path in config.paths.items() if role != 'TaskNames.ps1'}
    names_text = reg._decode(before[config.paths['TaskNames.ps1']])
    try:
        allowlist.literal_names(names_text)
    except allowlist.AllowlistError:
        raise reg.Conflict('invalid_allowlist', 'TaskNames.ps1') from None
    if any(not isinstance(doc, dict) for doc in documents.values()):
        raise reg.Conflict('invalid_projection', 'files')
    if documents['bindings'] != request['bindings'] or documents['machine'] != request['machine']:
        block('input_projection_difference')
    if documents['tombstones']:
        block('initial_tombstones_not_empty')
    current_request = deepcopy(request)
    current_request['baseline'].update(task_names=names_text, task_health=documents['task-health.json'],
                                       categories=documents['categories.json'])
    parity = compile_plan(current_request)['parity']
    for role in ('task_names', 'task_health', 'categories'):
        if parity[role]['status'] in ('invalid', 'not-checked'):
            block('projection_incomplete')
    tasks = []
    for spec in specs:
        task_id, name = spec['task_id'], spec['name']
        row = {'task_id': task_id, 'name': name, 'task_path': '\\'}
        try:
            snapshot = reg._read(runtime.scheduler, name)
            reg._scheduler_config(snapshot)
            if snapshot['state'] == 'present':
                if not re.fullmatch(r'task-definition:[a-f0-9]{64}', snapshot['identity']):
                    raise reg.Conflict('task_identity_invalid', 'scheduler')
                if observation(name, snapshot['value'])['identity'] != snapshot['identity']:
                    raise reg.Conflict('task_identity_mismatch', 'scheduler')
            row.update(state=snapshot['state'], identity=snapshot.get('identity'),
                       enabled=snapshot.get('value', {}).get('enabled'),
                       running=snapshot.get('value', {}).get('running'),
                       fingerprint=reg.fingerprint(reg._scheduler_config(snapshot)))
        except reg.ContractError as exc:
            row.update(state='unknown', code=exc.code)
            block(exc.code, task_id)
        tasks.append(row)
    running = sorted(row['task_id'] for row in tasks if row.get('running'))
    input_proofs = {str(config.desired): reg.fingerprint(desired), str(source_binding): reg.fingerprint(source_snapshot)}
    locks = sorted({'authority', *('task:' + i for i in ids),
                    *('file:' + p for p in [*before, *input_proofs, *source_proofs,
                                           str(config.adoption), str(runtime.authority.pointer)])})
    result = {'schemaVersion': 1, 'mode': 'initial-adoption', 'review_only': review_only,
              'applicable': not blockers, 'source_binding': str(source_binding),
              'config_revision': reg.revision(_config(runtime)), 'input_revision': compiled['input_revision'],
              'inputs': input_proofs, 'sources': source_proofs,
              'files': {p: reg.fingerprint(s) for p, s in before.items()},
              'tasks': tasks, 'task_count': len(tasks), 'migrated_tasks': [],
              'running_tasks': running,
              'projection_parity': {k: parity[k]['status'] for k in ('task_names', 'task_health', 'categories')},
              'blockers': blockers, 'lock_keys': locks}
    return result, request


def _available(runtime, own=None):
    config = runtime.authority.config
    records = runtime.journal.list()
    if any(r['transaction_id'] != own for r in records):
        raise reg.Conflict('existing_transaction_evidence', 'authority')
    if own is None:
        targets = _files(runtime, [config.adoption, runtime.authority.pointer])
        if any(reg._read(targets, str(p))['state'] != 'absent' for p in (config.adoption, runtime.authority.pointer)):
            raise reg.Conflict('authority_already_exists', 'authority')
    receipts = fs.validate_path(config.state_root / 'receipts')
    owned = set()
    if own:
        receipt = str(receipts / (own + '.json'))
        owned = {own + '.json'}
        # A cleaned journal has retired its stage ownership. A later file at
        # that reserved name is foreign evidence, even for the same token.
        if any(r['transaction_id'] == own and not r.get('cleaned') for r in records):
            owned.add(_files(runtime, [receipt]).staging_path(receipt, own + ':1').name)
    if receipts.exists() and any(p.name not in owned for p in receipts.iterdir()):
        raise reg.Conflict('existing_receipt_evidence', 'authority')


def _stable(plan):
    """Plan identity without the independently observed execution state.

    Epoch-zero bootstrap publishes metadata only; it never edits a Scheduler
    object, so a daemon that starts or stops between approval and publication
    changes no payload this transaction owns. Running is still collected and
    shown for review, but keeping it inside the plan identity would silently
    reimpose the all-task idle gate through full-plan equality. Definition,
    enabled, absence, projection, source and input proofs stay inside.
    """
    rows = plan.get('tasks')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise reg.Conflict('invalid_bootstrap_plan', 'tasks')
    stable = {k: v for k, v in plan.items() if k not in ('plan_revision', 'running_tasks')}
    stable['tasks'] = [{k: v for k, v in row.items() if k != 'running'} for row in rows]
    return stable


def _seal(plan):
    return dict(plan, plan_revision=reg.revision(_stable(plan)))


def build_plan(runtime, *, source_binding, review_only=False):
    """Return a value-free review plan; does not provision runtime state."""
    if type(review_only) is not bool:
        raise reg.Conflict('invalid_arguments', 'review_only')
    plan, _ = _collect(runtime, source_binding, review_only)
    try:
        _available(runtime)
    except reg.Conflict as exc:
        plan['blockers'].append({'code': exc.code})
        plan['applicable'] = False
    return _seal(plan)


def _approval(plan, approval):
    if not isinstance(plan, dict) or plan.get('mode') != 'initial-adoption':
        raise reg.Conflict('invalid_bootstrap_plan', 'plan')
    if not approval or approval != plan.get('plan_revision') or reg.revision(_stable(plan)) != approval:
        raise reg.Conflict('approval_revision_required', 'plan')
    if plan.get('review_only') is not False or plan.get('applicable') is not True or plan.get('blockers'):
        raise reg.Conflict('bootstrap_blocked', 'plan')


def _revalidate(runtime, plan):
    fresh, request = _collect(runtime, plan['source_binding'], False)
    if _stable(fresh) != _stable(plan):
        raise reg.Conflict('bootstrap_plan_changed', 'plan')
    return request


def apply(plan, approval_revision, *, runtime):
    """Explicit approval publishes initial authority only; zero tasks migrate."""
    _approval(plan, approval_revision)
    with runtime.locks.hold(plan['lock_keys']):
        _available(runtime)
        request = _revalidate(runtime, plan)
        tx = uuid.uuid4().hex
        record = {'schemaVersion': 1, 'transaction_id': tx, 'status': 'preparing',
                  'locks': plan['lock_keys'], 'steps': [], 'cleaned': False,
                  'bootstrap': {'plan': plan}, 'input_revision': plan['input_revision']}
        runtime.journal.create(tx, record)
        _prepare(runtime, record, request)
        return _finish(runtime, record)


def _prepare(runtime, record, request):
    """Continue durable intent; never infer ownership from equal bytes alone."""
    tx = record['transaction_id']
    plan = record['bootstrap']['plan']
    config = runtime.authority.config
    paths = [str(config.adoption), str(config.state_root / 'receipts' / (tx + '.json')),
             str(runtime.authority.pointer)]
    resources = _files(runtime, paths)
    steps = record['steps']
    if len(steps) > 3:
        raise reg.Conflict('bootstrap_resource_mismatch', 'journal')
    for i, step in enumerate(steps):
        if (step.get('kind') != 'files' or step.get('key') != paths[i]
                or step.get('token') != tx + ':' + str(i)
                or step.get('phase') not in ('staging', 'staged')
                or runtime.vault.get(step['before']) != {'state': 'absent'}):
            raise reg.Conflict('bootstrap_resource_mismatch', 'journal')
    # Incomplete preparation has never published anything. Check every target
    # before writing any further intent, including targets not reached yet.
    if any(reg._read(resources, path)['state'] != 'absent' for path in paths):
        raise reg.Conflict('authority_already_exists', 'authority')
    snapshot = {'state': 'present', 'identity': 'effective-input-v1',
                'value': {'submitted': request, 'request': deepcopy(request)}}
    reference = record['bootstrap'].get('input_reference')
    if reference is None and steps:
        # Older journals did not retain the input reference separately. Recover
        # it only from the first stage's durable identity, never a vault scan.
        first = steps[0]
        if 'after' in first:
            reference = reg._document(runtime.vault.get(first['after']))['input_reference']
        else:
            proof = resources.prepared_snapshot(paths[0], first['token'])
            if proof is not None:
                reference = reg._document(proof)['input_reference']
    if reference is None:
        # put allocates a new object even for the same key. A crash before save
        # may leave an unreferenced blob; retain it and checkpoint the new ref.
        reference = runtime.vault.put(tx + ':initial-input', snapshot)
    if (not isinstance(reference, str) or not reference.endswith(':' + tx)
            or runtime.vault.get(reference) != snapshot):
        raise reg.Conflict('bootstrap_input_mismatch', 'journal')
    if record['bootstrap'].get('input_reference') != reference:
        record['bootstrap']['input_reference'] = reference
        runtime.journal.save(tx, record)
    runtime.checkpoint('bootstrap_input_saved')
    receipt = {'schemaVersion': 1, 'domain': runtime.authority.config.domain, 'generation': tx,
               'authority': {'authority_epoch': 0, 'migrated_tasks': []}, 'input_reference': reference,
               'ownership': {row['task_id']: {'name': row['name'], 'task_path': '\\', 'epoch': 0,
                    'writer': 'legacy', 'identity': row['identity'], 'enabled': row['enabled'],
                    'state': row['state'], 'receipt_kind': 'observed-root-task-definition'} for row in plan['tasks']},
               'file_ownership': plan['files'], 'bootstrap_plan_revision': plan['plan_revision']}
    values = [encode(receipt), encode(receipt), encode({'generation': tx, 'receipt_digest': digest(encode(receipt))})]
    for i, (path, value) in enumerate(zip(paths, values)):
        if i == len(steps):
            step = {'kind': 'files', 'key': path, 'token': tx + ':' + str(i), 'phase': 'staging',
                    'before': runtime.vault.put(tx + ':before:' + str(i), {'state': 'absent'})}
            steps.append(step)
            runtime.journal.save(tx, record)
        step = steps[i]
        runtime.checkpoint('bootstrap_stage_intent')
        after = resources.prepare(path, value, step['token'])
        runtime.checkpoint('bootstrap_stage_prepared')
        if 'after' in step:
            if runtime.vault.get(step['after']) != after:
                raise reg.Conflict('stage_owner_lost', 'files')
        else:
            step['after'] = runtime.vault.put(tx + ':after:' + str(i), after)
        step['phase'] = 'staged'
        runtime.journal.save(tx, record)
        runtime.checkpoint('bootstrap_stage_saved')
    runtime.checkpoint('bootstrap_staged')


def _staged(runtime, record):
    """The exact three authority resources this transaction is allowed to own."""
    tx = record['transaction_id']
    if len(record['steps']) != 3 or any('after' not in step for step in record['steps']):
        raise reg.Conflict('bootstrap_preparation_incomplete', 'journal')
    config = runtime.authority.config
    expected_paths = [str(config.adoption), str(config.state_root / 'receipts' / (tx + '.json')),
                      str(runtime.authority.pointer)]
    if [step['key'] for step in record['steps']] != expected_paths:
        raise reg.Conflict('bootstrap_resource_mismatch', 'journal')
    return _files(runtime, expected_paths)


def _retire(runtime, record):
    """Retire the stages of an already committed bootstrap, idempotently.

    A committed transaction has already spent its publication approval, so the
    historical source and task plan is not reobserved here: an unrelated later
    edit to an obsolete input file must not strand cleanup of authority that is
    published and readable. What is revalidated is this transaction's own
    evidence - the retained journal, the exact owned stage identities, the three
    published files and the receipt this approval produced. Any mismatch refuses
    and keeps the foreign or ambiguous file; FileResources.cleanup applies the
    same identity test to every stage before unlinking it.
    """
    tx = record['transaction_id']
    plan = record['bootstrap']['plan']
    _available(runtime, own=tx)
    resources = _staged(runtime, record)
    for step in record['steps']:
        if reg._read(resources, step['key']) != runtime.vault.get(step['after']):
            raise reg.Conflict('bootstrap_resource_changed', 'authority')
    receipt = runtime.authority.receipt()  # Ordinary reader resolves this generation.
    if (receipt.get('generation') != tx
            or receipt.get('bootstrap_plan_revision') != plan['plan_revision']
            or receipt.get('authority', {}).get('migrated_tasks') != []):
        raise reg.Conflict('bootstrap_authority_mismatch', 'authority')
    if not record.get('cleaned'):
        for step in record['steps']:
            resources.cleanup(step['token'])
        record['cleaned'] = True
        runtime.journal.save(tx, record)
    return {'schemaVersion': 1, 'ok': True, 'status': 'committed', 'cleaned': True,
            'transaction_id': tx, 'task_count': plan['task_count'], 'migrated_tasks': 0,
            'plan_revision': plan['plan_revision']}


def _finish(runtime, record):
    tx = record['transaction_id']
    plan = record['bootstrap']['plan']
    _available(runtime, own=tx)
    request = _revalidate(runtime, plan)
    if record['status'] == 'preparing' and (len(record['steps']) != 3
            or any('after' not in step for step in record['steps'])):
        _prepare(runtime, record, request)
    resources = _staged(runtime, record)
    for step in record['steps']:
        current = reg._read(resources, step['key'])
        if current != runtime.vault.get(step['after']) and current != runtime.vault.get(step['before']):
            raise reg.Conflict('bootstrap_resource_changed', 'authority')
    record['status'] = 'publishing'
    for i, step in enumerate(record['steps']):
        desired = runtime.vault.get(step['after'])
        current = reg._read(resources, step['key'])
        if current != desired:
            if current != runtime.vault.get(step['before']) or step['phase'] == 'done':
                raise reg.Conflict('bootstrap_resource_changed', 'authority')
            _revalidate(runtime, plan)
            step['phase'] = 'publishing'
            runtime.journal.save(tx, record)
            resources.publish(step['key'], current, desired)
            if reg._read(resources, step['key']) != desired:
                raise reg.Conflict('bootstrap_readback_failed', 'authority')
        # The fault boundary intentionally precedes the durable done marker.
        runtime.checkpoint('bootstrap_pointer_written' if i == 2 else 'bootstrap_receipt_written')
        step['phase'] = 'done'
        runtime.journal.save(tx, record)
    _revalidate(runtime, plan)
    for step in record['steps']:
        if reg._read(resources, step['key']) != runtime.vault.get(step['after']):
            raise reg.Conflict('bootstrap_resource_changed', 'authority')
    runtime.authority.load()  # Validate the ordinary authority/input reader too.
    record['status'] = 'committed'
    runtime.journal.save(tx, record)
    runtime.checkpoint('bootstrap_committed')
    for step in record['steps']:
        resources.cleanup(step['token'])
    record['cleaned'] = True
    runtime.journal.save(tx, record)
    return {'schemaVersion': 1, 'ok': True, 'status': 'committed', 'cleaned': True,
            'transaction_id': tx, 'task_count': plan['task_count'], 'migrated_tasks': 0,
            'plan_revision': plan['plan_revision']}


def resume(transaction_id, approval_revision, *, runtime):
    """Resume only a proven bootstrap transaction, retaining ambiguous evidence."""
    record = runtime.journal.load(identifier(transaction_id))
    if not isinstance(record.get('bootstrap'), dict):
        raise reg.Conflict('bootstrap_transaction_required', 'journal')
    plan = record['bootstrap']['plan']
    _approval(plan, approval_revision)
    if record['locks'] != plan['lock_keys']:
        raise reg.Conflict('bootstrap_resource_mismatch', 'journal')
    with runtime.locks.hold(plan['lock_keys']):
        record = runtime.journal.load(transaction_id)
        _approval(record['bootstrap']['plan'], approval_revision)
        if (record['locks'] != plan['lock_keys'] or record['bootstrap']['plan'] != plan
                or record['input_revision'] != plan['input_revision']
                or record['status'] not in ('preparing', 'publishing', 'committed')):
            raise reg.Conflict('bootstrap_resource_mismatch', 'journal')
        if record['status'] == 'committed':
            # Terminal: publication already happened under this same approval.
            return _retire(runtime, record)
        return _finish(runtime, record)
