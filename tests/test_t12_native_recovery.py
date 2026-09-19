"""Durable candidate recovery against synthetic storage / actual COM prepare."""
from copy import deepcopy
import os

import pytest

import t12_runtime_support as fixture
from t12_runtime_support import configured, setup, reg, read_json, fs, encode
from t12_native_shape_support import NativeShapeScheduler
from task_console.runtime import create_runtime, RuntimeConfig

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows in-memory COM parser')


@pytest.fixture
def native_rig(tmp_path, monkeypatch):
    original = fixture.example_request
    def request():
        value = original()
        binding = next(iter(value['bindings']['tasks'].values()))
        binding['principal'] = {'user_id':'S-1-5-21-111111111-222222222-333333333-1001',
                                'logon_type':'InteractiveToken', 'run_level':'LeastPrivilege'}
        binding['timeout_seconds'] = 60
        binding['trigger'] = []
        value['baseline']['tasks'][0].update({k:deepcopy(binding[k]) for k in value['baseline']['tasks'][0]})
        return value
    monkeypatch.setattr(fixture,'example_request',request)
    _, task_id = setup(tmp_path,absent=True)
    config = RuntimeConfig.read(tmp_path/'private/config.json',private_root=tmp_path/'private',
                                state_root=tmp_path/'state',vault_root=tmp_path/'vault')
    def fresh():
        # The default protector is same-user DPAPI; JSON has no injection knob.
        return create_runtime(config,transport=NativeShapeScheduler(tmp_path))
    return fresh,task_id,tmp_path


class Death(BaseException):
    pass


def test_plan_exposes_fresh_native_engine_choice(native_rig):
    fresh,task_id,_ = native_rig
    plan = reg.build_plan(fresh(),[task_id],migrate=True)
    assert plan['changes']['tasks'][0]['scheduler_semantics'] == {
        'xml_version':'1.4','use_unified_scheduling_engine':True}


def test_publish_before_receipt_is_recoverable_and_preserves_effective_input(native_rig):
    fresh,task_id,root = native_rig
    runtime=fresh(); original=(root/'private/desired.json').read_bytes()
    operation=runtime.scheduler.transport
    def die(operation_name,request):
        result=operation(operation_name,request)
        if operation_name=='publish':
            raise Death()
        return result
    die.concrete=True; die.native_normalization=True
    runtime.scheduler.transport=die
    plan=reg.build_plan(runtime,[task_id],migrate=True)
    with pytest.raises(Death):
        reg.apply(plan,plan['input_revision'],runtime=runtime)
    runtime=fresh(); journal=runtime.journal.list()[0]
    actual=runtime.scheduler.read('AcmeSync')
    prepared=runtime.vault.get(journal['steps'][0]['after'])
    assert actual['value']['xml'] != prepared['value']['xml']
    assert reg._same('scheduler',actual,prepared)
    result=reg.recover(journal['transaction_id'],runtime=runtime)
    assert result['status']=='rolled_back' and result['cleaned'],result
    assert fresh().scheduler.read('AcmeSync')['state']=='absent'
    assert (root/'private/desired.json').read_bytes()==original
    assert fresh().load()['request']['machine']['migrated_tasks']==[]


def test_register_receipt_reopen_and_retire(native_rig):
    fresh,task_id,root=native_rig
    original=(root/'private/desired.json').read_bytes()
    runtime=fresh(); plan=reg.build_plan(runtime,[task_id],migrate=True)
    result=reg.apply(plan,plan['input_revision'],runtime=runtime)
    assert result['status']=='committed' and result['cleaned'],result
    runtime=fresh()
    assert runtime.load()['request']['machine']['migrated_tasks']==[task_id]
    assert runtime.scheduler.read('AcmeSync')['value']['enabled'] is False
    plan=reg.build_plan(runtime,[task_id],operation='retire',reason='Synthetic test complete')
    result=reg.apply(plan,plan['input_revision'],runtime=runtime)
    assert result['status']=='committed' and result['cleaned'],result
    assert fresh().scheduler.read('AcmeSync')['state']=='absent'
    assert (root/'private/desired.json').read_bytes()==original


def test_raw_observed_cas_rejects_an_intervening_edit(native_rig):
    fresh,task_id,root=native_rig
    runtime=fresh(); spec=reg._compile(runtime.load())['task_specs'][0]
    value=runtime.render(spec,{'state':'absent'},False)
    prepared=runtime.scheduler.prepare('AcmeSync',value,'b'*32+':0')
    runtime.scheduler.publish('AcmeSync',{'state':'absent'},prepared)
    before=runtime.scheduler.read('AcmeSync')
    tasks=read_json(root/'synthetic-scheduler.json')
    tasks['AcmeSync']['value']['xml']=tasks['AcmeSync']['value']['xml'].replace('<Settings>','<Settings><Priority>4</Priority>')
    from task_console.runtime_xml import observation
    tasks['AcmeSync']=observation('AcmeSync',tasks['AcmeSync']['value'])
    fs.atomic_replace(root/'synthetic-scheduler.json',encode(tasks))
    with pytest.raises(reg.Conflict,match='scheduler_changed'):
        runtime.scheduler.publish('AcmeSync',before,{'state':'absent'})
    assert read_json(root/'synthetic-scheduler.json')==tasks


def test_raw_observed_cas_rejects_even_a_representation_edit(native_rig):
    fresh,task_id,root=native_rig
    runtime=fresh(); spec=reg._compile(runtime.load())['task_specs'][0]
    value=runtime.render(spec,{'state':'absent'},False)
    prepared=runtime.scheduler.prepare('AcmeSync',value,'c'*32+':0')
    runtime.scheduler.publish('AcmeSync',{'state':'absent'},prepared)
    before=runtime.scheduler.read('AcmeSync')
    tasks=read_json(root/'synthetic-scheduler.json')
    tasks['AcmeSync']['value']['xml']=tasks['AcmeSync']['value']['xml'].replace('<Settings>','<Settings>\n    ')
    fs.atomic_replace(root/'synthetic-scheduler.json',encode(tasks))
    with pytest.raises(reg.Conflict,match='scheduler_changed'):
        runtime.scheduler.publish('AcmeSync',before,{'state':'absent'})
    assert read_json(root/'synthetic-scheduler.json')==tasks
