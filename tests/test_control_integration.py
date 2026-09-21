"""Synthetic control boundaries. No live discovery, Scheduler or payload calls."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts/task_console'))
sys.path.insert(0, str(ROOT.parent / 'fleet-guards'))
sys.path.insert(0, str(ROOT.parent / 'llmcall'))


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(('TASK_CONSOLE_', 'TASKCONSOLE_')):
            monkeypatch.delenv(key)
    home = tmp_path / 'synthetic-home'
    home.mkdir()
    for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP'):
        monkeypatch.setenv(key, str(home))


@pytest.fixture
def http_server(monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    import server
    def collect_only(script, *args, **kwargs):
        assert script == server.COLLECT, 'No legacy shell may run from HTTP'
        return 0, json.dumps({'tasks': [{'name': 'AcmeSync'}]}), ''
    monkeypatch.setattr(server, 'run_ps', collect_only)
    class Handler(server.Handler):
        token = 'synthetic-token'
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = httpd.server_address[1]
    Handler.allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}'}
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield server, port
    httpd.shutdown()
    thread.join(timeout=3)
    httpd.server_close()


def post(port, payload, *, token='synthetic-token', host=None, path='/api/act'):
    import http.client
    # Real filesystem transactions in the synthetic runtime share the production
    # 60-second control budget; a shorter client timeout would hide the response.
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=65)
    connection.request('POST', path, json.dumps(payload), headers={
        'Host': host or f'127.0.0.1:{port}', 'X-Console-Token': token,
        'Content-Type': 'application/json'})
    reply = connection.getresponse()
    result = reply.status, json.loads(reply.read())
    connection.close()
    return result


def test_missing_authority_never_calls_legacy():
    from task_console.controller import load_runtime
    from task_console.contracts import ContractError
    with pytest.raises(ContractError, match='runtime_not_configured'):
        load_runtime(environ={})


def test_partial_authority_never_falls_back():
    from task_console.controller import load_runtime
    from task_console.contracts import ContractError
    with pytest.raises(ContractError, match='incomplete_runtime_configuration'):
        load_runtime(environ={'TASK_CONSOLE_RUNTIME_CONFIG': 'missing.json'})


def test_dispatch_holds_task_and_authority_locks(tmp_path):
    from test_registration_transaction import rig
    from task_console.runtime import dispatch_adapter
    runtime, bundle, ids = rig(tmp_path)
    def legacy():
        assert {'authority', 'task:' + ids[0]} <= runtime.locks.active
        return {'ok': True}
    assert dispatch_adapter(runtime)('AcmeSync', 'stop', legacy=legacy)['ok']


def test_scheduler_run_is_acknowledgement_not_payload_success():
    from task_console.scheduler_windows import WindowsScheduler
    snapshot = {'state': 'present', 'identity': 'synthetic', 'value': {
        'xml': '<Task/>', 'enabled': True, 'running': False,
        'spec': {'argv': ['synthetic-never-executed']}}}
    calls = []
    def transport(operation, request):
        calls.append((operation, request))
        assert request['TaskPath'] == '\\'
        assert request['TaskName'] == "Acme 'quoted'; $literal"
        if operation == 'query':
            return {'ok': True, 'snapshot': deepcopy(snapshot)}
        return {'ok': True, 'acknowledged': True, 'running': False}
    result = WindowsScheduler(transport).control("Acme 'quoted'; $literal", 'run', snapshot)
    assert result['ok'] and result['acknowledged']
    assert result['payload_success'] is None
    assert result['status'] == 'run_requested'
    assert [op for op, _ in calls] == ['query', 'run']


def test_scheduler_stop_uncertain_is_not_terminal():
    from task_console.scheduler_windows import WindowsScheduler
    snapshot = {'state': 'present', 'identity': 'synthetic', 'value': {
        'xml': '<Task/>', 'enabled': True, 'running': True,
        'spec': {'argv': ['synthetic-never-executed']}}}
    def transport(operation, request):
        if operation == 'query':
            return {'ok': True, 'snapshot': deepcopy(snapshot)}
        return {'ok': True, 'acknowledged': True, 'running': True}
    result = WindowsScheduler(transport).control('AcmeSync', 'stop', snapshot)
    assert result['ok'] is False
    assert result['status'] == 'cleanup_uncertain'
    assert result['payload_success'] is None


@pytest.mark.parametrize('migrated', [False, True])
@pytest.mark.parametrize('name', ['AcmeSync', 'Acme "quoted"; $literal'])
def test_http_and_fresh_cli_share_dispatch_and_payload(tmp_path, monkeypatch, http_server, migrated, name):
    import subprocess
    from control_support import fixture_runtime
    runtime, _ = fixture_runtime(tmp_path, migrated=migrated, name=name)
    server, port = http_server
    def collect(script, *args, **kwargs):
        assert script == server.COLLECT
        return 0, json.dumps({'tasks': [{'name': name}]}), ''
    monkeypatch.setattr(server, 'run_ps', collect)
    monkeypatch.setattr(server.task_control, 'load_runtime', lambda: runtime)
    status, result = post(port, {'name': name, 'verb': 'stop'})
    assert status == 200 and result['status'] == 'scheduler_idle'
    child = subprocess.run([sys.executable, str(Path(__file__).with_name('control_support.py')),
                            str(tmp_path), 'control'], input=json.dumps({'name': name, 'verb': 'stop'}),
                           capture_output=True, text=True, timeout=30, env=dict(os.environ))
    assert child.returncode == 0, child.stdout + child.stderr
    assert json.loads(child.stdout) == result
    assert not any(op == 'run' for op, _ in runtime.scheduler.transport.calls)


def test_http_migrated_run_disable_enable_and_stop(tmp_path, monkeypatch, http_server):
    from control_support import fixture_runtime
    runtime, task_id = fixture_runtime(tmp_path, migrated=True, enabled=True)
    server, port = http_server
    monkeypatch.setattr(server.task_control, 'load_runtime', lambda: runtime)
    for verb in ('run', 'disable', 'enable', 'stop'):
        status, result = post(port, {'name': 'AcmeSync', 'verb': verb})
        assert status == 200, result
        if verb == 'run':
            assert result['status'] == 'run_requested' and result['payload_success'] is None
        if verb in ('disable', 'enable'):
            assert runtime.load()['request']['bindings']['tasks'][task_id]['enabled'] is (verb == 'enable')


def test_http_bad_authority_has_complete_safe_error(monkeypatch, http_server):
    server, port = http_server
    monkeypatch.setenv('TASK_CONSOLE_RUNTIME_CONFIG', 'missing')
    status, result = post(port, {'name': 'AcmeSync', 'verb': 'stop'})
    assert status == 500 and result['ok'] is False
    assert result['error']['code'] == 'incomplete_runtime_configuration'
    assert result['message'] and result['name'] == 'AcmeSync'


@pytest.mark.parametrize('payload,token,host,status', [
    ({'name': 'AcmeSync', 'verb': 'run'}, 'wrong', None, 403),
    ({'name': 'AcmeSync', 'verb': 'run'}, 'synthetic-token', 'evil.example.com', 400),
    ({'name': 'Unknown', 'verb': 'run'}, 'synthetic-token', None, 400),
    ({'name': 'Acme*', 'verb': 'run'}, 'synthetic-token', None, 400),
    ([], 'synthetic-token', None, 400),
])
def test_http_security_precedes_dispatch(monkeypatch, http_server, payload, token, host, status):
    server, port = http_server
    monkeypatch.setattr(server.task_control, 'load_runtime', lambda: pytest.fail('dispatcher reached'))
    actual, _ = post(port, payload, token=token, host=host)
    assert actual == status


def test_loader_rejects_missing_bad_and_stale_authority(tmp_path, monkeypatch):
    from control_support import fixture_runtime
    from task_console import controller
    from task_console.contracts import ContractError
    fixture_runtime(tmp_path)
    values = dict(runtime_config=str(tmp_path / 'private/config.json'), private_root=str(tmp_path / 'private'),
                  state_root=str(tmp_path / 'state'), vault_root=str(tmp_path / 'vault'))
    # Construction/status never calls the concrete transport.
    assert controller.load_runtime(**values).load()['authority_generation']
    authority = tmp_path / 'private/adoption.json'
    original = authority.read_bytes()
    for data in (b'not JSON', b'{"schemaVersion":1}'):
        authority.write_bytes(data)
        with pytest.raises(ContractError):
            controller.load_runtime(**values)
    authority.unlink()
    with pytest.raises(ContractError):
        controller.load_runtime(**values)
    authority.write_bytes(original)
    config = tmp_path / 'private/config.json'
    document = json.loads(config.read_bytes())
    document['adapter_module'] = 'must.never.import'
    config.write_text(json.dumps(document))
    with pytest.raises(ContractError, match='invalid_runtime_config'):
        controller.load_runtime(**values)


def test_cancel_and_concurrent_dispatch_do_not_start_payload(tmp_path):
    import threading
    from control_support import fixture_runtime
    from task_console.controller import Controller
    from task_console.contracts import ContractError
    from llmcall.process import execution_scope
    runtime, task_id = fixture_runtime(tmp_path)
    controller = Controller(runtime)
    cancellation = threading.Event()
    cancellation.set()
    with execution_scope(cancel=cancellation), pytest.raises(ContractError, match='control_cancelled'):
        controller.action('AcmeSync', 'run')
    with runtime.locks.hold(['authority']), pytest.raises(ContractError, match='busy'):
        controller.action('AcmeSync', 'stop')
    assert not any(op == 'run' for op, _ in runtime.scheduler.transport.calls)


def test_disabled_run_refuses_without_transport_action(tmp_path):
    from control_support import fixture_runtime
    from task_console.controller import Controller
    from task_console.contracts import ContractError
    runtime, _ = fixture_runtime(tmp_path, migrated=True)
    with pytest.raises(ContractError, match='task_disabled'):
        Controller(runtime).action('AcmeSync', 'run')
    assert not any(op == 'run' for op, _ in runtime.scheduler.transport.calls)


def test_registration_can_finish_after_slow_native_preparation(tmp_path, monkeypatch):
    from control_support import fixture_runtime
    from task_console.controller import Controller
    from task_console.registration import Conflict
    from llmcall import process

    runtime, _ = fixture_runtime(tmp_path, migrated=True, enabled=True)
    clock = [0.0]
    monkeypatch.setattr(process.time, 'monotonic', lambda: clock[0])
    original_get = runtime.vault.get

    def get(reference):
        if process.current_control().is_set():
            raise Conflict('transport_timeout', 'transport')
        return original_get(reference)

    def checkpoint(point):
        if point == 'journal_created':
            clock[0] = 65.0

    monkeypatch.setattr(runtime.vault, 'get', get)
    runtime.checkpoint = checkpoint
    result = Controller(runtime).action('AcmeSync', 'disable')
    assert result['ok'] and result['cleaned'], result
    assert runtime.scheduler.read('AcmeSync')['value']['enabled'] is False


def test_public_retire_has_no_unconfigured_bypass(monkeypatch):
    import retire
    from task_console.contracts import ContractError
    monkeypatch.setattr(retire, '_apply_legacy', lambda *a: pytest.fail('legacy bypass'))
    with pytest.raises(ContractError, match='runtime_not_configured'):
        retire.apply('AcmeSync', 'synthetic retirement')


def test_migrated_retire_reports_linked_work_without_cancelling(tmp_path, monkeypatch, http_server):
    from control_support import fixture_runtime
    runtime, task_id = fixture_runtime(tmp_path, migrated=True)
    load = runtime.load
    linked = [{'work_item_id': 'synthetic-pending', 'state': 'claimed'}]
    def supplied():
        bundle = load()
        bundle['linked_work_items'] = {task_id: linked}
        return bundle
    runtime.load = supplied
    server, port = http_server
    monkeypatch.setattr(server.task_control, 'load_runtime', lambda: runtime)
    status, preview = post(port, {'name': 'AcmeSync', 'reason': 'synthetic retirement'}, path='/api/retire/plan')
    assert status == 200 and preview['linked_work_items'] == linked
    assert runtime.load()['request']['bindings']['tasks'][task_id]['enabled'] is False
    assert runtime.journal.pending(['authority']) == []
    status, result = post(port, {'action': 'task.retire', 'name': 'AcmeSync', 'arg': 'synthetic retirement'}, path='/api/maint/act')
    assert status == 200, result
    assert result['linked_work_items'][task_id] == linked
    assert linked[0]['state'] == 'claimed'
    assert not any(op in ('run', 'stop', 'cancel') for op, _ in runtime.scheduler.transport.calls)


def test_legacy_callback_entire_operation_is_locked(tmp_path):
    import subprocess
    from control_support import fixture_runtime
    from task_console.controller import Controller
    from task_console.contracts import ContractError
    runtime, _ = fixture_runtime(tmp_path)
    seen = []
    def legacy():
        # Probe real independent lock handles while callback is still active.
        with pytest.raises(ContractError, match='busy'):
            Controller(runtime).action('AcmeSync', 'stop')
        child = subprocess.run([sys.executable, str(Path(__file__).with_name('control_support.py')),
                                str(tmp_path), 'control', '--name', 'AcmeSync', '--verb', 'stop'],
                               env=dict(os.environ), capture_output=True, text=True, timeout=20)
        assert child.returncode == 2 and json.loads(child.stdout)['error']['code'] == 'busy'
        seen.append('complete legacy operation')
        return {'ok': True, 'message': 'legacy completed'}
    result = Controller(runtime).action('AcmeSync', 'stop', legacy=legacy)
    assert result['message'] == 'legacy completed' and len(seen) == 1


def test_stale_owner_and_pending_journal_never_fall_back(tmp_path):
    from control_support import fixture_runtime
    from task_console.controller import Controller
    from task_console.contracts import ContractError
    runtime, _ = fixture_runtime(tmp_path)
    transport = runtime.scheduler.transport
    tasks = json.loads(transport.path.read_bytes())
    tasks['AcmeSync']['identity'] = 'task-definition:' + 'a' * 64
    transport.path.write_text(json.dumps(tasks))
    with pytest.raises(ContractError, match='scheduler_ownership_changed'):
        Controller(runtime).action('AcmeSync', 'stop', legacy=lambda: pytest.fail('stale fallback'))
    runtime.journal.create('3' * 32, {'schemaVersion': 1, 'transaction_id': '3' * 32,
                                   'status': 'preparing', 'locks': ['authority'], 'steps': []})
    with pytest.raises(ContractError, match='recovery_required'):
        Controller(runtime).action('AcmeSync', 'stop', legacy=lambda: pytest.fail('pending fallback'))


def test_run_and_stop_transport_uncertainty_is_incomplete(tmp_path):
    from control_support import fixture_runtime
    from task_console.controller import Controller, exit_code
    from task_console.contracts import ContractError
    runtime, _ = fixture_runtime(tmp_path, migrated=True, enabled=True)
    transport = runtime.scheduler.transport
    def interrupted(operation, request):
        if operation == 'run':
            raise ContractError('transport_cancelled', 'transport')
        return transport(operation, request)
    runtime.scheduler.transport = interrupted
    result = Controller(runtime).action('AcmeSync', 'run')
    assert result['status'] == 'cleanup_uncertain' and result['acknowledged'] is None
    assert result['payload_success'] is None and exit_code(result) == 3


def test_read_status_never_provisions_or_calls_scheduler(tmp_path, monkeypatch, capsys):
    from control_support import fixture_runtime
    from task_console.controller import load_runtime
    from task_console.__main__ import main
    fixture_runtime(tmp_path)
    values = {'runtime_config': str(tmp_path / 'private/config.json'), 'private_root': str(tmp_path / 'private'),
              'state_root': str(tmp_path / 'state'), 'vault_root': str(tmp_path / 'vault')}
    runtime = load_runtime(**values)
    runtime.scheduler.transport = lambda *args: pytest.fail('read path called Scheduler')
    assert main(['runtime-status'], runtime=runtime) == 0
    assert json.loads(capsys.readouterr().out)['pending'] == []
    assert not (tmp_path / 'state').exists()


def test_recovery_loader_preserves_missing_submission_repair(tmp_path):
    from control_support import fixture_runtime
    from task_console.controller import load_runtime
    from task_console.contracts import ContractError
    fixture_runtime(tmp_path)
    (tmp_path / 'private/desired.json').unlink()
    values = {'runtime_config': str(tmp_path / 'private/config.json'), 'private_root': str(tmp_path / 'private'),
              'state_root': str(tmp_path / 'state'), 'vault_root': str(tmp_path / 'vault')}
    with pytest.raises(ContractError):
        load_runtime(**values)
    assert load_runtime(**values, for_recovery=True).journal.pending(['authority']) == []


def test_legacy_retire_uses_runtime_paths_not_legacy_environment(tmp_path, monkeypatch, http_server):
    from control_support import fixture_runtime
    runtime, _ = fixture_runtime(tmp_path)
    unrelated = tmp_path / 'unrelated.txt'
    unrelated.write_text('synthetic unrelated file')
    monkeypatch.setenv('TASK_CONSOLE_ALLOWLIST', str(unrelated))
    monkeypatch.setenv('TASK_CONSOLE_HEALTH', str(unrelated))
    server, port = http_server
    monkeypatch.setattr(server.task_control, 'load_runtime', lambda: runtime)
    from task_console import retire
    monkeypatch.setattr(retire, '_task_state', lambda *a: pytest.fail('legacy live query'))
    monkeypatch.setattr(retire, '_disable', lambda *a: pytest.fail('already disabled task mutated'))
    status, result = post(port, {'action': 'task.retire', 'name': 'AcmeSync', 'arg': 'synthetic retirement'}, path='/api/maint/act')
    assert status == 200, result
    assert set(result['done']) == {'scheduler', 'projections'}
    assert unrelated.read_text() == 'synthetic unrelated file'
    assert 'AcmeSync' not in (tmp_path / 'private/names.ps1').read_text()
