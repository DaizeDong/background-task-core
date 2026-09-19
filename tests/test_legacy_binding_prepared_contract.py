"""Regression cases for supplied prepared response consistency."""
import pytest

from test_legacy_binding_normalization import legacy, Preparation
from task_console import export_restore as owner, runtime_xml as xml
from task_console.registration import Conflict


@pytest.mark.parametrize('corruption', ['stale_identity', 'wrong_name_identity', 'stale_candidate_identity'])
def test_prepared_identity_must_match_name_and_xml(corruption):
    spec, snapshot = legacy()
    class Forged(Preparation):
        def prepare(self, name, value, token):
            prepared = super().prepare(name, value, token)
            if corruption == 'stale_identity':
                prepared['identity'] = 'task-definition:' + '0' * 64
            elif corruption == 'stale_candidate_identity':
                prepared['candidate_identity'] = 'task-definition:' + '0' * 64
            else:
                prepared = xml.observation('OtherSyntheticTask', prepared['value'])
            return prepared
    with pytest.raises(Conflict):
        owner.normalize_legacy_binding(spec, snapshot, scheduler=Forged())


@pytest.mark.parametrize('addition', [
    '<GroupId>S-1-5-32-545</GroupId>',
    '<RunLevel>HighestAvailable</RunLevel>',
])
def test_power_preparation_must_validate_its_principal(addition):
    spec, snapshot = legacy()
    class ForgedSecond(Preparation):
        def prepare(self, name, value, token):
            prepared = super().prepare(name, value, token)
            if len(self.calls) == 2:
                prepared['value']['xml'] = prepared['value']['xml'].replace(
                    '</Principal>', addition + '</Principal>')
                # Deliberately use a correct identity: structural validation is
                # independently necessary after the identity check is repaired.
                prepared = xml.observation(name, prepared['value'])
            return prepared
    with pytest.raises(Conflict):
        owner.normalize_legacy_binding(spec, snapshot, scheduler=ForgedSecond())


def test_second_preparation_cannot_change_selected_run_level():
    spec, snapshot = legacy()
    class ChangedSecond(Preparation):
        def prepare(self, name, value, token):
            prepared = super().prepare(name, value, token)
            if len(self.calls) == 2:
                prepared['value']['xml'] = prepared['value']['xml'].replace('LeastPrivilege', 'HighestAvailable')
                prepared = xml.observation(name, prepared['value'])
            return prepared
    with pytest.raises(Conflict):
        owner.normalize_legacy_binding(spec, snapshot, scheduler=ChangedSecond())
