"""Synthetic preparation crash recovery; real storage with a fake protector."""
import pytest
from test_authority_bootstrap import initial, approved, Crash, forbidden
from t12_runtime_support import configured, fs, encode, read_json
from task_console import adoption, registration as reg, runtime_windows
from task_console.runtime_storage import FileResources


@pytest.fixture(autouse=True)
def no_live_transport(monkeypatch):
    for name in ('COMTransport', 'DPAPIProtector', 'powershell'):
        monkeypatch.setattr(runtime_windows, name, forbidden)


def interrupt_put(runtime, monkeypatch, index, after):
    original = runtime.vault.put
    count = 0
    def put(key, value):
        nonlocal count
        count += 1
        if count == index and not after:
            raise Crash()
        result = original(key, value)
        if count == index and after:
            raise Crash()
        return result
    monkeypatch.setattr(runtime.vault, 'put', put)


@pytest.mark.parametrize('index', range(1, 8))
@pytest.mark.parametrize('after', [False, True])
def test_resume_each_vault_write_boundary(tmp_path, monkeypatch, index, after):
    runtime, task_id, source = initial(tmp_path)
    plan = approved(runtime, source)
    interrupt_put(runtime, monkeypatch, index, after)
    with pytest.raises(Crash):
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    record = runtime.journal.list()[0]
    tx = record['transaction_id']
    fresh = configured(tmp_path)
    result = adoption.resume(tx, plan['plan_revision'], runtime=fresh)
    assert result['cleaned'] and result['transaction_id'] == tx
    assert fresh.authority.receipt()['ownership'][task_id]['writer'] == 'legacy'
    assert fresh.load()['request']['machine']['authority_epoch'] == 0
    assert fresh.load()['request']['machine']['migrated_tasks'] == []
    assert {op for op, _ in fresh.scheduler.transport.calls} == {'query'}
    blobs = {p.name: p.read_bytes() for p in (tmp_path / 'vault').iterdir()}
    assert adoption.resume(tx, plan['plan_revision'], runtime=configured(tmp_path)) == result
    assert blobs == {p.name: p.read_bytes() for p in (tmp_path / 'vault').iterdir()}

@pytest.mark.parametrize('point', ['bootstrap_stage_intent', 'bootstrap_stage_prepared', 'bootstrap_stage_saved'])
@pytest.mark.parametrize('position', [1, 2, 3])
def test_legacy_partial_journal_reuses_owned_stage(tmp_path, monkeypatch, point, position):
    from copy import deepcopy
    runtime, _, source = initial(tmp_path)
    plan = approved(runtime, source)
    save = runtime.journal.save
    def legacy_save(tx, record):
        legacy = deepcopy(record)
        legacy['bootstrap'].pop('input_reference', None)
        save(tx, legacy)
    monkeypatch.setattr(runtime.journal, 'save', legacy_save)
    hits = 0
    def checkpoint(at):
        nonlocal hits
        if at == point:
            hits += 1
            if hits == position:
                raise Crash()
    runtime.checkpoint = checkpoint
    with pytest.raises(Crash):
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    record = runtime.journal.list()[0]
    assert 'input_reference' not in record['bootstrap']
    stages = {p: fs.identity(p) for p in tmp_path.rglob('.t12-stage-*')}
    tx = record['transaction_id']
    fresh = configured(tmp_path)
    assert adoption.resume(tx, plan['plan_revision'], runtime=fresh)['cleaned']
    for stage, identity in stages.items():
        # Published hard link keeps the identity proven before interruption.
        role = next(step['key'] for step in record['steps']
                    if stage.name.endswith(adoption.digest(step['token'].encode())))
        assert fs.identity(role) == identity
    assert fresh.authority.receipt()['bootstrap_plan_revision'] == plan['plan_revision']


def pending(tmp_path, monkeypatch, index=1):
    runtime, _, source = initial(tmp_path)
    plan = approved(runtime, source)
    interrupt_put(runtime, monkeypatch, index, False)
    with pytest.raises(Crash):
        adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    return configured(tmp_path), source, plan, runtime.journal.list()[0]['transaction_id']


@pytest.mark.parametrize('drift', ['source', 'input', 'binding', 'projection', 'task', 'config', 'approval', 'locks', 'foreign_journal', 'foreign_receipt', 'adoption', 'pointer'])
def test_early_resume_refuses_drift_and_foreign_resources(tmp_path, monkeypatch, drift):
    runtime, source, plan, tx = pending(tmp_path, monkeypatch)
    config = runtime.authority.config
    from pathlib import Path
    paths = {'source': tmp_path / 'private/maintained.py', 'input': config.desired,
             'binding': source, 'projection': Path(config.paths['categories.json'])}
    approval = plan['plan_revision']
    if drift in paths:
        p = paths[drift]
        fs.atomic_replace(p, p.read_bytes() + b' ')
    elif drift == 'task':
        fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode({}))
    elif drift == 'config':
        object.__setattr__(config, 'launchers', {})
    elif drift == 'approval':
        approval = 'sha256:' + '0' * 64
    elif drift == 'locks':
        record = runtime.journal.load(tx)
        record['locks'] = ['authority']
        runtime.journal.save(tx, record)
    elif drift == 'foreign_journal':
        record = runtime.journal.load(tx)
        record['transaction_id'] = 'f' * 32
        runtime.journal.create('f' * 32, record)
    else:
        p = {'foreign_receipt': config.state_root / 'receipts' / ('f' * 32 + '.json'),
             'adoption': config.adoption, 'pointer': runtime.authority.pointer}[drift]
        fs.atomic_replace(p, b'{"synthetic_foreign":true}')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(reg.Conflict):
        adoption.resume(tx, approval, runtime=runtime)
    assert before == {p: p.read_bytes() for p in before}
    assert not runtime.journal.load(tx)['cleaned']


