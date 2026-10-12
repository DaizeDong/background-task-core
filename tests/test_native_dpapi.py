"""Synthetic native DPAPI interoperability; never read existing vault objects."""
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
from task_console import runtime_windows as win
from task_console.registration import Conflict
from tools.make_fixtures import native_dpapi_case


native = pytest.mark.skipif(os.name != 'nt', reason='Windows CurrentUser DPAPI')


def same(actual, expected):
    if actual != expected:
        pytest.fail('synthetic DPAPI roundtrip mismatch', pytrace=False)


def forbidden(*args, **kwargs):
    pytest.fail('unprotect launched PowerShell', pytrace=False)


def native_stub(monkeypatch, call):
    monkeypatch.setitem(sys.modules, 'win32crypt', SimpleNamespace(CryptUnprotectData=call))
    monkeypatch.setattr(win, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(win, 'powershell', forbidden)


@native
@pytest.mark.parametrize('case', ['empty', 'ascii', 'unicode', 'nul', 'large'])
def test_native_powershell_interoperability_without_read_subprocess(monkeypatch, case):
    import win32crypt

    text = native_dpapi_case()['text'][case]
    old = win.powershell(win._PROTECT, {'operation': 'protect', 'text': text})['text']
    same(win32crypt.CryptUnprotectData(bytes.fromhex(old), None, None, None, 1)[1],
         text.encode('utf-16-le'))
    new = win32crypt.CryptProtectData(text.encode('utf-16-le'), None, None, None, None, 1)
    same(win.powershell(win._PROTECT, {'operation': 'unprotect', 'text': new.hex()})['text'], text)
    monkeypatch.setattr(win, 'powershell', forbidden)
    for ciphertext in (old.encode('ascii'), new.hex().upper().encode('ascii')):
        if case == 'unicode':
            with pytest.raises(Conflict, match='protector_invalid'):
                win.DPAPIProtector().unprotect(ciphertext)
        else:
            same(win.DPAPIProtector().unprotect(ciphertext), text.encode('ascii'))


@native
@pytest.mark.parametrize('case', ['ascii', 'unicode', 'nul'])
def test_native_securestring_format_interoperability(case):
    import win32crypt
    from test_t12_large_vault_protector import LEGACY

    text = native_dpapi_case()['text'][case]
    old = win.powershell(LEGACY, {'operation': 'protect', 'text': text})['text']
    same(win32crypt.CryptUnprotectData(bytes.fromhex(old), None, None, None, 1)[1],
         text.encode('utf-16-le'))
    new = win32crypt.CryptProtectData(text.encode('utf-16-le'), None, None, None, None, 1)
    same(win.powershell(LEGACY, {'operation': 'unprotect', 'text': new.hex()})['text'], text)


@native
def test_native_corrupt_cipher_fails_without_subprocess(monkeypatch):
    import win32crypt

    cases = native_dpapi_case()
    damaged = bytearray(win32crypt.CryptProtectData(
        cases['text']['ascii'].encode('utf-16-le'), None, None, None, None, 1))
    damaged[-1] ^= 1
    monkeypatch.setattr(win, 'powershell', forbidden)
    for ciphertext in (cases['invalid_cipher'], damaged.hex().encode('ascii')):
        with pytest.raises(Conflict, match='protector_failed') as raised:
            win.DPAPIProtector().unprotect(ciphertext)
        assert raised.value.field == 'vault'


@native
@pytest.mark.parametrize('case', ['odd', 'surrogate'])
def test_native_rejects_malformed_utf16_plaintext(monkeypatch, case):
    import win32crypt

    data = native_dpapi_case()['malformed_utf16'][case]
    ciphertext = win32crypt.CryptProtectData(data, None, None, None, None, 1).hex().encode('ascii')
    monkeypatch.setattr(win, 'powershell', forbidden)
    with pytest.raises(Conflict, match='protector_invalid'):
        win.DPAPIProtector().unprotect(ciphertext)


def test_missing_native_dependency_is_explicit_and_has_no_fallback(monkeypatch):
    native_stub(monkeypatch, forbidden)
    monkeypatch.setitem(sys.modules, 'win32crypt', None)
    with pytest.raises(Conflict, match='protector_dependency_unavailable') as raised:
        win.DPAPIProtector().unprotect(native_dpapi_case()['invalid_cipher'])
    assert raised.value.field == 'vault'


def test_native_failure_does_not_expose_detail_or_retry(monkeypatch):
    def fail(*args):
        raise OSError('synthetic native failure detail')
    native_stub(monkeypatch, fail)
    with pytest.raises(Conflict, match='protector_failed') as raised:
        win.DPAPIProtector().unprotect(native_dpapi_case()['invalid_cipher'])
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert 'synthetic' not in str(raised.value)


def test_native_call_uses_null_entropy_and_forbids_ui(monkeypatch):
    data = native_dpapi_case()['text']['ascii'].encode('ascii')
    calls = []
    def decrypt(*args):
        calls.append(args)
        return '', data.decode('ascii').encode('utf-16-le')
    native_stub(monkeypatch, decrypt)
    ciphertext = native_dpapi_case()['invalid_cipher']
    same(win.DPAPIProtector().unprotect(ciphertext), data)
    assert calls == [(bytes.fromhex(ciphertext.decode('ascii')), None, None, None, 1)]


@pytest.mark.parametrize('case', ['odd', 'surrogate', 'unicode', 'oversized'])
def test_native_plaintext_validation_prevents_cache_insertion(monkeypatch, case):
    cases = native_dpapi_case()
    if case == 'oversized':
        data = native_dpapi_case(large_size=win.DPAPIProtector.PLAIN_LIMIT + 1)['text']['large'].encode('utf-16-le')
    elif case == 'unicode':
        data = cases['text'][case].encode('utf-16-le')
    else:
        data = cases['malformed_utf16'][case]
    native_stub(monkeypatch, lambda *args: ('', data))
    protector = win.DPAPIProtector()
    code = 'protector_limit' if case == 'oversized' else 'protector_invalid'
    with pytest.raises(Conflict, match=code):
        protector.unprotect(cases['invalid_cipher'])
    assert not protector._cache and protector._cache_size == 0


@pytest.mark.parametrize('checkpoint', ['cancel', 'timeout'])
def test_native_read_rechecks_operation_before_cache_or_return(monkeypatch, checkpoint):
    from llmcall import process

    cancel = threading.Event()
    state = {'decrypted': False}
    data = native_dpapi_case()['text']['ascii'].encode('utf-16-le')
    def decrypt(*args):
        state['decrypted'] = True
        if checkpoint == 'cancel':
            cancel.set()
        return '', data
    native_stub(monkeypatch, decrypt)
    if checkpoint == 'timeout':
        monkeypatch.setattr(process, 'remaining_timeout', lambda _: 0 if state['decrypted'] else 25)
    protector = win.DPAPIProtector()
    code = 'transport_cancelled' if checkpoint == 'cancel' else 'transport_timeout'
    with process.execution_scope(timeout=30, cancel=cancel):
        with pytest.raises(Conflict, match=code):
            protector.unprotect(native_dpapi_case()['invalid_cipher'])
    assert not protector._cache and protector._cache_size == 0
