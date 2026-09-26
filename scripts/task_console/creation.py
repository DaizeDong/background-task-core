"""Read-only action reuse checks shared by skill callers and registration."""
from __future__ import annotations

from .contracts import ContractError


def _live_spec(value):
    """Legacy Scheduler observations carry argv only; recover scope from their XML."""
    spec = value.get('spec')
    if not isinstance(spec, dict):
        raise ContractError('creation_action_unknown', 'scheduler')
    if spec.get('principal'):
        return spec
    from .runtime_xml import NS, parse
    root = parse(value['xml'])
    ns = {'t': NS}
    principals = root.findall('t:Principals/t:Principal', ns)
    actions = root.findall('t:Actions/t:Exec', ns)
    if len(principals) != 1 or len(actions) != 1:
        raise ContractError('creation_action_unknown', 'scheduler')
    principal = {field: principals[0].findtext('t:' + tag, namespaces=ns)
                 for field, tag in (('user_id', 'UserId'), ('logon_type', 'LogonType'),
                                    ('run_level', 'RunLevel'))}
    if principals[0].find('t:RunLevel', ns) is None:
        principal['run_level'] = 'LeastPrivilege'  # Task Scheduler schema default
    if not all(principal.values()):
        raise ContractError('creation_action_unknown', 'principal')
    return {**spec, 'principal': principal,
            'cwd': actions[0].findtext('t:WorkingDirectory', namespaces=ns)}


def _action(spec):
    argv, principal = spec.get('argv'), spec.get('principal')
    if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
        raise ContractError('creation_action_unknown', 'argv')
    if not isinstance(principal, dict) or not principal:
        raise ContractError('creation_action_unknown', 'principal')
    cwd = spec.get('cwd')
    if cwd is not None and not isinstance(cwd, str):
        raise ContractError('creation_action_unknown', 'cwd')
    path = lambda value: value.replace('\\', '/').rstrip('/').casefold()
    return {'argv': [path(argv[0]), *argv[1:]], 'cwd': path(cwd or ''), 'principal': principal}


def inspect(runtime, bundle, specs, task_id):
    from . import registration as reg
    if task_id not in specs:
        raise reg.Conflict('invalid_task_selection', 'task_id')
    proposed = specs[task_id]
    target = _action(proposed)
    matches, observed = [], {}
    current = None
    for other_id, spec in specs.items():
        snapshot = reg._read(runtime.scheduler, spec['name'])
        observed[spec['name']] = reg.fingerprint(reg._scheduler_config(snapshot))
        if other_id == task_id:
            current = snapshot
            continue
        existing = snapshot.get('value')
        if existing is not None:
            equivalent = _action(_live_spec(existing)) == target
        else:
            equivalent = _action(spec) == target
        if equivalent:
            matches.append({'task_id': other_id, 'name': spec['name'],
                            'state': snapshot['state'], 'enabled': existing.get('enabled') if existing else False,
                            'running': existing.get('running') if existing else False})
    return {'schemaVersion': 1, 'task_id': task_id,
            'decision': 'reuse' if matches else 'update' if current['state'] == 'present' else 'create',
            'matches': matches, 'checked_tasks': len(specs), 'coverage': 'declared_tasks',
            'inventory_revision': reg.revision(observed), 'input_revision': reg.revision(bundle)}
