"""Creation checks use the real planner with synthetic owned transports."""
from copy import deepcopy

import pytest

from test_registration_transaction import api, rig
from tools.make_fixtures import add_creation_candidate, legacy_creation_xml


def creation_rig(tmp_path):
    runtime, bundle, _ = rig(tmp_path)
    original, candidate = add_creation_candidate(bundle['request'])
    binding = bundle['request']['bindings']['tasks'][candidate]
    bundle['ownership'][candidate] = dict(bundle['ownership'][original], name=binding['name'], identity=None)
    bundle['active_xml'][candidate] = None
    return runtime, bundle, original, candidate


def test_check_returns_existing_identity_without_running_or_registering(tmp_path):
    runtime, bundle, original, candidate = creation_rig(tmp_path)
    result = api().creation_check(runtime, candidate)
    assert result['decision'] == 'reuse'
    assert result['matches'][0]['task_id'] == original
    assert result['matches'][0]['enabled'] is False
    assert not runtime.scheduler.calls
    assert not runtime.journal.items


def test_new_name_cannot_bypass_equivalent_payload_check(tmp_path):
    runtime, bundle, original, candidate = creation_rig(tmp_path)
    with pytest.raises(api().Conflict, match='equivalent_task_exists'):
        api().build_plan(runtime, [candidate], migrate=True)
    assert not runtime.scheduler.calls and not runtime.journal.items


def test_frequency_change_reuses_existing_action_instead_of_duplicate(tmp_path):
    runtime, bundle, original, candidate = creation_rig(tmp_path)
    binding = bundle['request']['bindings']['tasks'][candidate]
    binding['trigger'] = {'type': 'interval', 'minutes': 15}
    assert api().creation_check(runtime, candidate)['decision'] == 'reuse'
    with pytest.raises(api().Conflict, match='equivalent_task_exists'):
        api().build_plan(runtime, [candidate], migrate=True)


def test_different_target_argument_is_a_distinct_automation(tmp_path):
    runtime, bundle, original, candidate = creation_rig(tmp_path)
    binding = bundle['request']['bindings']['tasks'][candidate]
    binding['argv'] += ['--destination', 'other-synthetic-destination']
    assert api().creation_check(runtime, candidate)['decision'] == 'create'
    assert api().build_plan(runtime, [candidate], migrate=True)['applicable']


def test_unknown_existing_task_is_not_treated_as_absence(tmp_path):
    runtime, bundle, original, candidate = creation_rig(tmp_path)
    runtime.scheduler.items[bundle['ownership'][original]['name']] = {'state': 'unknown'}
    with pytest.raises(api().Conflict, match='query_unknown'):
        api().creation_check(runtime, candidate)


def test_existing_task_migration_does_not_require_new_creation_review(tmp_path):
    runtime, bundle, original, candidate = creation_rig(tmp_path)
    plan = api().build_plan(runtime, [original], migrate=True)
    assert plan['applicable']


@pytest.mark.parametrize('user_id,decision', [('AcmeService', 'reuse'), ('AcmeOther', 'create')])
def test_legacy_observation_reads_scope_from_actual_xml(tmp_path, user_id, decision):
    runtime, bundle, original, candidate = creation_rig(tmp_path)
    existing = runtime.scheduler.items[bundle['ownership'][original]['name']]['value']
    existing['spec'] = {'argv': existing['spec']['argv']}
    existing['xml'] = legacy_creation_xml(user_id)
    assert api().creation_check(runtime, candidate)['decision'] == decision
    assert not runtime.scheduler.calls


def test_legacy_observation_missing_principal_refuses_creation(tmp_path):
    import xml.etree.ElementTree as ET
    from scripts.task_console.contracts import ContractError
    runtime, bundle, original, candidate = creation_rig(tmp_path)
    existing = runtime.scheduler.items[bundle['ownership'][original]['name']]['value']
    existing['spec'] = {'argv': existing['spec']['argv']}
    xml = ET.fromstring(legacy_creation_xml())
    xml.remove(xml[0])
    existing['xml'] = ET.tostring(xml, encoding='unicode')
    with pytest.raises(ContractError, match='creation_action_unknown'):
        api().creation_check(runtime, candidate)


def test_legacy_default_run_level_still_matches(tmp_path):
    import xml.etree.ElementTree as ET
    runtime, bundle, original, candidate = creation_rig(tmp_path)
    existing = runtime.scheduler.items[bundle['ownership'][original]['name']]['value']
    existing['spec'] = {'argv': existing['spec']['argv']}
    xml = ET.fromstring(legacy_creation_xml())
    principal = xml[0][0]
    principal.remove(principal[-1])
    existing['xml'] = ET.tostring(xml, encoding='unicode')
    assert api().creation_check(runtime, candidate)['decision'] == 'reuse'
