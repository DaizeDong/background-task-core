"""Generation-bound snapshots and explicit restore plans for the shared controller.

The backup owner stages destinations. This adapter returns private XML only to
export callers; restore returns completed receipts, never deferred executable work.
All mutations use registration.apply, including legacy full-XML restoration.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import re

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


def validate_backup_archive(archive):
    """Validate complete export evidence without reading or changing runtime state."""
    from .runtime_storage import identifier
    from .runtime_xml import observation

    tasks = _archive(archive)
    fields = {'schemaVersion', 'operation', 'read_only', 'status', 'generation', 'input_revision',
              'authority_epoch', 'migrated_tasks', 'tasks', 'excluded', 'ok', 'receipt_revision'}
    identifier(archive.get('generation'))
    if (set(archive) != fields or type(archive['schemaVersion']) is not int
            or archive.get('read_only') is not True
            or not isinstance(archive.get('input_revision'), str)
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', archive['input_revision'])
            or type(archive.get('authority_epoch')) is not int or archive['authority_epoch'] < 0
            or not isinstance(archive.get('excluded'), list)):
        raise reg.Conflict('incomplete_export_evidence', 'archive')
    scope, names = {}, set()
    common = {'task_id', 'name', 'task_path', 'writer', 'ownership_identity'}
    for row, excluded in [(r, False) for r in tasks.values()] + [(r, True) for r in archive['excluded']]:
        expected = common | ({'reason'} if excluded else {'status', 'enabled', 'snapshot', 'snapshot_revision'})
        if (not isinstance(row, dict) or set(row) != expected
                or not isinstance(row.get('task_id'), str) or not row['task_id'].strip()
                or row['task_id'] in scope or row.get('task_path') != '\\'
                or row.get('writer') not in ('legacy', 'declarations')):
            raise reg.Conflict('invalid_export_scope', 'archive')
        name = task_name(row['name'])
        if name.casefold() in names:
            raise reg.Conflict('invalid_export_scope', 'archive')
        names.add(name.casefold())
        identity = row['ownership_identity']
        if identity is not None and (not isinstance(identity, str) or not re.fullmatch(r'task-definition:[a-f0-9]{64}', identity)):
            raise reg.Conflict('invalid_export_identity', 'archive')
        scope[row['task_id']] = row
        if excluded:
            if row['reason'] not in ('retired', 'backup_disabled'):
                raise reg.Conflict('invalid_export_exclusion', 'archive')
            continue
        snapshot = reg._snapshot(row['snapshot'])
        if row['snapshot_revision'] != reg.revision(snapshot):
            raise reg.Conflict('export_snapshot_changed', 'archive')
        if snapshot['state'] == 'absent':
            if row['status'] != 'ABSENT' or row['enabled'] is not None:
                raise reg.Conflict('invalid_absent_export', 'archive')
        else:
            value = validate_value(snapshot['value'])
            observed = observation(name, value)
            if (row['status'] != 'COMPLETED' or type(row['enabled']) is not bool
                    or row['enabled'] != value['enabled'] or snapshot['identity'] != identity
                    or observed['identity'] != snapshot['identity']):
                raise reg.Conflict('export_definition_changed', 'archive')
    migrated = archive.get('migrated_tasks')
    if (not scope or not isinstance(migrated, list)
            or any(not isinstance(i, str) or not i for i in migrated)
            or len(set(migrated)) != len(migrated) or not set(migrated) <= scope.keys()
            or any((r['writer'] == 'declarations') != (i in migrated) for i, r in scope.items())):
        raise reg.Conflict('invalid_export_scope', 'archive')
    return scope


def validate_backup(archive, current, xml_files):
    """Compare two complete owner receipts and exact UTF-8 backup XML bytes.

    The current receipt must come from export_tasks. Execution state is transient;
    raw XML, enabled state, ownership and generation remain exact comparisons.
    """
    backed = validate_backup_archive(archive)
    live = validate_backup_archive(current)
    if (backed.keys() != live.keys() or any(archive[key] != current[key] for key in
            ('generation', 'input_revision', 'authority_epoch', 'migrated_tasks'))):
        raise reg.Conflict('backup_scope_or_generation_changed', 'backup')
    expected = {}
    for task_id, row in backed.items():
        fresh = live[task_id]
        metadata = {k: v for k, v in row.items() if k not in ('snapshot', 'snapshot_revision')}
        fresh_metadata = {k: v for k, v in fresh.items() if k not in ('snapshot', 'snapshot_revision')}
        if metadata != fresh_metadata:
            raise reg.Conflict('backup_task_changed', 'backup')
        if 'snapshot' in row and reg._scheduler_config(row['snapshot']) != reg._scheduler_config(fresh['snapshot']):
            raise reg.Conflict('backup_definition_changed', 'backup')
        if row.get('status') == 'COMPLETED':
            name = hashlib.sha256(task_id.encode('utf-8')).hexdigest() + '.xml'
            expected[name] = row['snapshot']['value']['xml'].encode('utf-8')
    if not isinstance(xml_files, dict) or xml_files.keys() != expected.keys():
        raise reg.Conflict('backup_xml_coverage_changed', 'backup')
    if any(not isinstance(xml_files[name], bytes) or xml_files[name] != data for name, data in expected.items()):
        raise reg.Conflict('backup_xml_content_changed', 'backup')
    rows = archive['tasks']
    return {'schemaVersion': 1, 'operation': 'backup-check', 'ok': True, 'read_only': True,
            'status': 'COMPLETED', 'scope_count': len(backed), 'checked_count': len(rows),
            'present_count': len(expected), 'absent_count': sum(r['status'] == 'ABSENT' for r in rows),
            'disabled_count': sum(r['status'] == 'COMPLETED' and r['enabled'] is False for r in rows),
            'excluded_count': len(archive['excluded']), 'receipt_revision': archive['receipt_revision'],
            'current_receipt_revision': current['receipt_revision']}


def check_backup(runtime, directory):
    """Read an existing backup and acquire the owner's fresh locked export.

    No publication, recovery, registration, cache, or destination creation occurs.
    Filesystem safety and JSON decoding use the existing storage boundary.
    """
    from fleet_guards import filesystem as fs
    from .runtime_storage import LIMIT, decode

    if not Path(directory).is_absolute():
        raise reg.Conflict('absolute_backup_path_required', 'backup')
    root = fs.validate_path(Path(directory))
    def contents():
        return {p.name: fs.read_bounded(fs.validate_path(p, root=root), LIMIT) for p in root.iterdir()}
    try:
        files = contents()
        if 'receipt.json' not in files:
            raise reg.Conflict('backup_receipt_missing', 'backup')
        receipt_bytes = files.pop('receipt.json')
        archive = decode(receipt_bytes)
        validate_backup_archive(archive)
        current = export_tasks(runtime)
        result = validate_backup(archive, current, files)
        # Detect replacement while the live observation was being acquired.
        if contents() != {**files, 'receipt.json': receipt_bytes}:
            raise reg.Conflict('backup_changed_during_check', 'backup')
        return result
    except reg.ContractError:
        raise
    except (OSError, ValueError):
        raise reg.Conflict('backup_unreadable', 'backup') from None


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


def _power(snapshot, *, scheduler=None, name=None, principal=None):
    from .runtime_xml import NS, parse

    def fields(value):
        settings = _xml_group(parse(value['xml']), 'Settings', 'power')
        # Repeated settings are ambiguous even when their values happen to agree.
        tags = [item.tag for item in settings if isinstance(item.tag, str)]
        if len(tags) != len(set(tags)):
            raise reg.Conflict('power_requires_explicit_adapter', 'power')
        result = {}
        for key, tag in (('disallow_start_on_batteries', 'DisallowStartIfOnBatteries'),
                         ('stop_on_batteries', 'StopIfGoingOnBatteries'), ('wake_to_run', 'WakeToRun')):
            nodes = settings.findall('{' + NS + '}' + tag)
            if not nodes:
                result[key] = None
                continue
            node = nodes[0]
            value = (node.text or '').strip(' \t\r\n')
            if node.attrib or len(node) or value not in ('true', 'false', '1', '0'):
                raise reg.Conflict('power_requires_explicit_adapter', 'power')
            result[key] = value in ('true', '1')
        return result

    power = fields(snapshot['value'])
    if None in power.values():
        if scheduler is None:
            raise reg.Conflict('power_requires_explicit_adapter', 'power')
        prepared = _binding_preparation(snapshot, scheduler, name)
        prepared_principal = _principal_fields(prepared['value'], prepared=True)
        if principal is not None and prepared_principal != principal:
            raise reg.Conflict('principal_requires_explicit_adapter', 'principal')
        normalized = fields(prepared['value'])
        # Require explicit prepared XML values, never infer false from absence.
        if (None in normalized.values()
                or any(value is not None and normalized[key] != value for key, value in power.items())):
            raise reg.Conflict('power_requires_explicit_adapter', 'power')
        return normalized
    return power


def normalize_legacy_binding(binding, snapshot, *, scheduler=None):
    """Return one private binding with explicit principal/power from a snapshot.

    This verifies definition identity and XML-only field provenance, not freshness
    or registration authority. The caller supplies the verified current snapshot
    and retains it as the raw CAS before-image. Only an injected scheduler's
    unregistered prepare operation may resolve omitted defaults. All other fields,
    including the complete XML passthrough, remain unchanged. No runtime is loaded.
    """
    from .runtime_xml import NS, canonical, observation, parse
    import xml.etree.ElementTree as ET

    snapshot = reg._snapshot(snapshot)
    if snapshot['state'] != 'present' or not isinstance(binding, dict):
        raise reg.Conflict('present_legacy_binding_required', 'binding')
    name = task_name(binding.get('name'))
    value = validate_value(snapshot['value'])
    if observation(name, value)['identity'] != snapshot['identity']:
        raise reg.Conflict('snapshot_definition_changed', 'scheduler')
    passthrough = binding.get('xml_passthrough')
    if (not isinstance(passthrough, dict) or not isinstance(passthrough.get('xml'), str)
            or canonical(passthrough['xml']) != canonical(value['xml'])):
        raise reg.Conflict('legacy_xml_changed', 'xml_passthrough')
    if type(binding.get('enabled')) is not bool or binding['enabled'] != value['enabled']:
        raise reg.Conflict('enabled_readback_mismatch', 'scheduler')
    root = parse(value['xml'])
    for field, tag in (('principal', 'Principals'), ('power', 'Settings')):
        group = _xml_group(root, tag, field)
        supplied = binding.get(field)
        if not isinstance(supplied, dict) or set(supplied) != {'xml'} or not isinstance(supplied['xml'], str):
            raise reg.Conflict('xml_only_binding_required', field)
        def wrapped(fragment):
            return '<Task xmlns="' + NS + '">' + fragment + '</Task>'
        if canonical(wrapped(supplied['xml'])) != canonical(wrapped(ET.tostring(group, encoding='unicode'))):
            raise reg.Conflict('legacy_field_xml_changed', field)
    result = deepcopy(binding)
    result['principal'] = _principal(snapshot, scheduler=scheduler, name=name)
    if result['principal']['logon_type'] in ('Password', 'InteractiveTokenOrPassword'):
        raise reg.Conflict('auth_pending', 'credential_ref')
    result['power'] = _power(snapshot, scheduler=scheduler, name=name, principal=result['principal'])
    return result


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
