"""Generation-bound snapshots and explicit restore plans for the shared controller.

The backup owner stages destinations. This adapter returns private XML only to
export callers; restore returns completed receipts, never deferred executable work.
All mutations use registration.apply, including legacy full-XML restoration.
"""
from copy import deepcopy

from . import registration as reg
from .scheduler_windows import task_name, validate_value


def _proof(bundle, spec):
    epoch, migrated = reg._authority(bundle)
    proof = bundle.get('ownership', {}).get(spec['task_id'])
    expected = {'name': spec['name'], 'task_path': '\\', 'epoch': epoch,
                'writer': 'declarations' if spec['task_id'] in migrated else 'legacy'}
    if not isinstance(proof, dict) or 'identity' not in proof or any(proof.get(k) != v for k, v in expected.items()):
        raise reg.Conflict('ownership_required', 'ownership')
    task_name(spec['name'])
    return proof


def export_tasks(runtime):
    """Fresh literal root queries under authority, task and resource locks."""
    with runtime.locks.hold(['authority']):
        if runtime.journal.pending(['authority']):
            raise reg.Conflict('recovery_required', 'journal')
        bundle = runtime.load()
        specs = reg._compile(bundle)['task_specs']
        paths = set(bundle['paths'].values()) | set(bundle.get('launchers', {}).values())
        paths |= {p for p in bundle.get('active_xml', {}).values() if p is not None}
        keys = ['task:' + s['task_id'] for s in specs] + ['file:' + p for p in paths]
        with runtime.locks.hold(sorted(keys)):
            if reg.revision(runtime.load()) != reg.revision(bundle):
                raise reg.Conflict('input_changed', 'input_revision')
            for path in paths:
                if reg.fingerprint(reg._read(runtime.files, path)) != bundle['file_ownership'].get(path):
                    raise reg.Conflict('file_ownership_changed', 'files')
            tombstones = reg._document(reg._read(runtime.files, bundle['paths']['tombstones']))
            if not isinstance(tombstones, dict):
                raise reg.Conflict('invalid_tombstones', 'tombstones')
            rows, excluded = [], []
            for spec in specs:
                proof = _proof(bundle, spec)
                base = {'task_id': spec['task_id'], 'name': spec['name'], 'task_path': '\\',
                        'writer': proof['writer'], 'ownership_identity': proof['identity']}
                if spec['task_id'] in tombstones or not spec['backup']:
                    excluded.append({**base, 'reason': 'retired' if spec['task_id'] in tombstones else 'backup_disabled'})
                    continue
                try:
                    snapshot = reg._read(runtime.scheduler, spec['name'])
                    if snapshot['state'] == 'present':
                        validate_value(snapshot['value'])
                        if snapshot['identity'] != proof['identity']:
                            raise reg.Conflict('scheduler_ownership_changed', 'ownership')
                    rows.append({**base, 'status': 'COMPLETED' if snapshot['state'] == 'present' else 'ABSENT',
                                 'enabled': snapshot.get('value', {}).get('enabled'), 'snapshot': snapshot,
                                 'snapshot_revision': reg.revision(snapshot)})
                except reg.ContractError as exc:
                    rows.append({**base, 'status': 'FAILED', 'error': exc.to_dict()})
            if reg.revision(runtime.load()) != reg.revision(bundle):
                raise reg.Conflict('input_changed', 'input_revision')
            result = {'schemaVersion': 1, 'operation': 'export', 'read_only': True,
                      'status': 'INCOMPLETE' if any(r['status'] == 'FAILED' for r in rows) else 'COMPLETED',
                      'generation': bundle.get('authority_generation'), 'input_revision': reg.revision(bundle),
                      'authority_epoch': bundle['request']['machine']['authority_epoch'],
                      'migrated_tasks': bundle['request']['machine']['migrated_tasks'],
                      'tasks': rows, 'excluded': excluded}
            result['ok'] = result['status'] == 'COMPLETED'
            result['receipt_revision'] = reg.revision(result)
            return result


def _archive(archive):
    if (not isinstance(archive, dict) or archive.get('schemaVersion') != 1
            or archive.get('operation') != 'export' or archive.get('status') != 'COMPLETED'
            or archive.get('ok') is not True or not isinstance(archive.get('tasks'), list)):
        raise reg.Conflict('completed_export_required', 'archive')
    unsigned = {k: v for k, v in archive.items() if k != 'receipt_revision'}
    if archive.get('receipt_revision') != reg.revision(unsigned):
        raise reg.Conflict('export_receipt_changed', 'archive')
    tasks = {}
    for row in archive['tasks']:
        if not isinstance(row, dict) or not isinstance(row.get('task_id'), str) or row['task_id'] in tasks:
            raise reg.Conflict('invalid_export_scope', 'archive')
        tasks[row['task_id']] = row
    return tasks


