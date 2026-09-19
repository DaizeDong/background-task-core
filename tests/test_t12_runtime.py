"""Real filesystem/locks/receipts with injected Scheduler mutations. No live tasks."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from t12_runtime_support import (setup, configured, reg, fs, encode, read_json, parse, NS,
                                 observation, node, text, FakeProtector)
from task_console.runtime_storage import ResourceLocks, ProtectedVault, JournalStore
from task_console.runtime_xml import launcher_bytes, _split_command, render, canonical

DRIVER = Path(__file__).with_name('t12_runtime_support.py')


def child(root, command, point='', occurrence=1):
    return subprocess.run([sys.executable, str(DRIVER), str(root), command, point, str(occurrence)],
                          capture_output=True, text=True, timeout=30)


def successful(reply):
    assert reply.returncode == 0, reply.stdout + reply.stderr
    return json.loads(reply.stdout)


def test_apply_fresh_process_receipt_launcher_retire(tmp_path):
    runtime, task_id = setup(tmp_path)
    desired = (tmp_path / 'private/desired.json').read_bytes()
    result = successful(child(tmp_path, 'apply'))
    assert result['status'] == 'committed', result
    assert result['cleaned']
    fresh = configured(tmp_path)
    assert fresh.load()['request']['machine']['migrated_tasks'] == [task_id]
    assert fresh.load()['authority_generation'] == result['transaction_id']
    assert (tmp_path / 'private/desired.json').read_bytes() == desired
    launcher = (tmp_path / 'private/hidden.vbs').read_bytes()
    assert launcher.startswith(b'\xff\xfe')
    assert 'WScript.Quit shell.Run(' in launcher[2:].decode('utf-16-le')
    assert ', 0, True)' in launcher[2:].decode('utf-16-le')
    value = fresh.scheduler.read('AcmeSync')['value']
    assert parse(value['xml']).findtext('./{' + NS + '}Settings/{' + NS + '}Priority') == '7'
    assert value['enabled'] is value['spec']['enabled'] is False
    result = successful(child(tmp_path, 'retire'))
    assert result['status'] == 'committed', result
    assert not (tmp_path / 'private/hidden.vbs').exists()
    assert configured(tmp_path).scheduler.read('AcmeSync') == {'state': 'absent'}


@pytest.mark.parametrize('point', ['journal_created', 'staged', 'intent', 'published', 'receipt_intent',
                                  'receipt_written', 'pointer_published', 'committed', 'cleanup',
                                  'file_detached', 'file_linked'])
def test_actual_storage_process_death_recovery(tmp_path, point):
    setup(tmp_path)
    reply = child(tmp_path, 'apply', point)
    assert reply.returncode == 73, reply.stdout + reply.stderr
    blocked = child(tmp_path, 'dispatch')
    assert blocked.returncode == 2 and 'recovery_required' in blocked.stdout
    result = successful(child(tmp_path, 'recover'))
    assert result['status'] in ('rolled_back', 'committed'), result
    assert result['cleaned'], result
    assert successful(child(tmp_path, 'recover')) == result
    assert configured(tmp_path).journal.pending(['authority']) == []


def test_cross_process_lock_is_busy_and_crash_releases(tmp_path):
    setup(tmp_path)
    runtime = configured(tmp_path)
    with runtime.locks.hold(['authority']):
        reply = child(tmp_path, 'apply')
        assert reply.returncode == 2 and 'busy' in reply.stdout
    assert successful(child(tmp_path, 'apply'))['status'] == 'committed'


def test_busy_recovery_preserves_support_and_retries(tmp_path):
    setup(tmp_path, enabled=True)
    assert child(tmp_path, 'apply', 'published', 3).returncode == 73
    scheduler = tmp_path / 'synthetic-scheduler.json'
    tasks = read_json(scheduler)
    tasks['AcmeSync']['value']['running'] = True
    fs.atomic_replace(scheduler, encode(tasks))
    before = {p: p.read_bytes() for p in (tmp_path / 'private').iterdir() if p.is_file()}
    result = successful(child(tmp_path, 'recover'))
    assert result['status'] == 'conflict'
    assert all(p.read_bytes() == value for p, value in before.items())
    tasks['AcmeSync']['value']['running'] = False
    fs.atomic_replace(scheduler, encode(tasks))
    assert successful(child(tmp_path, 'recover'))['status'] == 'rolled_back'


def test_lost_protected_reference_fails_closed(tmp_path):
    runtime, _ = setup(tmp_path)
    assert child(tmp_path, 'apply', 'published').returncode == 73
    record = runtime.journal.list()[0]
    ref = record['steps'][0]['before']
    parts = ref.split(':')
    path = tmp_path / 'vault' / ('task-console-' + parts[1] + '-' + parts[2] + '.cred')
    path.unlink()
    result = successful(child(tmp_path, 'recover'))
    assert result['status'] == 'conflict'
    assert runtime.journal.pending(['authority'])
    assert '<Task' not in ''.join(p.read_text() for p in (tmp_path / 'state/journals').iterdir())


def test_unmanaged_launcher_conflicts(tmp_path):
    setup(tmp_path)
    (tmp_path / 'private/hidden.vbs').write_bytes(b'synthetic unmanaged launcher')
    reply = child(tmp_path, 'apply')
    assert reply.returncode == 2 and 'file_ownership_changed' in reply.stdout
    assert (tmp_path / 'private/hidden.vbs').read_bytes() == b'synthetic unmanaged launcher'


def test_absent_task_and_literal_same_root_name(tmp_path):
    name = "Acme '$literal; task"
    setup(tmp_path, absent=True, name=name)
    result = successful(child(tmp_path, 'apply'))
    assert result['status'] == 'committed', result
    tasks = read_json(tmp_path / 'synthetic-scheduler.json')
    assert list(tasks) == [name]
    runtime = configured(tmp_path)
    runtime.scheduler.read(name)
    assert runtime.scheduler.transport.calls[-1][1]['TaskName'] == name
    with pytest.raises(reg.Conflict, match='invalid_task_name'):
        runtime.scheduler.read('Folder\\' + name)


def test_current_authority_loss_and_unknown_owner_refuse(tmp_path):
    setup(tmp_path)
    successful(child(tmp_path, 'apply'))
    (tmp_path / 'state/current.json').unlink()
    reply = child(tmp_path, 'dispatch')
    assert reply.returncode == 2 and 'authority_pointer_lost' in reply.stdout


def test_readonly_config_status_never_provisions(tmp_path):
    setup(tmp_path)
    assert not (tmp_path / 'state').exists()
    successful(child(tmp_path, 'status'))
    successful(child(tmp_path, 'cli'))
    assert not (tmp_path / 'state').exists()
    assert not (tmp_path / 'vault').exists()


def test_secure_reference_domain_and_owner_retention(tmp_path):
    vault = ProtectedVault(tmp_path / 'vault', '1' * 32, FakeProtector())
    image = {'state': 'present', 'identity': 'test', 'value': b'synthetic XML'}
    ref = vault.put('2' * 32 + ':0:before', image)
    assert ProtectedVault(tmp_path / 'vault', '1' * 32, FakeProtector()).get(ref) == image
    with pytest.raises(reg.Conflict, match='secure_reference_unavailable'):
        ProtectedVault(tmp_path / 'vault', '3' * 32, FakeProtector()).get(ref)
    with pytest.raises(reg.Conflict, match='secure_reference_unavailable'):
        vault.get(ref[:-32] + '4' * 32)
    assert len(list((tmp_path / 'vault').iterdir())) == 1


def test_argv_unicode_quotes_and_empty_arguments_roundtrip():
    argv = ['C:/Acme/python.exe', 'synthetic text', '', 'a"b', 'trail\\', '中文', '$literal; &']
    assert _split_command(subprocess.list2cmdline(argv)) == argv
    launcher = launcher_bytes(argv, 'C:/Acme/source')
    assert launcher.startswith(b'\xff\xfe')
    assert '中文' in launcher.decode('utf-16')


def test_password_principal_is_never_substituted(tmp_path):
    runtime, task_id = setup(tmp_path)
    spec = reg._compile(runtime.load())['task_specs'][0]
    spec['principal']['logon_type'] = 'Password'
    with pytest.raises(reg.Conflict, match='auth_pending'):
        runtime.render(spec, runtime.scheduler.read('AcmeSync'), False)


def test_concurrent_file_edit_is_preserved(tmp_path):
    setup(tmp_path)
    assert child(tmp_path, 'apply', 'published', 2).returncode == 73
    path = tmp_path / 'private/bindings.json'
    path.write_bytes(b'{"synthetic":"concurrent edit"}')
    result = successful(child(tmp_path, 'recover'))
    assert result['status'] == 'conflict'
    assert path.read_bytes() == b'{"synthetic":"concurrent edit"}'


def test_cli_mutation_and_explicit_config_status_in_fresh_process(tmp_path):
    setup(tmp_path)
    assert successful(child(tmp_path, 'cliapply'))['status'] == 'committed'
    # Standalone status has no fake-protector option. Rewrap just its synthetic
    # input-generation fixture through the real API, like CONFIG's DPAPI restore.
    # Transaction fault tests retain their cheap fake cipher boundary.
    from task_console.runtime_windows import DPAPIProtector
    from task_console.runtime_storage import digest
    pointer_path = tmp_path / 'state/current.json'
    pointer = read_json(pointer_path)
    receipt_path = tmp_path / 'state/receipts' / (pointer['generation'] + '.json')
    receipt = read_json(receipt_path)
    reference = receipt['input_reference']
    original_vault = configured(tmp_path).vault
    snapshot = original_vault.get(reference)
    native_vault = ProtectedVault(tmp_path / 'vault', original_vault.domain, DPAPIProtector())
    receipt['input_reference'] = native_vault.put(reference.split(':')[-1] + ':native-fixture', snapshot)
    fs.atomic_replace(receipt_path, encode(receipt))
    pointer['receipt_digest'] = digest(encode(receipt))
    fs.atomic_replace(pointer_path, encode(pointer))
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([str(DRIVER.parents[1] / 'scripts'),
                                       str(DRIVER.parents[2] / 'fleet-guards')])
    result = subprocess.run([sys.executable, '-m', 'task_console', 'runtime-status', '--runtime-config',
                            str(tmp_path / 'private/config.json'), '--private-root', str(tmp_path / 'private'),
                            '--state-root', str(tmp_path / 'state'), '--vault-root', str(tmp_path / 'vault')],
                           env=env, capture_output=True, text=True, timeout=20)
    assert successful(result)['pending'] == []


def test_completion_recovery_does_not_require_desired_input(tmp_path):
    setup(tmp_path)
    assert child(tmp_path, 'apply', 'pointer_published').returncode == 73
    (tmp_path / 'private/desired.json').unlink()
    assert successful(child(tmp_path, 'recover'))['status'] == 'committed'


def test_reference_cannot_be_borrowed_from_another_transaction(tmp_path):
    runtime, _ = setup(tmp_path)
    assert child(tmp_path, 'apply', 'published').returncode == 73
    record = runtime.journal.list()[0]
    record['steps'][0]['before'] = record['steps'][0]['before'][:-32] + 'f' * 32
    with pytest.raises(reg.Conflict, match='reference_owner_mismatch'):
        runtime.journal.save(record['transaction_id'], record)


def test_full_xml_passthrough_keeps_timezone_triggers_and_settings(tmp_path):
    runtime, _ = setup(tmp_path)
    spec = reg._compile(runtime.load())['task_specs'][0]
    old = runtime.scheduler.read('AcmeSync')
    tree = parse(old['value']['xml'])
    trigger = tree.find('./{' + NS + '}Triggers/{' + NS + '}CalendarTrigger')
    text(trigger, 'StartBoundary', '2027-01-01T03:15:00-05:00')
    text(trigger, 'RandomDelay', 'PT7M')
    text(node(tree, 'RegistrationInfo'), 'Description', 'Synthetic description')
    import xml.etree.ElementTree as ET
    xml = ET.tostring(tree, encoding='unicode')
    spec['timezone'] = 'America/New_York'
    spec['xml_passthrough'] = {'xml': xml, 'owner': 'synthetic', 'reason': 'preserve DST and delay'}
    rendered = render(spec, old, False)
    after = parse(rendered['xml'])
    assert ET.tostring(after.find('{' + NS + '}Triggers')) == ET.tostring(tree.find('{' + NS + '}Triggers'))
    assert ET.tostring(after.find('{' + NS + '}Settings')) == ET.tostring(tree.find('{' + NS + '}Settings'))
    assert after.findtext('./{' + NS + '}RegistrationInfo/{' + NS + '}Description') == 'Synthetic description'


def test_list_triggers_and_weekday_schema(tmp_path):
    runtime, _ = setup(tmp_path)
    spec = reg._compile(runtime.load())['task_specs'][0]
    spec['trigger'] = [{'type': 'weekly', 'days': ['mon', 'fri'], 'at': '03:15:27'},
                       {'type': 'interval', 'seconds': 120}, {'type': 'logon'}]
    rendered = render(spec, {'state': 'absent'}, False)
    triggers = parse(rendered['xml']).find('{' + NS + '}Triggers')
    assert len(triggers) == 3
    assert triggers[0].findtext('{' + NS + '}StartBoundary').endswith('03:15:27Z')
    assert triggers[0].find('./{' + NS + '}ScheduleByWeek/{' + NS + '}DaysOfWeek/{' + NS + '}Monday') is not None


def test_transport_cannot_change_explicit_settings(tmp_path):
    runtime, _ = setup(tmp_path)
    transport = runtime.scheduler.transport
    def corrupt(operation, request):
        response = transport(operation, request)
        if operation == 'prepare':
            value = response['snapshot']['value']
            value['xml'] = value['xml'].replace('<Priority>7</Priority>', '<Priority>9</Priority>')
        return response
    corrupt.concrete = True
    runtime.scheduler.transport = corrupt
    value = runtime.scheduler.read('AcmeSync')['value']
    with pytest.raises(reg.Conflict, match='preparation_changed_settings'):
        runtime.scheduler.prepare('AcmeSync', value, 'a' * 32 + ':0')


@pytest.mark.parametrize('point', ['intent', 'published'])
def test_activation_crash_recovers_actual_resources(tmp_path, point):
    setup(tmp_path, enabled=True)
    assert child(tmp_path, 'apply', point, 9).returncode == 73
    result = successful(child(tmp_path, 'recover'))
    assert result['status'] == 'rolled_back', result
    assert not (tmp_path / 'private/hidden.vbs').exists()
    assert configured(tmp_path).scheduler.read('AcmeSync')['value']['enabled'] is False


def test_control_refuses_to_publish_unapplied_argv(tmp_path):
    runtime, task_id = setup(tmp_path)
    assert successful(child(tmp_path, 'apply'))['status'] == 'committed'
    request = read_json(tmp_path / 'private/desired.json')
    request['bindings']['tasks'][task_id]['argv'].append('--synthetic-change')
    fs.atomic_replace(tmp_path / 'private/desired.json', encode(request))
    with pytest.raises(reg.Conflict, match='declaration_apply_required'):
        reg.build_plan(configured(tmp_path), [task_id], operation='enable', approve_enable=True)


def test_control_and_dispatch_refuse_unknown_or_lost_owners(tmp_path):
    runtime, task_id = setup(tmp_path)
    with pytest.raises(reg.Conflict, match='ownership_required'):
        reg.control('UnknownSyntheticTask', 'enable', runtime=runtime)
    with pytest.raises(reg.Conflict, match='ownership_required'):
        reg.dispatch('UnknownSyntheticTask', 'enable', runtime=runtime, legacy=lambda: pytest.fail('legacy called'))
    receipt = read_json(tmp_path / 'private/adoption.json')
    receipt['ownership'].pop(task_id)
    fs.atomic_replace(tmp_path / 'private/adoption.json', encode(receipt))
    with pytest.raises(reg.Conflict, match='ownership_required'):
        reg.control('AcmeSync', 'enable', runtime=runtime)
