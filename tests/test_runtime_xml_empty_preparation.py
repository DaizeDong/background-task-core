"""Constant synthetic COM shapes; no native transport or private snapshots."""
from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from t12_runtime_support import NS
from task_console import runtime_xml as xml


def task(trigger):
    return (f'<Task xmlns="{NS}" version="1.4">'
            '<Principals><Principal id="Author"><UserId>AcmeService</UserId>'
            '<LogonType>InteractiveToken</LogonType></Principal></Principals>'
            '<Settings><Enabled>false</Enabled><ExecutionTimeLimit>PT7M</ExecutionTimeLimit>'
            '<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>'
            '<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries></Settings>'
            f'<Triggers>{trigger}</Triggers>'
            '<Actions><Exec><Command>synthetic.exe</Command><Arguments>--example</Arguments>'
            '</Exec></Actions></Task>')


SHAPES = (
    ('<CalendarTrigger><ScheduleByWeek><WeeksInterval>2</WeeksInterval>'
     '<DaysOfWeek><Monday/><Friday/></DaysOfWeek></ScheduleByWeek></CalendarTrigger>',
     '<CalendarTrigger><Enabled>true</Enabled><ScheduleByWeek>'
     '<DaysOfWeek><Monday/><Friday/></DaysOfWeek><WeeksInterval>2</WeeksInterval>'
     '</ScheduleByWeek></CalendarTrigger>'),
    ('<CalendarTrigger><ScheduleByMonth><Months><January/><March/></Months>'
     '<DaysOfMonth><Day>12</Day><Day>25</Day></DaysOfMonth></ScheduleByMonth></CalendarTrigger>',
     '<CalendarTrigger><Enabled>true</Enabled><ScheduleByMonth>'
     '<DaysOfMonth><Day>12</Day><Day>25</Day></DaysOfMonth>'
     '<Months><January/><March/></Months></ScheduleByMonth></CalendarTrigger>'),
    ('<LogonTrigger/>', '<LogonTrigger>\n      <Enabled>true</Enabled>\n    </LogonTrigger>'),
)


@pytest.mark.parametrize('before,after', SHAPES, ids=['weekly-fields', 'monthly-fields', 'empty-logon'])
def test_com_representation_preserves_explicit_input(before, after):
    source = task(before)
    prepared = task(after).replace('</Principal>', '<RunLevel>LeastPrivilege</RunLevel></Principal>')
    prepared = prepared.replace('</Settings>', '<WakeToRun>false</WakeToRun></Settings>')
    assert xml.preserves_xml(source, prepared)
    # Preparation comparison does not redefine canonical identity.
    assert xml.canonical(source) != xml.canonical(prepared)


@pytest.mark.parametrize('before,after', [
    ('<LogonTrigger> \n </LogonTrigger>', '<LogonTrigger>\n<Enabled>true</Enabled>\n</LogonTrigger>'),
    ('<LogonTrigger/>', '<LogonTrigger><Enabled>true</Enabled></LogonTrigger>'),
    ('<LogonTrigger/>', '<LogonTrigger>\n </LogonTrigger>'),
])
def test_empty_known_container_indentation_is_not_an_explicit_leaf(before, after):
    assert xml.preserves_xml(task(before), task(after))


@pytest.mark.parametrize('old,new', [
    ('<Enabled>false</Enabled>', '<Enabled>true</Enabled>'),
    ('PT7M', 'PT8M'),
    ('<DisallowStartIfOnBatteries>true', '<DisallowStartIfOnBatteries>false'),
    ('<StopIfGoingOnBatteries>false', '<StopIfGoingOnBatteries>true'),
    ('AcmeService', 'OtherSyntheticService'),
    ('InteractiveToken', 'S4U'),
    ('id="Author"', 'id="Other"'),
    ('synthetic.exe', 'changed.exe'),
    ('--example', '--changed'),
    ('WeeksInterval>2<', 'WeeksInterval>3<'),
    ('<Monday/>', '<Tuesday/>'),
])
def test_explicit_values_and_attributes_survive_preparation(old, new):
    source = task(SHAPES[0][0])
    prepared = task(SHAPES[0][1])
    assert old in prepared
    assert not xml.preserves_xml(source, prepared.replace(old, new))