def restore_plan(runtime, archive, task_ids, *, rebinding=None):
    rows = _archive(archive)
    if (not isinstance(task_ids, list) or not task_ids or len(set(task_ids)) != len(task_ids)
            or not set(task_ids) <= rows.keys()):
        raise reg.Conflict('invalid_task_selection', 'task_ids')
    intent = {'operation': 'restore', 'task_ids': task_ids, 'migrate': False,
              'approve_enable': False, 'reason': '', 'restore_tasks': {i: rows[i] for i in task_ids},
              'export_revision': archive['receipt_revision'], 'rebinding': rebinding or {}}
    return reg._assemble(runtime, intent)[0]


def validate_restore_target(bundle, intent, spec, current, *, scheduler=None):
    """Rechecked by the transaction under locks, including exact rebinding evidence."""
    row = intent['restore_tasks'].get(spec['task_id'])
    if (not isinstance(row, dict) or row.get('status') != 'COMPLETED'
            or row.get('task_id') != spec['task_id'] or row.get('task_path') != '\\'
            or row.get('name') != spec['name']):
        raise reg.Conflict('completed_task_export_required', 'restore_tasks')
    source = reg._snapshot(row.get('snapshot'))
    if source['state'] != 'present' or row.get('snapshot_revision') != reg.revision(source):
        raise reg.Conflict('export_snapshot_changed', 'restore_tasks')
    validate_value(source['value'])
    principal = _principal(source, scheduler=scheduler, name=spec['name'])
    if not isinstance(principal, dict) or type(row.get('enabled')) is not bool or row['enabled'] != source['value']['enabled']:
        raise reg.Conflict('incomplete_restore_evidence', 'restore_tasks')
    migrated = spec['task_id'] in bundle['request']['machine']['migrated_tasks']
    target_principal = spec['principal'] if migrated else principal
    if principal != spec['principal'] and not migrated:
        raise reg.Conflict('legacy_principal_conversion_required', 'principal')
    if target_principal.get('logon_type') in ('Password', 'InteractiveTokenOrPassword'):
        raise reg.Conflict('auth_pending', 'credential_ref')
    changed_principal = principal != target_principal or (current['state'] == 'present'
        and _principal(current, scheduler=scheduler, name=spec['name']) != target_principal)
    if current['state'] == 'absent' or changed_principal:
        evidence = intent['rebinding'].get(spec['task_id'])
        expected = {'task_id': spec['task_id'], 'source_revision': row['snapshot_revision'],
                    'target_revision': reg.fingerprint(reg._scheduler_config(current)),
                    'target_principal': target_principal, 'generation': bundle.get('authority_generation'),
                    'approved': True}
        if (not isinstance(evidence, dict) or set(evidence) != set(expected) | {'reason'}
                or any(evidence.get(k) != v for k, v in expected.items())
                or not isinstance(evidence['reason'], str) or not evidence['reason'].strip()):
            raise reg.Conflict('reviewed_rebinding_required', 'rebinding')
    return row['enabled']


def _principal_fields(value, *, prepared=False):
    """Shared strict parser for original and identity-checked prepared values."""
    from .runtime_xml import NS, parse
    container = _xml_group(parse(value['xml']), 'Principals', 'principal')
    if len(container) != 1:
        raise reg.Conflict('principal_requires_explicit_adapter', 'principal')
    principal = container[0]
    tags = {'user_id': 'UserId', 'logon_type': 'LogonType', 'run_level': 'RunLevel'}
    if (principal.tag != '{' + NS + '}Principal' or set(principal.attrib) - {'id'}
            or (principal.text or '').strip() or any((item.tail or '').strip() for item in principal)
            or any(item.tag not in {'{' + NS + '}' + tag for tag in tags.values()} for item in principal)):
        raise reg.Conflict('principal_requires_explicit_adapter', 'principal')
    result = {}
    for key, tag in tags.items():
        nodes = principal.findall('{' + NS + '}' + tag)
        if not nodes and key == 'run_level':
            # Task Scheduler's schema default, after successful preparation.
            result[key] = 'LeastPrivilege' if prepared else None
        elif len(nodes) != 1 or not (nodes[0].text or '').strip() or len(nodes[0]) or nodes[0].attrib:
            raise reg.Conflict('principal_requires_explicit_adapter', 'principal')
        else:
            result[key] = nodes[0].text
    if (result['logon_type'] not in ('InteractiveToken', 'S4U', 'ServiceAccount',
                                     'Password', 'InteractiveTokenOrPassword')
            or result['run_level'] not in (None, 'LeastPrivilege', 'HighestAvailable')):
        raise reg.Conflict('principal_requires_explicit_adapter', 'principal')
    return result


