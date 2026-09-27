"""The console inventory describes connected data, not client plugin installation."""
import json
import http.client
import pytest
from test_panel_parity import synthetic_server, get
from tools.make_fixtures import integration_feed_case, integration_origins_case


def test_inventory_preserves_source_and_health_boundaries(monkeypatch):
    import integrations
    import work_status
    import component_status
    monkeypatch.setattr(work_status, 'read_configured', integration_feed_case)
    monkeypatch.setattr(component_status, 'read_configured', lambda: {'available': False})
    result = integrations.read_configured()
    assert len({row['id'] for row in result['items']}) == len(result['items'])
    rows = {row['id']: row for row in result['items']}
    assert rows['work']['connection']['state'] == 'ready'
    assert rows['producer:example-mail']['connection']['state'] == 'unchecked'
    assert rows['producer:example-mail']['record_count'] == 3
    assert rows['producer:example-mail']['depends_on'] == ['work']
    assert rows['plugins']['label'] == 'Claude 插件清单'
    assert result['coverage']['producer_health'] == 'unchecked'


def test_manual_record_origins_are_distinct_from_signal_sources(monkeypatch):
    import integrations
    import work_status
    import component_status
    monkeypatch.setattr(work_status, 'read_configured', integration_origins_case)
    monkeypatch.setattr(component_status, 'read_configured', lambda: {'available': False})
    rows = {row['id']: row for row in integrations.read_configured()['items']}
    assert rows['producer:example-mail']['layer'] == 'business'
    assert rows['producer:example-manual']['layer'] == 'origin'
    assert sum(row.get('record_count', 0) for row in rows.values()) == 5


def test_empty_framework_is_available_without_business_plugins(monkeypatch):
    import integrations
    import work_status
    import component_status
    monkeypatch.setattr(work_status, 'read_configured', lambda: {'available': False, 'reason': 'work_reader_not_configured'})
    monkeypatch.setattr(component_status, 'read_configured', lambda: {'available': False})
    result = integrations.read_configured()
    assert result['available']
    assert not [row for row in result['items'] if row['layer'] == 'business']
    assert result['items'][0]['id'] == 'console'
    assert result['coverage']['work_sources'] == 'unavailable'


def test_unloadable_adapter_does_not_break_inventory(monkeypatch):
    import integrations
    import component_status
    original = integrations.importlib.import_module

    def missing(name):
        if name == 'work_status':
            raise ImportError('synthetic missing adapter')
        return original(name)

    monkeypatch.setattr(integrations.importlib, 'import_module', missing)
    monkeypatch.setattr(component_status, 'read_configured', lambda: {'available': True})
    result = integrations.read_configured()
    assert result['available']
    assert result['coverage']['work_sources'] == 'unavailable'


def test_integration_inventory_is_authenticated(synthetic_server, monkeypatch):
    import integrations
    monkeypatch.setattr(integrations, 'read_configured', lambda: {'available': True, 'items': []})
    port = synthetic_server[0]
    assert get(port, '/api/integrations')[0] == 403
    assert json.loads(get(port, '/api/integrations', token='synthetic-browser-token')[2])['available']


def test_unauthorized_posts_cannot_restart_inflight_reads(synthetic_server, monkeypatch):
    import source_reads
    invalidations = []
    monkeypatch.setattr(source_reads.boundary, 'invalidate', lambda: invalidations.append(1))
    client = http.client.HTTPConnection('127.0.0.1', synthetic_server[0], timeout=5)
    client.request('POST', '/api/work/action', body='{}', headers={'Content-Type': 'application/json'})
    response = client.getresponse()
    assert response.status == 403
    response.read();client.close()
    assert invalidations == []


def test_convo_chain_is_an_informational_library_row(monkeypatch):
    """convo-chain 是钉版本的库依赖,不是被观测的生产者:登记成 library 层,没有端点,
    不进 ROUTES(对话链的路由都带参数,是显式的受保护路由),状态只答能否导入、哪一版。"""
    import integrations
    import work_status
    import component_status
    import convo_chain
    monkeypatch.setattr(work_status, 'read_configured', lambda: {'available': False})
    monkeypatch.setattr(component_status, 'read_configured', lambda: {'available': False})
    row = {r['id']: r for r in integrations.read_configured()['items']}['convo-chain']
    assert (row['layer'], row['endpoint'], list(row['depends_on'])) == ('library', '', ['conversations'])
    assert row['connection']['state'] == 'ready'
    assert convo_chain.__version__ in row['connection']['reason']
    assert '' not in integrations.ROUTES
    assert all(a.endpoint in integrations.ROUTES for a in integrations.ADAPTERS if a.layer != 'library')


def test_an_unimportable_library_reads_unavailable_not_ready(monkeypatch):
    """负对照:库导入失败时这一行必须是 unavailable,不能因为它「只是登记」就恒为 ready。"""
    import integrations
    import work_status
    import component_status
    original = integrations.importlib.import_module

    def missing(name):
        if name == 'convo_chain':
            raise ImportError('synthetic missing library')
        return original(name)

    monkeypatch.setattr(integrations.importlib, 'import_module', missing)
    monkeypatch.setattr(work_status, 'read_configured', lambda: {'available': False})
    monkeypatch.setattr(component_status, 'read_configured', lambda: {'available': False})
    row = {r['id']: r for r in integrations.read_configured()['items']}['convo-chain']
    assert row['connection']['state'] == 'unavailable'
    assert 'synthetic missing library' in row['connection']['reason']
