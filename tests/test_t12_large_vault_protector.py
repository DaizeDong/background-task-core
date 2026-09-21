"""Synthetic protector tests only; no Scheduler, credentials, or real vault reads.

Select ``-k 'not native'`` for pure protocol tests. Native tests use the current
process identity through the unchanged, bounded llmcall.process transport.
"""
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT.parent / 'fleet-guards'))
from task_console import runtime_windows as win
from task_console import runtime_storage as storage
from task_console.registration import Conflict


LEGACY = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
  $r = [Console]::In.ReadToEnd() | ConvertFrom-Json
  if ($r.operation -eq 'protect') {
    $s = ConvertTo-SecureString ([string]$r.text) -AsPlainText -Force
    $result = ConvertFrom-SecureString $s
  } else {
    $s = ConvertTo-SecureString ([string]$r.text)
    $p = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { $result = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($p) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p) }
  }
  @{ok=$true; text=$result} | ConvertTo-Json -Compress
} catch { @{ok=$false; code='protector_failed'} | ConvertTo-Json -Compress }
'''

native = pytest.mark.skipif(os.name != 'nt', reason='Windows CurrentUser DPAPI')


def same_bytes(actual, expected):
    # Do not let assertion introspection disclose ciphertext or envelope content.
    if actual != expected:
        pytest.fail('synthetic roundtrip mismatch', pytrace=False)


def memory_vault(monkeypatch, protector):
    objects = {}
    monkeypatch.setattr(storage.fs, 'validate_path', lambda p: Path(p))
    def publish(path, data):
        if path in objects:
            return False
        objects[path] = data
        return True
    def read(path, limit):
        data = objects[path]
        if len(data) > limit:
            raise ValueError('synthetic read limit')
        return data
    monkeypatch.setattr(storage.fs, 'create_no_replace', publish)
    monkeypatch.setattr(storage.fs, 'read_bounded', read)
    return storage.ProtectedVault(Path('synthetic-vault'), '1' * 32, protector), objects


@pytest.mark.parametrize('code', ['transport_timeout', 'transport_cancelled', 'transport_cleanup_failed'])
def test_interrupted_vault_read_keeps_transport_diagnosis(monkeypatch, code):
    class SyntheticProtector:
        def protect(self, value): return value
        def unprotect(self, value): return value

    protector = SyntheticProtector()
    vault, _ = memory_vault(monkeypatch, protector)
    reference = vault.put('2' * 32 + ':0', {'state': 'present', 'value': 'synthetic'})

    def interrupted(value):
        raise Conflict(code, 'transport')

    monkeypatch.setattr(protector, 'unprotect', interrupted)
    with pytest.raises(Conflict) as raised:
        vault.get(reference)
    assert raised.value.code == code and raised.value.field == 'transport'


def test_decryption_reuses_exact_ciphertext_only_in_the_same_operation(monkeypatch):
    from llmcall.process import execution_scope
    calls = []
    def convert(script, request):
        calls.append(request['text'])
        return {'text': request['text']}
    monkeypatch.setattr(win, 'powershell', convert)
    protector = win.DPAPIProtector()
    first, changed = (bytes([i]).hex().encode('ascii') for i in range(2))
    with execution_scope(timeout=30):
        same_bytes(protector.unprotect(first), first)
        same_bytes(protector.unprotect(first), first)
        same_bytes(protector.unprotect(changed), changed)
        assert len(calls) == 2
    with execution_scope(timeout=30):
        same_bytes(protector.unprotect(first), first)
    assert len(calls) == 3


def test_decryption_cache_preserves_cancellation_and_memory_bound(monkeypatch):
    import threading
    from llmcall.process import execution_scope
    calls = []
    def convert(script, request):
        calls.append(request['text'])
        return {'text': request['text']}
    monkeypatch.setattr(win, 'powershell', convert)
    protector = win.DPAPIProtector()
    protector.CACHE_LIMIT = 2
    cancel = threading.Event()
    first, second = (bytes([i]).hex().encode('ascii') for i in range(2))
    with execution_scope(timeout=30, cancel=cancel):
        protector.unprotect(first)
        protector.unprotect(second)
        protector.unprotect(first)
        assert len(calls) == 3 and protector._cache_size <= protector.CACHE_LIMIT
        cancel.set()
        with pytest.raises(Conflict, match='transport_cancelled'):
            protector.unprotect(first)
        assert len(calls) == 3


@pytest.mark.parametrize('value', [None, 'text', 12, {}, b'\xff'])
@pytest.mark.parametrize('method', ['protect', 'unprotect'])
def test_wrong_input_type_or_encoding_refuses_before_transport(monkeypatch, value, method):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid input reached transport', pytrace=False)
    monkeypatch.setattr(win, 'powershell', forbidden)
    with pytest.raises(Conflict, match='protector_invalid'):
        getattr(win.DPAPIProtector(), method)(value)


@pytest.mark.parametrize('value', [b'', b'0', b'zz', b'00 00', b'00\n'])
def test_invalid_hex_refuses_before_transport(monkeypatch, value):
    monkeypatch.setattr(win, 'powershell', lambda *a, **k: pytest.fail('transport called'))
    with pytest.raises(Conflict, match='protector_invalid'):
        win.DPAPIProtector().unprotect(value)


@pytest.mark.parametrize('reply', [{}, {'text': None}, {'text': 1}, {'text': []},
                                  {'text': '\u96ea'}, {'text': 'gg'}, {'text': 'a'}])
def test_protect_validates_reply(monkeypatch, reply):
    monkeypatch.setattr(win, 'powershell', lambda *a, **k: reply)
    with pytest.raises(Conflict, match='protector_invalid'):
        win.DPAPIProtector().protect(b'synthetic')


def test_scoped_expansion_limit_prevents_publication(monkeypatch):
    protector = win.DPAPIProtector()
    monkeypatch.setattr(win, 'powershell', lambda *a, **k: pytest.fail('transport called'))
    vault, objects = memory_vault(monkeypatch, protector)
    with pytest.raises(Conflict, match='protector_limit'):
        vault.put('2' * 32 + ':0', {'state': 'present', 'value': b'x' * (2 * 1024 * 1024)})
    assert not objects
    assert win.LIMIT == storage.LIMIT == 8 * 1024 * 1024


def test_ciphertext_transport_framing_limit(monkeypatch):
    monkeypatch.setattr(win, 'powershell', lambda *a, **k: pytest.fail('transport called'))
    with pytest.raises(Conflict, match='protector_limit'):
        win.DPAPIProtector().unprotect(b'00' * (win.LIMIT // 2))


def test_oversized_cipher_reply_prevents_publication(monkeypatch):
    monkeypatch.setattr(win, 'powershell', lambda *a, **k: {'text': '00' * (win.LIMIT // 2)})
    vault, objects = memory_vault(monkeypatch, win.DPAPIProtector())
    with pytest.raises(Conflict, match='protector_limit'):
        vault.put('2' * 32 + ':0', {'state': 'present', 'value': b'synthetic'})
    assert not objects


def test_protector_keeps_shared_transport_defaults(monkeypatch):
    calls = []
    def capture(script, request, **kwargs):
        calls.append((script, request, kwargs))
        return {'text': '00' if request['operation'] == 'protect' else 'synthetic'}
    monkeypatch.setattr(win, 'powershell', capture)
    protector = win.DPAPIProtector()
    protector.protect(b'synthetic')
    protector.unprotect(b'00')
    assert [c[1]['operation'] for c in calls] == ['protect', 'unprotect']
    assert all(c[0] == win._PROTECT and not c[2] for c in calls)


@pytest.mark.parametrize('reply', [{}, {'text': None}, {'text': 1}, {'text': '\u96ea'}])
def test_unprotect_validates_reply(monkeypatch, reply):
    monkeypatch.setattr(win, 'powershell', lambda *a, **k: reply)
    with pytest.raises(Conflict, match='protector_invalid'):
        win.DPAPIProtector().unprotect(b'00')


def test_plain_limit_boundary_and_cipher_expansion(monkeypatch):
    protector = win.DPAPIProtector()
    seen = []
    def capture(script, request):
        seen.append(len(request['text']))
        return {'text': '00'}
    monkeypatch.setattr(win, 'powershell', capture)
    protector.protect(b'x' * protector.PLAIN_LIMIT)
    with pytest.raises(Conflict, match='protector_limit'):
        protector.protect(b'x' * (protector.PLAIN_LIMIT + 1))
    assert seen == [protector.PLAIN_LIMIT]
    assert 4 * protector.PLAIN_LIMIT + 3072 == protector.CIPHER_LIMIT


def test_json_request_expansion_remains_bounded():
    # Control bytes expand sixfold in JSON; this must fail before process import.
    if os.name != 'nt':
        pytest.skip('Windows transport preflight')
    with pytest.raises(Conflict, match='request_limit'):
        win.DPAPIProtector().protect(b'\x00' * win.DPAPIProtector.PLAIN_LIMIT)


@native
def test_native_small_legacy_interoperability():
    data = json.dumps({'synthetic': 'quotes " \\ \u96ea \u0000'}).encode('ascii')
    protector = win.DPAPIProtector()
    old = win.powershell(LEGACY, {'operation': 'protect', 'text': data.decode('ascii')})['text']
    same_bytes(protector.unprotect(old.encode('ascii')), data)
    new = protector.protect(data)
    same_bytes(protector.unprotect(new), data)
    legacy_read = win.powershell(LEGACY, {'operation': 'unprotect', 'text': new.decode('ascii')})['text']
    same_bytes(legacy_read.encode('ascii'), data)


@native
def test_native_above_securestring_ceiling():
    data = b'x' * 65537
    with pytest.raises(Conflict):
        win.powershell(LEGACY, {'operation': 'protect', 'text': data.decode('ascii')})
    protector = win.DPAPIProtector()
    same_bytes(protector.unprotect(protector.protect(data)), data)


@native
def test_native_large_protected_vault_envelope(monkeypatch):
    raw = json.dumps({'synthetic': 'x' * 1_153_434}).encode('ascii')
    snapshot = {'state': 'present', 'identity': 'synthetic', 'value': raw}
    vault, objects = memory_vault(monkeypatch, win.DPAPIProtector())
    reference = vault.put('2' * 32 + ':0', snapshot)
    same_bytes(vault.get(reference)['value'], raw)
    assert len(objects) == 1
    size = len(next(iter(objects.values())))
    assert 4 * 1024 * 1024 < size < storage.LIMIT


@native
def test_native_invalid_cipher_and_wrong_protocol_types():
    for request in ({'operation': 'unprotect', 'text': '00' * 100},
                    {'operation': 'unprotect', 'text': '0'},
                    {'operation': 'unprotect', 'text': 'zz'},
                    {'operation': 'protect', 'text': 123},
                    {'operation': 'protect', 'text': ['synthetic']},
                    {'operation': 'unknown', 'text': 'synthetic'}):
        with pytest.raises(Conflict):
            win.powershell(win._PROTECT, request)


@native
def test_native_disk_large_reference(tmp_path):
    """Actual CurrentUser DPAPI plus ordinary filesystem publication/readback."""
    vault = storage.ProtectedVault(tmp_path / 'vault', 'a' * 32, win.DPAPIProtector())
    snapshot = {'state': 'present', 'identity': 'synthetic-effective-input',
                'value': {'submitted': {'synthetic': 'x' * 510_000},
                          'request': {'synthetic': 'x' * 510_000}}}
    reference = vault.put('b' * 32 + ':initial-input', snapshot)
    if vault.get(reference) != snapshot:
        pytest.fail('synthetic disk reference mismatch', pytrace=False)
    files = list((tmp_path / 'vault').glob('*.cred'))
    assert len(files) == 1 and 4_000_000 < files[0].stat().st_size < storage.LIMIT