def _principal(snapshot, *, scheduler=None, name=None):
    # Raw XML remains the backup/CAS image. Only the existing preparation
    # boundary may resolve an omitted default; identity and logon stay explicit.
    principal = _principal_fields(snapshot['value'])
    if principal['run_level'] is None:
        if scheduler is None:
            raise reg.Conflict('principal_requires_explicit_adapter', 'principal')
        prepared = _binding_preparation(snapshot, scheduler, name)
        normalized = _principal_fields(prepared['value'], prepared=True)
        if normalized != {**principal, 'run_level': 'LeastPrivilege'}:
            raise reg.Conflict('principal_requires_explicit_adapter', 'principal')
        return normalized
    return principal


def _xml_group(root, tag, field):
    from .runtime_xml import NS
    groups = root.findall('{' + NS + '}' + tag)
    if (len(groups) != 1 or groups[0].attrib or (groups[0].text or '').strip()
            or any((item.tail or '').strip() for item in groups[0])):
        raise reg.Conflict(field + '_requires_explicit_adapter', field)
    return groups[0]


def _binding_preparation(snapshot, scheduler, name):
    from .runtime_xml import observation, preserves_xml
    prepared = reg._snapshot(scheduler.prepare(name, deepcopy(snapshot['value']), 'readback'))
    if prepared['state'] != 'present':
        raise reg.Conflict('preparation_missing', 'scheduler')
    validate_value(prepared['value'])
    before, after = deepcopy(snapshot['value']), deepcopy(prepared['value'])
    if not preserves_xml(before.pop('xml'), after.pop('xml')) or before != after:
        raise reg.Conflict('preparation_changed_settings', 'scheduler')
    expected_identity = observation(name, prepared['value'])['identity']
    if (prepared['identity'] != expected_identity or
            ('candidate_identity' in prepared and prepared['candidate_identity'] != expected_identity)):
        raise reg.Conflict('snapshot_definition_changed', 'scheduler')
    return prepared


def restore(runtime, plan, approval):
    """One existing transaction; the installer receives only readback receipts."""
    if (not isinstance(plan, dict) or plan.get('intent', {}).get('operation') != 'restore'
            or not approval or approval != plan.get('plan_revision')
            or approval != reg.revision({k: v for k, v in plan.items() if k != 'plan_revision'})):
        raise reg.Conflict('restore_approval_required', 'plan_revision')
    result = reg.apply(plan, plan['input_revision'], runtime=runtime, restore_approval=approval)
    return restore_receipt(runtime, result, plan['task_ids'], approval)


def restore_receipt(runtime, result, task_ids, approval):
    # Lock again after apply releases; refuse to describe another writer's state.
    with runtime.locks.hold(['authority'] + ['task:' + i for i in task_ids]):
        bundle = runtime.load()
        completed = result.get('ok') is True and bundle.get('authority_generation') == result['transaction_id']
        rows = []
        for task_id in task_ids:
            proof = bundle['ownership'][task_id]
            row = {'task_id': task_id, 'name': proof['name'], 'task_path': '\\', 'writer': proof['writer'],
                   'status': 'INCOMPLETE', 'transaction_id': result['transaction_id']}
            if completed:
                snapshot = reg._read(runtime.scheduler, proof['name'])
                if snapshot['state'] != 'present' or snapshot.get('identity') != proof['identity']:
                    raise reg.Conflict('restore_receipt_changed', 'scheduler')
                row.update(status='COMPLETED', identity=snapshot['identity'], enabled=snapshot['value']['enabled'])
            rows.append(row)
        return {'schemaVersion': 1, 'operation': 'restore', 'status': 'COMPLETED' if completed else 'INCOMPLETE',
                'ok': completed, 'tasks_registered': completed, 'runtime_ready': False,
                'auth_pending': result.get('failure', {}).get('code') == 'auth_pending' if result.get('failure') else False,
                'generation': bundle.get('authority_generation'), 'approval_revision': approval,
                'authority_epoch': bundle['request']['machine']['authority_epoch'],
                'migrated_tasks': bundle['request']['machine']['migrated_tasks'], 'tasks': rows,
                'transaction': result, 'shortfalls': [] if completed else ['Restore transaction requires inspection/recovery; no outer registration is permitted.']}


def restore_recover(runtime, transaction_id, approval):
    record = runtime.journal.load(transaction_id)
    evidence = record.get('restore', {})
    if not approval or evidence.get('approval') != approval:
        raise reg.Conflict('restore_approval_required', 'journal')
    result = reg.recover(transaction_id, runtime=runtime)
    return restore_receipt(runtime, result, evidence['task_ids'], approval)
