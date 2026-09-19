"""Synthetic Scheduler storage with the real in-memory COM parser.

The publication serializer models the recorded native representation changes;
it is not a substitute for the separately scoped real registration canary.
"""
from copy import deepcopy
import xml.etree.ElementTree as ET

from t12_runtime_support import fs, encode, read_json, observation, reg, NS
from task_console.runtime_windows import COMTransport
from task_console.runtime_xml import parse, node


class NativeShapeScheduler:
    concrete = True
    native_normalization = True

    def __init__(self, root):
        self.path = root / 'synthetic-scheduler.json'
        self.cache = {}

    def __call__(self, operation, request):
        assert request['TaskPath'] == '\\'
        if operation == 'cleanup':
            return {'ok': True}
        name = request['TaskName']
        tasks = read_json(self.path)
        if operation == 'query':
            saved = tasks.get(name, {'state': 'absent'})
            # Native query supplies XML/flags, never a separate JSON spec whose
            # serialization order could rewrite the embedded Data payload.
            snapshot = saved if saved['state'] == 'absent' else observation(name, {
                k: saved['value'][k] for k in ('xml', 'enabled', 'running')})
            return {'ok': True, 'snapshot': snapshot}
        if operation == 'prepare':
            key = repr(request['value'])
            if key not in self.cache:
                self.cache[key] = COMTransport()(operation, request)
            return deepcopy(self.cache[key])
        assert operation == 'publish'
        current = tasks.get(name, {'state': 'absent'})
        # Raw CAS is deliberately stricter than candidate matching.
        if current != request['expected']:
            expected = deepcopy(request['expected'])
            expected.pop('candidate_identity', None)
            if current != expected:
                raise reg.Conflict('scheduler_changed', 'scheduler')
        desired = request['desired']
        if desired['state'] == 'absent':
            tasks.pop(name, None)
        else:
            value = deepcopy(desired['value'])
            root = parse(value['xml'])
            settings = node(root, 'Settings')
            # Proven root-trace serializer behavior, on synthetic test XML only.
            defaults = {'AllowHardTerminate':'true', 'StartWhenAvailable':'false',
                        'RunOnlyIfNetworkAvailable':'false', 'AllowStartOnDemand':'true',
                        'Hidden':'false', 'RunOnlyIfIdle':'false', 'WakeToRun':'false',
                        'DisallowStartOnRemoteAppSession':'false', 'Priority':'7'}
            for item in list(settings):
                local = item.tag.removeprefix('{'+NS+'}')
                if local in defaults and defaults[local] == item.text:
                    settings.remove(item)
                if item.tag == '{'+NS+'}ExecutionTimeLimit' and item.text == 'PT60S':
                    item.text = 'PT1M'
            principal = root.find('./{'+NS+'}Principals/{'+NS+'}Principal')
            run_level = principal.find('{'+NS+'}RunLevel')
            if run_level is not None and run_level.text == 'LeastPrivilege':
                principal.remove(run_level)
            node(root, 'Actions').set('Context', principal.get('id'))
            triggers = node(root, 'Triggers'); root.remove(triggers); root.insert(len(root)-1,triggers)
            value['xml'] = ET.tostring(root,encoding='unicode')
            tasks[name] = observation(name,value)
        fs.atomic_replace(self.path,encode(tasks))
        return {'ok': True}
