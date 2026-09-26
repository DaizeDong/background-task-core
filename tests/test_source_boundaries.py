"""Read failures, concurrent refreshes and write/read overlap use synthetic data."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/task_console'))

import component_status
import maint
import work_status
from tools.make_fixtures import health_case, work_feed_case


@pytest.mark.parametrize('broken', ['skills', 'memory', 'plugins'])
def test_maintenance_sections_fail_independently(monkeypatch, broken):
    for name in ('skills', 'memory', 'plugins'):
        monkeypatch.setattr(maint, 'read_' + name, lambda: {'available': True})
    monkeypatch.setattr(component_status, 'read_configured', lambda: {'available': True})
    def fail():
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr(maint, 'read_' + broken, fail)
    result = maint.read_all()
    assert result[broken]['available'] is False
    assert result[broken]['reason_code'] == 'reader_failed'
    assert all(value['available'] for name, value in result.items() if name != broken)


def test_invalid_embedded_catalog_preserves_task_health():
    case = health_case()
    result = component_status.evaluate_snapshot({'schemaVersion': 1, 'tasks': [case], 'catalog': {}}, case['now'])
    assert len(result['tasks']) == 1
    assert result['catalog']['available'] is False
    assert result['catalog']['reason_code'] == 'query_failed'


@pytest.mark.parametrize('mode', ['utf8', 'offer', 'offers', 'current', 'event', 'source'])
def test_bad_owner_contract_is_local_unavailable(tmp_path, monkeypatch, mode):
    cli = tmp_path / 'owner.py'
    cli.write_text('# synthetic', encoding='utf-8')
    payload = work_feed_case()
    if mode == 'offer': payload['items'][0]['actions'] = {'offers': [None]}
    if mode == 'offers': payload['items'][0]['actions'] = {'offers': {}}
    if mode == 'current': payload['items'][0]['actions'] = {'current': []}
    if mode == 'event': payload['events'] = [None]
    if mode == 'source': payload['sources'] = [None]
    output = b'\xff' if mode == 'utf8' else json.dumps(payload).encode()
    monkeypatch.setattr(work_status.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=output))
    result = work_status.read_configured({'TASK_CONSOLE_REMINDER_CLI': str(cli), 'TASK_CONSOLE_REMINDER_DB': str(tmp_path / 'work.db')})
    assert result['available'] is False
    assert result['reason'] in ('work_reader_failed', 'work_contract_invalid')


def test_shared_read_has_no_persistent_payload_cache():
    from source_reads import ReadBoundary
    boundary = ReadBoundary()
    entered, finish = threading.Event(), threading.Event()
    calls = []
    def reader():
        calls.append(1); entered.set()
        assert finish.wait(3)
        return {'available': True, 'items': []}
    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(boundary.read, 'work', reader)
        assert entered.wait(3)
        second = pool.submit(boundary.read, 'work', reader)
        # Wait for the joining caller, not a guessed sleep.
        with boundary._condition:
            assert boundary._condition.wait_for(lambda: boundary._active['work']['callers'] == 2, timeout=2)
        finish.set()
        a, b = first.result(), second.result()
    a['items'].append('synthetic')
    assert b['items'] == []
    assert len(calls) == 1
    boundary.read('work', reader)
    assert len(calls) == 2


def test_write_invalidates_an_inflight_snapshot():
    from source_reads import ReadBoundary
    boundary = ReadBoundary()
    entered, finish = threading.Event(), threading.Event()
    state = {'revision': 1}
    def reader():
        value = dict(state)
        entered.set(); assert finish.wait(3)
        return value
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(boundary.read, 'work', reader)
        assert entered.wait(3)
        state['revision'] = 2
        boundary.invalidate()
        finish.set()
        assert pending.result()['revision'] == 2


def test_failure_keeps_last_success_but_does_not_return_stale_payload():
    from source_reads import ReadBoundary
    boundary = ReadBoundary()
    boundary.read('work', lambda: {'available': True})
    result = boundary.read('work', lambda: {'available': False, 'reason': 'synthetic offline'})
    assert result['available'] is False
    observation = boundary.snapshot()['work']
    assert observation['state'] == 'stale'
    assert observation['last_success']
    assert observation['reason'] == 'synthetic offline'


def test_maintenance_reads_overlap_and_release_workers(monkeypatch):
    # Serial aggregation cannot enter every reader before the release signal.
    entered = {name: threading.Event() for name in ('skills', 'memory', 'plugins', 'components')}
    release = threading.Event()
    workers = []

    def reader(name):
        workers.append(threading.current_thread())
        entered[name].set()
        assert release.wait(3)
        return {'available': True}

    for name in ('skills', 'memory', 'plugins'):
        monkeypatch.setattr(maint, 'read_' + name, lambda name=name: reader(name))
    monkeypatch.setattr(component_status, 'read_configured', lambda: reader('components'))
    with ThreadPoolExecutor(1) as caller:
        pending = caller.submit(maint.read_all)
        try:
            assert all(event.wait(1) for event in entered.values())
        finally:
            release.set()
        result = pending.result()
    assert set(result) == set(entered)
    assert all(section['available'] for section in result.values())
    assert not any(worker.is_alive() for worker in workers)
