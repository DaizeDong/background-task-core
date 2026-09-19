"""Selected legacy conversion using generated synthetic bindings only."""
from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from t12_runtime_support import example_request, reg
from task_console import export_restore as owner, runtime_xml as xml


def legacy():
    spec = reg._compile({'request': example_request()})['task_specs'][0]
    value = xml.render(spec, {'state': 'absent'}, False)
    root = xml.parse(value['xml'])
    root.remove(root.find('{'+xml.NS+'}Data'))
    principal = root.find('./{'+xml.NS+'}Principals/{'+xml.NS+'}Principal')
    principal.remove(principal.find('{'+xml.NS+'}RunLevel'))
    settings = root.find('{'+xml.NS+'}Settings')
    settings.remove(settings.find('{'+xml.NS+'}WakeToRun'))
    source = ET.tostring(root, encoding='unicode').replace('PT420S', 'PT7M')
    return bind(spec, source)


def bind(spec, source):
    spec = deepcopy(spec)
    root = xml.parse(source)
    for key, tag in (('principal', 'Principals'), ('power', 'Settings')):
        spec[key] = {'xml': ET.tostring(root.find('{'+xml.NS+'}'+tag), encoding='unicode')}
    spec['xml_passthrough'] = {'xml': source, 'owner': 'synthetic', 'reason': 'preserve'}
    snapshot = xml.observation(spec['name'], {'xml': source, 'spec': {'argv': spec['argv'], 'enabled': False},
                                             'enabled': False, 'running': False})
    return spec, snapshot


class Preparation:
    """Injected typed-definition observation; every other operation is absent."""
    def __init__(self):
        self.calls = []

    def prepare(self, name, value, token):
        self.calls.append((name, deepcopy(value), token))
        root = xml.parse(value['xml'])
        principal = root.find('./{'+xml.NS+'}Principals/{'+xml.NS+'}Principal')
        if principal.find('{'+xml.NS+'}RunLevel') is None:
            xml.text(principal, 'RunLevel', 'LeastPrivilege')
        settings = root.find('{'+xml.NS+'}Settings')
        for tag, default in (('DisallowStartIfOnBatteries', True), ('StopIfGoingOnBatteries', True), ('WakeToRun', False)):
            if settings.find('{'+xml.NS+'}'+tag) is None:
                xml.text(settings, tag, default)
        value['xml'] = ET.tostring(root, encoding='unicode')
        return xml.observation(name, value)


def test_selected_binding_converts_without_changing_input_or_other_fields():
    spec, snapshot = legacy()
    before = deepcopy((spec, snapshot))
    scheduler = Preparation()
    converted = owner.normalize_legacy_binding(spec, snapshot, scheduler=scheduler)
    assert converted['principal'] == {'user_id': 'AcmeService', 'logon_type': 'InteractiveToken', 'run_level': 'LeastPrivilege'}
    assert converted['power'] == {'disallow_start_on_batteries': True, 'stop_on_batteries': False, 'wake_to_run': False}
    assert {k: v for k, v in converted.items() if k not in ('principal', 'power')} == {k: v for k, v in spec.items() if k not in ('principal', 'power')}
    assert (spec, snapshot) == before
    assert scheduler.calls and all(call[1]['xml'] == snapshot['value']['xml'] for call in scheduler.calls)


def test_ordinary_render_still_refuses_xml_only_principal():
    spec, snapshot = legacy()
    with pytest.raises(reg.Conflict, match='principal_field_required'):
        xml.render(spec, snapshot, False)


@pytest.mark.parametrize('old,new', [
    ('<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>', '<DisallowStartIfOnBatteries>FALSE</DisallowStartIfOnBatteries>'),
    ('<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>', '<StopIfGoingOnBatteries/>'),
    ('</Settings>', '<WakeToRun>true</WakeToRun><WakeToRun>true</WakeToRun></Settings>'),
    ('</Settings>', '<WakeToRun><Nested/></WakeToRun></Settings>'),
    ('</Settings>', '<WakeToRun extra="true">true</WakeToRun></Settings>'),
    ('</Settings>', '<Enabled>false</Enabled></Settings>'),
    ('</Settings>', '</Settings><Settings/>'),
    ('<Settings>', '<Settings extra="true">'),
    ('<Settings>', '<Settings>mixed'),
    ('</Principal>', '<GroupId>S-1-5-32-545</GroupId></Principal>'),
    ('</Principal>', '<RequiredPrivileges><Privilege>SeBackupPrivilege</Privilege></RequiredPrivileges></Principal>'),
    ('</Principal>', '<RunLevel>garbage</RunLevel></Principal>'),
    ('</Principal>', '<LogonType>S4U</LogonType></Principal>'),
    ('</Principals>', '<Principal/></Principals>'),
    ('<Principals>', '<Principals extra="true">'),
    ('</Principals>', '</Principals><Principals/>'),
])
def test_malformed_or_ambiguous_native_fields_refuse(old, new):
    spec, snapshot = legacy()
    source = snapshot['value']['xml'].replace(old, new)
    # Build the deliberately invalid snapshot identity without a Scheduler read.
    with pytest.raises(reg.Conflict):
        spec, snapshot = bind(spec, source)
        owner.normalize_legacy_binding(spec, snapshot, scheduler=Preparation())


