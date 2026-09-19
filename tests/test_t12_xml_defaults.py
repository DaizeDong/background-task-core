"""Scheduler's optional boolean defaults, using synthetic XML only."""
import pytest
from t12_runtime_support import NS, observation, reg


def value(setting, enabled):
    return {'xml': f'<Task xmlns="{NS}" version="1.2"><Settings>{setting}</Settings>'
                   '<Actions><Exec><Command>C:/Synthetic/python.exe</Command>'
                   '<Arguments>synthetic.py</Arguments></Exec></Actions></Task>',
            'enabled': enabled, 'running': False}


@pytest.mark.parametrize('setting,enabled', [('',True),('<Enabled>true</Enabled>',True),
    ('<Enabled>false</Enabled>',False),('<Enabled>1</Enabled>',True),
    ('<Enabled>0</Enabled>',False),('<Enabled> true </Enabled>',True),
    ('<Enabled>\t1\n</Enabled>',True),('<Enabled>\r\n false \t</Enabled>',False),
    ('<Enabled> 0 </Enabled>',False)])
def test_schema_boolean_default_and_lexical_forms(setting, enabled):
    original=value(setting,enabled)
    result=observation('SyntheticTask',original)
    assert result['value']['xml']==original['xml']
    assert result['value']['enabled'] is enabled


@pytest.mark.parametrize('setting,enabled', [('',False),('<Enabled>true</Enabled>',False),
    ('<Enabled>false</Enabled>',True),('<Enabled>False</Enabled>',False),
    ('<Enabled/>',True),('<Enabled>garbage</Enabled>',True),
    ('<Enabled> </Enabled>',True),('<Enabled>TRUE</Enabled>',True),
    ('<Enabled>1</Enabled>',False),('<Enabled>0</Enabled>',True),
    ('<Enabled>true</Enabled><Enabled>true</Enabled>',True)])
def test_boolean_mismatch_and_invalid_values_refuse(setting, enabled):
    with pytest.raises(reg.Conflict, match='enabled_readback_mismatch'):
        observation('SyntheticTask',value(setting,enabled))


def test_missing_settings_defaults_to_true_and_keeps_bytes():
    original = value('', True)
    original['xml'] = original['xml'].replace('<Settings></Settings>', '')
    assert observation('SyntheticTask', original)['value']['xml'] == original['xml']
