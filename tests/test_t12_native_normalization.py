"""Synthetic native-definition regressions; no registered tasks or payloads."""
from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from t12_runtime_support import NS, example_request, reg
from task_console import runtime_xml as xml


def task(settings='', actions=None, triggers=''):
    return (f'<Task xmlns="{NS}" version="1.4"><Principals><Principal id="Author">'
            '<UserId>S-1-5-21-111111111-222222222-333333333-1001</UserId><LogonType>InteractiveToken</LogonType>'
            '<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>'
            f'<Settings><Enabled>false</Enabled>{settings}</Settings>'
            f'<Triggers>{triggers}</Triggers><Actions>{actions or "<Exec><Command>cmd.exe</Command><Arguments>/c exit 0</Arguments></Exec>"}</Actions></Task>')


def test_duration_and_single_principal_context_are_representation_only():
    a = task('<ExecutionTimeLimit>PT60S</ExecutionTimeLimit>')
    b = a.replace('PT60S', 'PT1M').replace('<Actions>', '<Actions Context="Author">')
    assert xml.canonical(a) == xml.canonical(b)


@pytest.mark.parametrize('a,b', [('PT3600S','PT1H'), ('P1DT60S','PT24H1M'),
                                  ('PT0S','P0D'), ('PT1.5S','PT1.500S')])
def test_fixed_durations(a,b):
    assert xml.canonical(task(f'<ExecutionTimeLimit>{a}</ExecutionTimeLimit>')) == xml.canonical(task(f'<ExecutionTimeLimit>{b}</ExecutionTimeLimit>'))


@pytest.mark.parametrize('old,new', [
    ('<UseUnifiedSchedulingEngine>false</UseUnifiedSchedulingEngine>', '<UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>'),
    ('<Priority>7</Priority>', '<Priority>6</Priority>'),
    ('<Future>PT60S</Future>', '<Future>PT1M</Future>'),
    ('<!--keep-->', '<!--edited-->'),
    ('<ExecutionTimeLimit>P1M</ExecutionTimeLimit>', '<ExecutionTimeLimit>P30D</ExecutionTimeLimit>'),
])
def test_semantic_or_unknown_change_never_compares_equal(old,new):
    assert xml.canonical(task(old)) != xml.canonical(task(new))


def test_unknown_loss_and_comment_loss_refuse():
    for field in ('<FutureSetting>synthetic</FutureSetting>', '<!--keep-->'):
        assert not xml.preserves_xml(task(field),task())


def test_mixed_unknown_content_and_processing_instruction_cannot_disappear():
    for field in ('<Unknown>before<A/>after</Unknown>', '<?synthetic keep?>'):
        assert not xml.preserves_xml(task(field),task())
    assert not xml.preserves_xml(task('<Unknown>before<A/>after</Unknown>'),task('<Unknown>changed<A/>after</Unknown>'))
    assert xml.canonical(task('<Unknown> <A/> </Unknown>')) != xml.canonical(task('<Unknown><A/></Unknown>'))
    assert not xml.preserves_xml(task('<Unknown> <A/> </Unknown>'),task('<Unknown><A/></Unknown>'))


@pytest.mark.parametrize('prefix,suffix', [('<!--keep-->',''), ('','<!--keep-->'), ('<?synthetic keep?>','')])
def test_document_metadata_outside_task_refuses_instead_of_dropping(prefix,suffix):
    with pytest.raises(reg.Conflict,match='outside_task_metadata_requires_preservation'):
        xml.parse(prefix+task()+suffix)


def test_long_duration_never_rounds_to_another_deadline():
    a='PT123456789012345678901234567890.0001S'
    b='PT123456789012345678901234567890.0002S'
    assert xml.canonical(task(f'<ExecutionTimeLimit>{a}</ExecutionTimeLimit>')) != xml.canonical(task(f'<ExecutionTimeLimit>{b}</ExecutionTimeLimit>'))


def test_ordered_actions_triggers_and_unknown_children():
    a='<Exec><Command>first.exe</Command></Exec><Exec><Command>second.exe</Command></Exec>'
    b='<TimeTrigger id="one"><StartBoundary>2030-01-01T00:00:00Z</StartBoundary></TimeTrigger><TimeTrigger id="two"><StartBoundary>2031-01-01T00:00:00Z</StartBoundary></TimeTrigger>'
    source=task(actions=a,triggers=b)
    for container in ('Actions','Triggers'):
        root=xml.parse(source); target=root.find('{'+NS+'}'+container); target[:]=reversed(list(target))
        assert not xml.preserves_xml(source,ET.tostring(root,encoding='unicode'))
    assert not xml.preserves_xml(task('<Unknown><A/><B/></Unknown>'),task('<Unknown><B/><A/></Unknown>'))


def test_fresh_engine_is_explicit_existing_engine_is_preserved():
    spec=reg._compile({'request':example_request()})['task_specs'][0]
    result=xml.render(spec,{'state':'absent'},False)
    assert '<UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>' in result['xml']
    result['xml']=result['xml'].replace('<UseUnifiedSchedulingEngine>true','<UseUnifiedSchedulingEngine>false')
    old={'state':'present','value':result}
    assert '<UseUnifiedSchedulingEngine>false</UseUnifiedSchedulingEngine>' in xml.render(spec,old,False)['xml']


def test_renderer_preserves_multiple_actions_when_primary_is_unchanged():
    spec=reg._compile({'request':example_request()})['task_specs'][0]
    original=xml.render(spec,{'state':'absent'},False)
    tree=xml.parse(original['xml']); actions=tree.find('{'+NS+'}Actions')
    actions.append(deepcopy(actions[0]))
    actions[1].find('{'+NS+'}Arguments').text='--second-synthetic-action'
    original['xml']=ET.tostring(tree,encoding='unicode')
    actual=xml.render(spec,{'state':'present','value':original},False)
    assert ET.tostring(xml.parse(actual['xml']).find('{'+NS+'}Actions')) == ET.tostring(actions)
    spec['argv'].append('--changed')
    with pytest.raises(reg.Conflict,match='multiple_actions_require_preservation'):
        xml.render(spec,{'state':'present','value':original},False)


def test_candidate_match_is_scoped_to_prepared_snapshots():
    a=xml.observation('SyntheticTask',{'xml':task(), 'enabled':False,'running':False})
    b=deepcopy(a); b['value']['xml']=b['value']['xml'].replace('<Actions>','<Actions Context="Author">')
    a['candidate_identity']=b['candidate_identity']='task-definition:'+'a'*64
    assert not xml.same_candidate(a,b)
    a['prepared']=True
    assert xml.same_candidate(a,b)
    b['candidate_identity']='task-definition:'+'b'*64
    assert not xml.same_candidate(a,b)