@pytest.mark.parametrize('change', ['identity', 'passthrough', 'field', 'enabled', 'name'])
def test_stale_or_mismatched_evidence_refuses(change):
    spec, snapshot = legacy()
    if change == 'identity':
        snapshot['identity'] = 'task-definition:' + '0' * 64
    elif change == 'passthrough':
        spec['xml_passthrough']['xml'] = spec['xml_passthrough']['xml'].replace('PT7M', 'PT8M')
    elif change == 'field':
        spec['power']['xml'] = spec['power']['xml'].replace('true', 'false')
    elif change == 'enabled':
        spec['enabled'] = True
    else:
        spec['name'] = 'OtherTask'
    with pytest.raises(reg.Conflict):
        owner.normalize_legacy_binding(spec, snapshot, scheduler=Preparation())


def test_missing_defaults_need_preparation_and_power_must_be_observed():
    spec, snapshot = legacy()
    with pytest.raises(reg.Conflict, match='principal_requires_explicit_adapter'):
        owner.normalize_legacy_binding(spec, snapshot)
    class StillMissing:
        def prepare(self, name, value, token):
            return deepcopy(snapshot)
    with pytest.raises(reg.Conflict, match='power_requires_explicit_adapter'):
        owner.normalize_legacy_binding(spec, snapshot, scheduler=StillMissing())


def test_preparation_cannot_change_unrelated_xml_or_typed_flags():
    spec, snapshot = legacy()
    class Changed(Preparation):
        def prepare(self, name, value, token):
            result = super().prepare(name, value, token)
            result['value']['xml'] = result['value']['xml'].replace('PT7M', 'PT8M')
            return result
    with pytest.raises(reg.Conflict, match='preparation_changed_settings'):
        owner.normalize_legacy_binding(spec, snapshot, scheduler=Changed())


@pytest.mark.parametrize('logon', ['Password', 'InteractiveTokenOrPassword'])
def test_password_remains_auth_pending(logon):
    spec, snapshot = legacy()
    spec, snapshot = bind(spec, snapshot['value']['xml'].replace('InteractiveToken', logon))
    with pytest.raises(reg.Conflict, match='auth_pending'):
        owner.normalize_legacy_binding(spec, snapshot, scheduler=Preparation())


@pytest.mark.parametrize('lexical,expected', [('true', True), ('false', False), (' 1\t', True), ('\n0 ', False)])
def test_explicit_power_boolean_lexical_forms(lexical, expected):
    spec, snapshot = legacy()
    source = snapshot['value']['xml'].replace('</Settings>', '<WakeToRun>'+lexical+'</WakeToRun></Settings>')
    source = source.replace('</Principal>', '<RunLevel>LeastPrivilege</RunLevel></Principal>')
    spec, snapshot = bind(spec, source)
    result = owner.normalize_legacy_binding(spec, snapshot)
    assert result['power']['wake_to_run'] is expected


@pytest.mark.parametrize('bad', ['false', 0, None])
def test_manual_non_boolean_power_refuses(bad):
    spec = reg._compile({'request': example_request()})['task_specs'][0]
    spec['power']['wake_to_run'] = bad
    with pytest.raises(reg.Conflict, match='invalid_power_boolean'):
        xml.render(spec, {'state': 'absent'}, False)


def semantics_without_action_metadata(source):
    root = xml.parse(source)
    for tag in ('Actions', 'Data'):
        for node in root.findall('{'+xml.NS+'}'+tag):
            root.remove(node)
    info = root.find('{'+xml.NS+'}RegistrationInfo')
    if info is not None:
        for node in info.findall('{'+xml.NS+'}URI'):
            info.remove(node)
    return xml.canonical(ET.tostring(root, encoding='unicode'))


def test_render_converted_binding_changes_only_action_and_controller_metadata():
    spec, snapshot = legacy()
    scheduler = Preparation()
    converted = owner.normalize_legacy_binding(spec, snapshot, scheduler=scheduler)
    converted['argv'] = ['C:/Acme/final/python.exe', '-I', '-m', 'acme_sync']
    rendered = xml.render(converted, snapshot, False, launcher='C:/Acme/launcher.vbs')
    before = scheduler.prepare(spec['name'], deepcopy(snapshot['value']), 'readback')
    after = scheduler.prepare(spec['name'], deepcopy(rendered), 'readback')
    assert semantics_without_action_metadata(before['value']['xml']) == semantics_without_action_metadata(after['value']['xml'])
    assert rendered['enabled'] is False
    assert '<Command>wscript.exe</Command>' in rendered['xml']
    assert rendered['spec']['xml_passthrough']['xml'] == snapshot['value']['xml']


def test_restore_principal_uses_the_same_owner(monkeypatch):
    spec, snapshot = legacy()
    calls = []
    original = owner._principal
    def observed(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(owner, '_principal', observed)
    owner.normalize_legacy_binding(spec, snapshot, scheduler=Preparation())
    assert calls == [True]