@pytest.mark.parametrize('before,after', [
    ('<LogonTrigger><Enabled/></LogonTrigger>', '<LogonTrigger><Enabled>true</Enabled></LogonTrigger>'),
    ('<LogonTrigger><Enabled> </Enabled></LogonTrigger>', '<LogonTrigger><Enabled/></LogonTrigger>'),
    ('<LogonTrigger>explicit</LogonTrigger>', '<LogonTrigger><Enabled>true</Enabled></LogonTrigger>'),
    ('<LogonTrigger/>', '<LogonTrigger>introduced<Enabled>true</Enabled></LogonTrigger>'),
    ('<LogonTrigger><Future/></LogonTrigger>', '<LogonTrigger><Future><Nested/></Future></LogonTrigger>'),
    ('<LogonTrigger><Future> </Future></LogonTrigger>', '<LogonTrigger><Future/></LogonTrigger>'),
    ('<LogonTrigger><Future> <A/> </Future></LogonTrigger>', '<LogonTrigger><Future><A/></Future></LogonTrigger>'),
    ('<LogonTrigger><Future>before<A/>after</Future></LogonTrigger>', '<LogonTrigger><Future>before<A/>changed</Future></LogonTrigger>'),
    ('<LogonTrigger><Future flag="keep"/></LogonTrigger>', '<LogonTrigger><Future/></LogonTrigger>'),
    ('<LogonTrigger><!--keep--></LogonTrigger>', '<LogonTrigger/>'),
    ('<LogonTrigger><?synthetic keep?></LogonTrigger>', '<LogonTrigger/>'),
])
def test_empty_leaf_unknown_and_mixed_content_remain_explicit(before, after):
    assert not xml.preserves_xml(task(before), task(after))


@pytest.mark.parametrize('container,children', [
    ('Actions', '<Exec><Command>one.exe</Command></Exec><Exec><Command>two.exe</Command></Exec>'),
    ('Triggers', '<LogonTrigger id="one"/><LogonTrigger id="two"/>'),
    ('Principals', '<Principal id="one"/><Principal id="two"/>'),
    ('DaysOfWeek', '<Monday/><Friday/>'),
    ('Months', '<January/><March/>'),
    ('DaysOfMonth', '<Day>12</Day><Day>25</Day>'),
])
def test_collection_order_and_empty_cardinality_are_preserved(container, children):
    source = f'<Task xmlns="{NS}"><{container}>{children}</{container}></Task>'
    root = xml.parse(source)
    root[0][:] = reversed(list(root[0]))
    assert not xml.preserves_xml(source, ET.tostring(root, encoding='unicode'))
    empty = f'<Task xmlns="{NS}"><{container}/></Task>'
    assert not xml.preserves_xml(empty, source)


@pytest.mark.parametrize('extra', ['<Future/>', '<!--keep-->', '<?synthetic keep?>'])
def test_schedule_reordering_does_not_reorder_unknown_or_metadata(extra):
    before = SHAPES[0][0].replace('<WeeksInterval>', extra + '<WeeksInterval>')
    after = SHAPES[0][1].replace('</ScheduleByWeek>', extra + '</ScheduleByWeek>')
    assert not xml.preserves_xml(task(before), task(after))


def test_raw_observation_and_prepared_identity_contracts_are_unchanged():
    source = task(SHAPES[2][0])
    value = {'xml': source, 'spec': {'argv': ['synthetic.exe'], 'enabled': False},
             'enabled': False, 'running': False}
    observed = xml.observation('SyntheticTask', value)
    assert observed['value']['xml'] == source
    other = deepcopy(observed)
    other['value']['xml'] = task(SHAPES[2][1])
    observed['candidate_identity'] = other['candidate_identity'] = 'task-definition:' + 'a' * 64
    assert not xml.same_candidate(observed, other)
    observed['prepared'] = True
    assert xml.same_candidate(observed, other)
    other['candidate_identity'] = 'task-definition:' + 'b' * 64
    assert not xml.same_candidate(observed, other)


@pytest.mark.parametrize('changed', [False, True])
def test_read_still_prepares_and_validates_through_injected_transport(changed):
    from task_console.registration import Conflict
    from task_console.scheduler_windows import WindowsScheduler

    source = task(SHAPES[2][0])
    value = {'xml': source, 'spec': {'argv': ['synthetic.exe'], 'enabled': False},
             'enabled': False, 'running': False}
    calls = []

    class Transport:
        concrete = native_normalization = True

        def __call__(self, operation, request):
            calls.append(operation)
            assert operation in ('query', 'prepare')
            result = deepcopy(value if operation == 'query' else request['value'])
            if operation == 'prepare':
                result['xml'] = task(SHAPES[2][1])
                if changed:
                    result['xml'] = result['xml'].replace('PT7M', 'PT8M')
            return {'ok': True, 'snapshot': xml.observation(request['TaskName'], result)}

    scheduler = WindowsScheduler(Transport())
    if changed:
        with pytest.raises(Conflict, match='preparation_changed_settings'):
            scheduler.read('SyntheticTask')
    else:
        result = scheduler.read('SyntheticTask')
        assert result['value']['xml'] == source
        assert 'prepared' not in result
        assert result['candidate_identity'] != result['identity']
    assert calls == ['query', 'prepare']
