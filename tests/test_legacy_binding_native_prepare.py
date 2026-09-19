"""Unregistered COM definition only; never query, register, or execute a task."""
from copy import deepcopy
import os

import pytest

from test_legacy_binding_normalization import legacy, semantics_without_action_metadata
from task_console.export_restore import normalize_legacy_binding
from task_console.runtime_windows import COMTransport
from task_console.scheduler_windows import WindowsScheduler
from task_console.runtime_xml import render


@pytest.mark.skipif(os.name != 'nt', reason='Windows unregistered COM preparation')
def test_native_omitted_fields_and_render_preservation():
    spec, snapshot = legacy()
    # A schema-valid synthetic SID avoids name resolution for a fictional account.
    from test_legacy_binding_normalization import bind
    spec, snapshot = bind(spec, snapshot['value']['xml'].replace('AcmeService', 'S-1-5-21-111111111-222222222-333333333-1001'))
    calls = []
    class PrepareOnly(COMTransport):
        def __call__(self, operation, request):
            assert operation == 'prepare'
            calls.append(operation)
            return super().__call__(operation, request)
    scheduler = WindowsScheduler(PrepareOnly())
    before = deepcopy((spec, snapshot))
    result = normalize_legacy_binding(spec, snapshot, scheduler=scheduler)
    assert result['principal']['run_level'] == 'LeastPrivilege'
    assert result['power']['wake_to_run'] is False
    result['argv'] = ['C:/Acme/final/python.exe', '-I', '-m', 'acme_sync']
    rendered = render(result, snapshot, False)
    a = scheduler.prepare(spec['name'], snapshot['value'], 'readback')
    b = scheduler.prepare(spec['name'], rendered, 'readback')
    assert semantics_without_action_metadata(a['value']['xml']) == semantics_without_action_metadata(b['value']['xml'])
    assert (spec, snapshot) == before
    assert calls and set(calls) == {'prepare'}
