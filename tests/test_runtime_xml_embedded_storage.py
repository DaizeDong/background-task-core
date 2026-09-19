"""Exact embedded metadata survives sorted durable storage and preparation."""
import base64
from copy import deepcopy
import json
import xml.etree.ElementTree as ET

import pytest

from task_console import runtime_xml as owner
from task_console.runtime_storage import encode, decode


def sample(*, encoded=True):
    spec = {'enabled': False, 'argv': ['synthetic.exe'],
            'z_nested': {'z': 9, 'a': 1}, 'a_nested': [{'z': 3, 'a': True}]}
    payload = {'original_data': 'opaque preserved text', 'spec': spec, 'extra': [3, 2, 1]}
    data = (owner.MARKER + base64.b64encode(json.dumps(payload, indent=3).encode()).decode()
            if encoded else 'opaque non-marker text')
    root = ET.Element('{' + owner.NS + '}Task')
    owner.text(owner.node(root, 'Settings'), 'Enabled', False)
    owner.text(owner.node(root, 'Actions'), 'Exec', 'synthetic')
    owner.text(root, 'Data', data)
    return {'xml': ET.tostring(root, encoding='unicode'), 'spec': deepcopy(spec),
            'enabled': False, 'running': False}


def data(value):
    return owner.parse(value['xml']).find('{' + owner.NS + '}Data').text


def test_sorted_storage_keeps_exact_metadata_and_candidate_identity():
    value = sample()
    snapshot = owner.observation('SyntheticStored', value)
    saved = decode(encode(snapshot))
    assert list(saved['value']['spec']) != list(value['spec'])
    assert saved['value']['spec'] == value['spec']
    normalized = owner.normalize(saved['value'])
    assert data(normalized) == data(value)
    assert owner.preserves_xml(value['xml'], normalized['xml'])
    assert owner.observation('SyntheticStored', normalized)['identity'] == snapshot['identity']
    assert owner.normalize(normalized) == normalized


@pytest.mark.parametrize('change', ['argv', 'nested', 'boolean_to_integer', 'integer_to_float', 'enabled'])
def test_real_spec_or_type_change_still_rewrites_metadata(change):
    value = sample()
    changed = decode(encode(value))
    if change == 'argv':
        changed['spec']['argv'] = ['different-synthetic.exe']
    elif change == 'nested':
        changed['spec']['z_nested']['a'] = 2
    elif change == 'boolean_to_integer':
        changed['spec']['a_nested'][0]['a'] = 1
    elif change == 'integer_to_float':
        changed['spec']['z_nested']['a'] = 1.0
    else:
        changed['enabled'] = True
    normalized = owner.normalize(changed)
    assert data(normalized) != data(value)
    assert not owner.preserves_xml(value['xml'], normalized['xml'])
    result = owner.embedded(owner.parse(normalized['xml']))
    assert json.dumps(result['spec'], sort_keys=True) == json.dumps(normalized['spec'], sort_keys=True)
    assert result['original_data'] == 'opaque preserved text'
    assert result['extra'] == [3, 2, 1]
    assert owner.normalize(normalized) == normalized


def test_unmanaged_data_remains_opaque():
    value = sample(encoded=False)
    assert data(owner.normalize(decode(encode(value)))) == data(value)


def test_invalid_embedded_payload_still_refuses():
    value = sample()
    root = owner.parse(value['xml'])
    owner.node(root, 'Data').text = owner.MARKER + 'invalid base64!'
    value['xml'] = ET.tostring(root, encoding='unicode')
    with pytest.raises(owner.Conflict, match='invalid_embedded_spec'):
        owner.normalize(value)
