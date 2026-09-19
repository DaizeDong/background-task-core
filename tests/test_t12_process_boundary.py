"""Synthetic PowerShell I/O only: never Scheduler, DPAPI, services or payloads."""
import os
import time

import pytest

from t12_runtime_support import reg
from task_console import runtime_windows as win

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native process boundary')


def test_fixed_script_roundtrips_stdin_json():
    script = r'''
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$r = [Console]::In.ReadToEnd() | ConvertFrom-Json
@{ok=$true; value=$r.value} | ConvertTo-Json -Compress
'''
    value = 'synthetic "quotes" $literal; unicode \u96ea'
    assert win.powershell(script, {'value': value}, timeout=10)['value'] == value


@pytest.mark.parametrize('stream', ['Out', 'Error'])
def test_output_limit_refuses_with_bounded_cleanup(monkeypatch, stream):
    monkeypatch.setattr(win, 'LIMIT', 4096)
    started = time.monotonic()
    with pytest.raises(reg.Conflict, match='bounded_transport_failed'):
        win.powershell(f'[Console]::{stream}.Write(("x" * 16384))', {}, timeout=8)
    assert time.monotonic() - started < 10


def test_timeout_does_not_wait_for_stdin_reader_or_non_daemon_pipes():
    started = time.monotonic()
    with pytest.raises(reg.Conflict, match='transport_timeout'):
        win.powershell('[Threading.Thread]::Sleep(30000)', {'text': 'x' * 1000000}, timeout=2)
    assert time.monotonic() - started < 4


def test_nonzero_exit_with_success_json_remains_failure():
    with pytest.raises(reg.Conflict, match='bounded_transport_failed'):
        win.powershell("[Console]::Out.Write('{\"ok\":true}'); exit 7", {}, timeout=8)
