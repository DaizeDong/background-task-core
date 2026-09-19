"""Exercise the public CLI loader around synthetic bootstrap authority states."""
import hashlib
import json

import pytest

from task_console import adoption, runtime as runtime_owner, runtime_windows
from task_console.__main__ import main
from test_authority_bootstrap import initial, approved, forbidden
from test_t11_bootstrap_repairs import crash_at


@pytest.fixture(autouse=True)
def no_live_transport(monkeypatch):
    for name in ('COMTransport', 'DPAPIProtector', 'powershell'):
        monkeypatch.setattr(runtime_windows, name, forbidden)


def tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('state', ['uninitialized', 'pending', 'ready', 'corrupt', 'invalid_desired'])
def test_status_through_explicit_cli_roots_reports_without_writing(tmp_path, monkeypatch, capsys, state):
    runtime, task_id, source = initial(tmp_path)
    tx = None
    if state == 'pending':
        _, tx = crash_at(runtime, source, 'bootstrap_staged')
    elif state in ('ready', 'invalid_desired'):
        plan = approved(runtime, source)
        tx = adoption.apply(plan, plan['plan_revision'], runtime=runtime)['transaction_id']
        if state == 'invalid_desired':
            desired = json.loads(runtime.authority.config.desired.read_bytes())
            desired['components'] = []
            runtime.authority.config.desired.write_text(json.dumps(desired), encoding='utf-8')
    elif state == 'corrupt':
        runtime.authority.config.adoption.write_bytes(b'not JSON')

    config = runtime.authority.config
    roots = ['--runtime-config', str(tmp_path / 'private/config.json'),
             '--private-root', str(config.private_root), '--state-root', str(config.state_root),
             '--vault-root', str(config.vault_root)]
    constructed = []

    def construct(parsed):
        assert parsed.private_root == config.private_root and parsed.state_root == config.state_root
        constructed.append(parsed)
        return runtime

    # Keep the real controller loader and config reader. Only replace native
    # runtime construction with the existing synthetic runtime implementation.
    monkeypatch.setattr(runtime_owner, 'create_runtime', construct)
    runtime.scheduler.transport = forbidden
    before = tree(tmp_path)
    code = main(['runtime-status', *roots])
    result = json.loads(capsys.readouterr().out)
    assert len(constructed) == 1 and tree(tmp_path) == before
    assert 'error' not in result
    if state == 'pending':
        assert code == 0 and result['pending_bootstrap'] == [tx]
        assert result['action_required'] == 'adoption-resume' and not result['initialized']
    elif state == 'ready':
        assert code == 0 and result['authority'] == 'available'
        assert result['generation'] == tx and result['authority_epoch'] == 0
        assert result['pending'] == []
    else:
        assert code == 2 and result['authority'] != 'available'
        assert result['initialized'] is (state in ('corrupt', 'invalid_desired'))
        assert result['pending_bootstrap'] == [] and result['action_required'] is None

    if state in ('uninitialized', 'corrupt', 'invalid_desired'):
        # A diagnostic entry may report missing authority. Mutation planning
        # still requires valid authority through the ordinary loader.
        assert main(['registration-plan', '--task-id', task_id, *roots]) == 2
        assert 'error' in json.loads(capsys.readouterr().out)
        assert tree(tmp_path) == before
