"""Synthetic child-process driver. Never creates a real Scheduler task or cipher."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT.parent / 'fleet-guards'))
from tools.make_fixtures import example_request
from task_console import registration as reg
from task_console.runtime import RuntimeConfig, create_runtime
from task_console.runtime_storage import encode, read_json, digest
from task_console.runtime_xml import NS, render, normalize, observation, parse, node, text
from fleet_guards import filesystem as fs


class FakeProtector:
    """A visible test envelope, deliberately not encryption or a production option."""
    def protect(self, data):
        import base64
        return b'SYNTHETIC-ONLY:' + base64.b64encode(data)

    def unprotect(self, data):
        import base64
        if not data.startswith(b'SYNTHETIC-ONLY:'):
            raise ValueError('synthetic boundary')
        return base64.b64decode(data.split(b':', 1)[1], validate=True)


class SyntheticScheduler:
    concrete = True

    def __init__(self, root):
        self.path = Path(root) / 'synthetic-scheduler.json'
        self.calls = []

    def __call__(self, operation, request):
        assert request['TaskPath'] == '\\'
        self.calls.append((operation, deepcopy(request)))
        if operation == 'cleanup':
            return {'ok': True}
        name = request['TaskName']
        tasks = read_json(self.path)
        if operation == 'query':
            return {'ok': True, 'snapshot': tasks.get(name, {'state': 'absent'})}
        if operation == 'prepare':
            value = request['value']
            return {'ok': True, 'snapshot': {'state': 'absent'} if value is None else observation(name, normalize(value))}
        if operation == 'publish':
            current = tasks.get(name, {'state': 'absent'})
            reg._idle(current)
            if not reg._same('scheduler', current, request['expected']):
                raise reg.Conflict('scheduler_changed', 'scheduler')
            if request['desired']['state'] == 'absent':
                tasks.pop(name, None)
            else:
                tasks[name] = request['desired']
            fs.atomic_replace(self.path, encode(tasks))
            return {'ok': True}
        raise AssertionError(operation)


def configured(root, checkpoint=lambda point: None):
    root = Path(root)
    config = RuntimeConfig.read(
        root / 'private/config.json', private_root=root / 'private', state_root=root / 'state', vault_root=root / 'vault')
    return create_runtime(config, transport=SyntheticScheduler(root), protector=FakeProtector(), checkpoint=checkpoint)


def setup(root, *, absent=False, enabled=False, name='AcmeSync'):
    root = Path(root)
    request = example_request()
    task_id = next(iter(request['bindings']['tasks']))
    binding = request['bindings']['tasks'][task_id]
    binding['name'], binding['enabled'] = name, enabled
    request['baseline']['tasks'][0]['name'] = name
    paths = {'bindings': 'bindings.json', 'machine': 'machine.json', 'task-health.json': 'health.json',
             'TaskNames.ps1': 'names.ps1', 'categories.json': 'categories.json', 'tombstones': 'tombstones.json'}
    for role, value in [('bindings', request['bindings']), ('machine', request['machine']),
                        ('task-health.json', request['baseline']['task_health']),
                        ('TaskNames.ps1', request['baseline']['task_names']),
                        ('categories.json', request['baseline']['categories']), ('tombstones', {})]:
        fs.atomic_replace(root / 'private' / paths[role], value.encode() if isinstance(value, str) else encode(value))
    fs.atomic_replace(root / 'private/desired.json', encode(request))
    config = {'schemaVersion': 1, 'domain': '1' * 32, 'desired': 'desired.json', 'adoption': 'adoption.json',
              'paths': paths, 'active_xml': {task_id: None}, 'launchers': {task_id: 'hidden.vbs'}}
    fs.atomic_replace(root / 'private/config.json', encode(config))
    spec = reg._compile({'request': request})['task_specs'][0]
    value = render(spec, {'state': 'absent'}, False)
    tree = parse(value['xml'])
    settings = node(tree, 'Settings')
    text(settings, 'Priority', 7)
    text(settings, 'StartWhenAvailable', True)
    text(node(settings, 'IdleSettings'), 'StopOnIdleEnd', False)
    value['xml'] = ET.tostring(tree, encoding='unicode')
    tasks = {} if absent else {name: observation(name, value)}
    fs.atomic_replace(root / 'synthetic-scheduler.json', encode(tasks))
    runtime = configured(root)
    receipt = {'schemaVersion': 1, 'domain': config['domain'], 'generation': '2' * 32,
               'authority': {'authority_epoch': 0, 'migrated_tasks': []},
               'ownership': {task_id: {'name': name, 'task_path': '\\', 'epoch': 0, 'writer': 'legacy',
                                       'identity': tasks.get(name, {}).get('identity')}},
               'file_ownership': {p: reg.fingerprint(runtime.files.read(p)) for p in runtime.files.paths}}
    fs.atomic_replace(root / 'private/adoption.json', encode(receipt))
    return runtime, task_id


def run(root, command, point='', occurrence=1):
    seen = 0
    def checkpoint(value):
        nonlocal seen
        if value == point:
            seen += 1
            if seen == occurrence:
                os._exit(73)
    runtime = configured(root, checkpoint)
    task_id = next(iter(runtime.load()['ownership'])) if command not in ('recover',) else None
    if point in ('file_detached', 'file_linked'):
        link = fs.link_no_replace
        def crash_link(source, destination):
            if point == 'file_detached':
                os._exit(73)
            result = link(source, destination)
            os._exit(73)
            return result
        fs.link_no_replace = crash_link
    if command == 'apply':
        plan = reg.build_plan(runtime, [task_id], migrate=True, approve_enable=True)
        result = reg.apply(plan, plan['input_revision'], runtime=runtime)
    elif command in ('enable', 'disable'):
        result = reg.control(runtime.load()['ownership'][task_id]['name'], command, runtime=runtime)
    elif command == 'recover':
        record = runtime.journal.list()[0]
        result = reg.recover(record['transaction_id'], runtime=runtime)
    elif command == 'retire':
        plan = reg.build_plan(runtime, [task_id], operation='retire', reason='synthetic retirement')
        result = reg.apply(plan, plan['input_revision'], runtime=runtime)
    elif command == 'status':
        result = {'pending': runtime.journal.pending(['authority']), 'generation': runtime.load()['authority_generation']}
    elif command == 'dispatch':
        result = reg.dispatch(runtime.load()['ownership'][task_id]['name'], 'disable', runtime=runtime,
                              legacy=lambda: {'legacy': True})
    elif command == 'cli':
        from task_console.__main__ import main
        return main(['registration-plan', '--task-id', task_id, '--migrate'], runtime=runtime)
    elif command == 'cliapply':
        from task_console.__main__ import main
        plan = reg.build_plan(runtime, [task_id], migrate=True)
        path = Path(root) / 'synthetic-plan.json'
        fs.atomic_replace(path, encode(plan))
        return main(['apply', '--request', str(path), '--expected-revision', plan['input_revision']], runtime=runtime)
    else:
        raise AssertionError(command)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(run(Path(sys.argv[1]), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '',
                             int(sys.argv[4]) if len(sys.argv) > 4 else 1))
    except reg.Conflict as exc:
        print(json.dumps({'error': exc.code}))
        raise SystemExit(2)
