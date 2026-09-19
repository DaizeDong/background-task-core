"""Real in-memory COM parser; no folders, task registration or payload starts."""
from copy import deepcopy
import os

import pytest

from test_t12_native_normalization import task
from task_console.runtime_windows import COMTransport
from task_console.scheduler_windows import WindowsScheduler
from task_console.runtime_xml import canonical, same_candidate
from task_console.registration import Conflict

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows COM parser')


def prepare(source):
    value={'xml':source, 'spec':{'argv':['cmd.exe','/c','exit','0'], 'enabled':False},
           'enabled':False, 'running':False}
    return WindowsScheduler(COMTransport()).prepare('SyntheticTask',value,'a'*32+':0')


def test_root_trace_defaults_duration_and_context_with_explicit_engine():
    lean=task('<UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine><ExecutionTimeLimit>PT1M</ExecutionTimeLimit>')
    rich=lean.replace('PT1M','PT60S').replace('<Actions>','<Actions Context="Author">')
    rich=rich.replace('</Settings>', '<AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>false</StartWhenAvailable>'
                      '<RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable><AllowStartOnDemand>true</AllowStartOnDemand>'
                      '<Hidden>false</Hidden><RunOnlyIfIdle>false</RunOnlyIfIdle><Priority>7</Priority></Settings>')
    assert same_candidate(prepare(lean),prepare(rich))


def test_native_engine_false_and_true_are_not_equivalent():
    a=prepare(task('<UseUnifiedSchedulingEngine>false</UseUnifiedSchedulingEngine>'))
    b=prepare(task('<UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>'))
    assert not same_candidate(a,b)


@pytest.mark.parametrize('version', ['1.2','1.3','1.4'])
def test_native_preparation_does_not_upgrade_xml_version(version):
    source=task().replace('version="1.4"','version="'+version+'"')
    assert 'version="'+version+'"' in prepare(source)['value']['xml']


@pytest.mark.parametrize('field', ['<FutureSetting>synthetic</FutureSetting>', '<!--synthetic comment-->'])
def test_native_loss_or_unsupported_xml_fails_before_registration(field):
    source=task(field)
    with pytest.raises(Conflict):
        prepare(source)
    assert field in source


@pytest.mark.parametrize('field', ['<Priority>4</Priority>', '<StartWhenAvailable>true</StartWhenAvailable>',
    '<AllowHardTerminate>false</AllowHardTerminate>', '<ExecutionTimeLimit>PT90S</ExecutionTimeLimit>',
    '<IdleSettings><Duration>PT15M</Duration><WaitTimeout>PT2H</WaitTimeout><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>true</RestartOnIdle></IdleSettings>'])
def test_explicit_native_settings_are_preserved_and_distinct(field):
    a=prepare(task(field))
    assert not same_candidate(a,prepare(task()))


def test_native_multiple_actions_and_triggers_keep_order():
    actions='<Exec><Command>cmd.exe</Command><Arguments>/c exit 0</Arguments></Exec><Exec><Command>cmd.exe</Command><Arguments>/c exit 1</Arguments></Exec>'
    triggers='<TimeTrigger id="one"><StartBoundary>2030-01-01T00:00:00Z</StartBoundary></TimeTrigger><TimeTrigger id="two"><StartBoundary>2031-01-01T00:00:00Z</StartBoundary></TimeTrigger>'
    source=task(actions=actions,triggers=triggers)
    result=prepare(source)['value']['xml']
    assert result.index('/c exit 0') < result.index('/c exit 1')
    assert result.index('id="one"') < result.index('id="two"')