@pytest.mark.parametrize('damage', ['equal_bytes_replaced', 'identity_missing'])
def test_unproven_stage_is_retained(tmp_path, monkeypatch, damage):
    runtime, _, plan, tx = pending(tmp_path, monkeypatch, 3)
    record = runtime.journal.load(tx)
    step = record['steps'][0]
    stage = next(tmp_path.rglob('.t12-stage-*'))
    if damage == 'equal_bytes_replaced':
        fs.atomic_replace(stage, stage.read_bytes())
    else:
        resources = adoption._files(runtime, [step['key']])
        owner_path = resources._stage(step['token'])
        owner = read_json(owner_path)
        owner['identity'] = None
        fs.atomic_replace(owner_path, encode(owner))
    before, identity = stage.read_bytes(), fs.identity(stage)
    with pytest.raises(reg.Conflict, match='stage_owner_lost'):
        adoption.resume(tx, plan['plan_revision'], runtime=runtime)
    assert stage.read_bytes() == before and fs.identity(stage) == identity
    assert not runtime.authority.config.adoption.exists()


def test_early_resume_accepts_running_tasks(tmp_path, monkeypatch):
    runtime, _, plan, tx = pending(tmp_path, monkeypatch)
    tasks = read_json(tmp_path / 'synthetic-scheduler.json')
    tasks['AcmeSync']['value']['running'] = True
    fs.atomic_replace(tmp_path / 'synthetic-scheduler.json', encode(tasks))
    assert adoption.resume(tx, plan['plan_revision'], runtime=runtime)['cleaned']
    assert read_json(tmp_path / 'synthetic-scheduler.json') == tasks


def test_prepared_snapshot_is_read_only_and_requires_durable_identity(tmp_path):
    target = tmp_path / 'value.json'
    resources = FileResources([target], tmp_path / 'stages')
    token = 'b' * 32 + ':0'
    assert resources.prepared_snapshot(str(target), token) is None
    assert list(tmp_path.iterdir()) == []
    desired = resources.prepare(str(target), b'{"synthetic":true}', token)
    assert resources.prepared_snapshot(str(target), token) == desired
    stage = resources.staging_path(str(target), token)
    before = stage.read_bytes()
    fs.atomic_replace(stage, before)
    identity = fs.identity(stage)
    with pytest.raises(reg.Conflict, match='stage_owner_lost'):
        resources.prepared_snapshot(str(target), token)
    assert stage.read_bytes() == before and fs.identity(stage) == identity
    assert not target.exists()


def test_prepared_snapshot_retains_unowned_orphan(tmp_path):
    target = tmp_path / 'value.json'
    resources = FileResources([target], tmp_path / 'stages')
    token = 'c' * 32 + ':0'
    stage = resources.staging_path(str(target), token)
    fs.atomic_replace(stage, b'synthetic foreign stage')
    identity = fs.identity(stage)
    with pytest.raises(reg.Conflict, match='stage_owner_lost'):
        resources.prepared_snapshot(str(target), token)
    assert fs.identity(stage) == identity
    assert not (tmp_path / 'stages').exists()


@pytest.mark.parametrize('field', ['identity', 'digest'])
def test_prepared_snapshot_rejects_incomplete_empty_stage_receipt(tmp_path, field):
    target = tmp_path / 'value.json'
    resources = FileResources([target], tmp_path / 'stages')
    token = 'd' * 32 + ':0'
    assert resources.prepare(str(target), None, token) == {'state': 'absent'}
    receipt = read_json(resources._stage(token))
    receipt.pop(field)
    fs.atomic_replace(resources._stage(token), encode(receipt))
    with pytest.raises(reg.Conflict, match='stage_owner_lost'):
        resources.prepared_snapshot(str(target), token)
    assert not target.exists()


def test_cleaned_bootstrap_no_longer_owns_receipt_stage_name(tmp_path):
    runtime, _, source = initial(tmp_path)
    plan = approved(runtime, source)
    result = adoption.apply(plan, plan['plan_revision'], runtime=runtime)
    tx = result['transaction_id']
    receipt = runtime.authority.config.state_root / 'receipts' / (tx + '.json')
    resources = adoption._files(runtime, [str(receipt)])
    stage = resources.staging_path(str(receipt), tx + ':1')
    fs.atomic_replace(stage, b'synthetic foreign stage after cleanup')
    before = {p: (p.read_bytes(), fs.identity(p)) for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(reg.Conflict, match='existing_receipt_evidence'):
        adoption.resume(tx, plan['plan_revision'], runtime=configured(tmp_path))
    assert before == {p: (p.read_bytes(), fs.identity(p)) for p in tmp_path.rglob('*') if p.is_file()}
