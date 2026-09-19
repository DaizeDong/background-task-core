"""Completion authority and native restore boundaries, with synthetic seams."""
from contextlib import contextmanager
from copy import deepcopy
import importlib
import os
import socket
import sqlite3
import subprocess
import sys
import types
import xml.etree.ElementTree as ET

import pytest


@pytest.fixture
def support(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('External state is forbidden in T12 final repairs')

    for key in list(os.environ):
        if key.startswith(('TASK_CONSOLE', 'TASKCONSOLE')):
            monkeypatch.delenv(key)
    for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP'):
        monkeypatch.setenv(key, str(tmp_path))
    for key, value in {'RUNTIME_CONFIG': 'private/config.json', 'PRIVATE_ROOT': 'private',
                       'STATE_ROOT': 'state', 'VAULT_ROOT': 'vault'}.items():
        monkeypatch.setenv('TASK_CONSOLE_' + key, str(tmp_path / value))
    native = types.ModuleType('task_console.runtime_windows')
    native.COMTransport = native.DPAPIProtector = native.powershell = forbidden
    native.split_arguments = forbidden
    monkeypatch.setitem(sys.modules, native.__name__, native)
    monkeypatch.setitem(sys.modules, 'scripts.task_console.runtime_windows', native)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(os, 'system', forbidden)
    llm = types.ModuleType('llmcall')
    llm.__path__ = []
    llm.call = forbidden
    process = types.ModuleType('llmcall.process')

    @contextmanager
    def execution_scope(**kwargs):
        yield types.SimpleNamespace(is_set=lambda: False)

    process.execution_scope = execution_scope
    monkeypatch.setitem(sys.modules, 'llmcall', llm)
    monkeypatch.setitem(sys.modules, 'llmcall.process', process)
    for name in ('notifications', 'notification', 'feishu_notify'):
        module = types.ModuleType(name)
        module.send = module.notify = forbidden
        monkeypatch.setitem(sys.modules, name, module)
    # All external seams and explicit roots are bound before these imports.
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for path in (root, root / 'scripts', root.parent / 'fleet-guards'):
        monkeypatch.syspath_prepend(str(path))
    return importlib.import_module('t12_runtime_support')


@pytest.mark.parametrize('edit', ['content', 'identity', 'deletion'])
def test_completion_refuses_foreign_file_refresh(support, tmp_path, monkeypatch, edit):
    s = support
    runtime, task_id = s.setup(tmp_path)
    target = runtime.load()['paths']['tombstones']
    initial = deepcopy(runtime.load())
    original_finish = runtime.finish
    changed = False

    def finish(record, status):
        nonlocal changed
        original_read = runtime.files.read
        reads = 0

        def read(path):
            nonlocal reads, changed
            if path == target:
                reads += 1
                if reads == 2 and not changed:
                    changed = True
                    if edit == 'deletion':
                        from pathlib import Path
                        Path(path).unlink()
                    else:
                        data = b'{"synthetic_foreign": true}' if edit == 'content' else original_read(path)['value']
                        s.fs.atomic_replace(path, data)
            return original_read(path)

        with monkeypatch.context() as patch:
            patch.setattr(runtime.files, 'read', read)
            return original_finish(record, status)

    monkeypatch.setattr(runtime, 'finish', finish)
    plan = s.reg.build_plan(runtime, [task_id], migrate=True)
    result = s.reg.apply(plan, plan['input_revision'], runtime=runtime)
    assert changed
    assert not result['ok'], 'Completion must not adopt a foreign file proof'
    assert runtime.load()['authority_generation'] == initial['authority_generation']
    foreign = runtime.files.read(target)
    assert s.reg.fingerprint(foreign) != initial['file_ownership'][target]
    record = runtime.journal.load(result['transaction_id'])
    assert 'completion' not in record
    if edit == 'content':
        assert foreign['value'] == b'{"synthetic_foreign": true}'
    elif edit == 'deletion':
        assert foreign == {'state': 'absent'}


def omit_runlevel(s, runtime, task_id, root):
    tasks = s.read_json(root / 'synthetic-scheduler.json')
    value = deepcopy(tasks['AcmeSync']['value'])
    tree = s.parse(value['xml'])
    principal = tree.find('./{' + s.NS + '}Principals/{' + s.NS + '}Principal')
    principal.remove(principal.find('{' + s.NS + '}RunLevel'))
    value['xml'] = ET.tostring(tree, encoding='unicode')
    tasks['AcmeSync'] = s.observation('AcmeSync', value)
    s.fs.atomic_replace(root / 'synthetic-scheduler.json', s.encode(tasks))
    receipt = s.read_json(root / 'private/adoption.json')
    receipt['ownership'][task_id]['identity'] = tasks['AcmeSync']['identity']
    s.fs.atomic_replace(root / 'private/adoption.json', s.encode(receipt))
    return value['xml']


def native_defaults(s, root):
    class NativeDefaults(s.SyntheticScheduler):
        native_normalization = True

        def __call__(self, operation, request):
            if operation == 'prepare' and request['value'] is not None:
                self.calls.append((operation, deepcopy(request)))
                value = s.normalize(request['value'])
                tree = s.parse(value['xml'])
                principal = tree.find('./{' + s.NS + '}Principals/{' + s.NS + '}Principal')
                if principal.find('{' + s.NS + '}RunLevel') is None:
                    s.text(principal, 'RunLevel', 'LeastPrivilege')
                value['xml'] = ET.tostring(tree, encoding='unicode')
                return {'ok': True, 'snapshot': s.observation(request['TaskName'], value)}
            return super().__call__(operation, request)

    return NativeDefaults(root)


@pytest.mark.parametrize('native', [False, True])
def test_omitted_default_restores_and_keeps_raw_archive(support, tmp_path, native):
    s = support
    from task_console import export_restore as owner
    runtime, task_id = s.setup(tmp_path)
    raw = omit_runlevel(s, runtime, task_id, tmp_path)
    if native:
        runtime.scheduler.transport = native_defaults(s, tmp_path)
    archive = owner.export_tasks(runtime)
    before = deepcopy(archive)
    plan = owner.restore_plan(runtime, archive, [task_id])
    result = owner.restore(runtime, plan, plan['plan_revision'])
    assert result['tasks_registered'] and result['tasks'][0]['enabled'] is False
    assert archive == before
    assert archive['tasks'][0]['snapshot']['value']['xml'] == raw
    record = runtime.journal.load(result['transaction']['transaction_id'])
    assert runtime.vault.get(record['scheduler_before']['AcmeSync'])['value']['xml'] == raw
    assert runtime.load()['ownership'][task_id]['identity'] == runtime.scheduler.read('AcmeSync')['identity']


@pytest.mark.parametrize('change', ['UserId', 'LogonType', 'RunLevel', 'GroupId', 'Duplicate', 'Unknown'])
def test_missing_default_never_repairs_unsupported_principal(support, tmp_path, change):
    s = support
    from task_console import export_restore as owner
    runtime, task_id = s.setup(tmp_path)
    omit_runlevel(s, runtime, task_id, tmp_path)
    snapshot = runtime.scheduler.read('AcmeSync')
    tree = s.parse(snapshot['value']['xml'])
    principal = tree.find('./{' + s.NS + '}Principals/{' + s.NS + '}Principal')
    if change in ('UserId', 'LogonType'):
        principal.remove(principal.find('{' + s.NS + '}' + change))
    elif change == 'Duplicate':
        principal.append(deepcopy(principal.find('{' + s.NS + '}UserId')))
    else:
        s.text(principal, change, 'unsupported-synthetic-value')
    snapshot['value']['xml'] = ET.tostring(tree, encoding='unicode')
    calls = len(runtime.scheduler.transport.calls)
    with pytest.raises(s.reg.Conflict, match='principal_requires_explicit_adapter'):
        owner._principal(snapshot, scheduler=runtime.scheduler, name='AcmeSync')
    assert len(runtime.scheduler.transport.calls) == calls


@pytest.mark.parametrize('field,value', [('RunLevel', 'HighestAvailable'), ('UserId', 'SyntheticOtherUser'),
                                       ('LogonType', 'S4U')])
def test_semantic_principal_difference_still_requires_review(support, tmp_path, field, value):
    s = support
    from task_console import export_restore as owner
    runtime, task_id = s.setup(tmp_path)
    omit_runlevel(s, runtime, task_id, tmp_path)
    archive = owner.export_tasks(runtime)
    current = runtime.scheduler.read('AcmeSync')
    tree = s.parse(current['value']['xml'])
    principal = tree.find('./{' + s.NS + '}Principals/{' + s.NS + '}Principal')
    s.text(principal, field, value)
    current['value']['xml'] = ET.tostring(tree, encoding='unicode')
    bundle = runtime.load()
    spec = s.reg._compile(bundle)['task_specs'][0]
    intent = {'restore_tasks': {task_id: archive['tasks'][0]}, 'rebinding': {}}
    with pytest.raises(s.reg.Conflict, match='reviewed_rebinding_required'):
        owner.validate_restore_target(bundle, intent, spec, current, scheduler=runtime.scheduler)
    assert runtime.journal.list() == []


def test_default_requires_preparation_and_rejects_preparer_semantic_change(support, tmp_path, monkeypatch):
    s = support
    from task_console import export_restore as owner
    runtime, task_id = s.setup(tmp_path)
    omit_runlevel(s, runtime, task_id, tmp_path)
    snapshot = runtime.scheduler.read('AcmeSync')
    with pytest.raises(s.reg.Conflict, match='principal_requires_explicit_adapter'):
        owner._principal(snapshot)
    prepare = runtime.scheduler.transport

    def changed(operation, request):
        reply = prepare(operation, request)
        if operation == 'prepare':
            value = reply['snapshot']['value']
            tree = s.parse(value['xml'])
            tree.find('./{' + s.NS + '}Principals/{' + s.NS + '}Principal/{' + s.NS + '}UserId').text = 'SyntheticForeignUser'
            value['xml'] = ET.tostring(tree, encoding='unicode')
        return reply

    changed.concrete = True
    monkeypatch.setattr(runtime.scheduler, 'transport', changed)
    with pytest.raises(s.reg.Conflict, match='preparation_changed_settings'):
        owner._principal(snapshot, scheduler=runtime.scheduler, name='AcmeSync')


def test_omitted_default_absence_rebinding_is_exact(support, tmp_path):
    s = support
    from task_console import export_restore as owner
    source, task_id = s.setup(tmp_path / 'source')
    omit_runlevel(s, source, task_id, tmp_path / 'source')
    archive = owner.export_tasks(source)
    target, _ = s.setup(tmp_path / 'target', absent=True)
    with pytest.raises(s.reg.Conflict, match='reviewed_rebinding_required'):
        owner.restore_plan(target, archive, [task_id])
    row = archive['tasks'][0]
    evidence = {task_id: {'task_id': task_id, 'source_revision': row['snapshot_revision'],
        'target_revision': s.reg.fingerprint({'state': 'absent'}),
        'target_principal': row['snapshot']['value']['spec']['principal'],
        'generation': target.load()['authority_generation'], 'approved': True, 'reason': 'Synthetic review'}}
    stale = deepcopy(evidence)
    stale[task_id]['source_revision'] = 'sha256:' + '0' * 64
    with pytest.raises(s.reg.Conflict, match='reviewed_rebinding_required'):
        owner.restore_plan(target, archive, [task_id], rebinding=stale)
    plan = owner.restore_plan(target, archive, [task_id], rebinding=evidence)
    assert owner.restore(target, plan, plan['plan_revision'])['tasks_registered']
