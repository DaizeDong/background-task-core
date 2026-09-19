"""Synthetic Scheduler objects and process clients; no live COM or payloads."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
OWNER = Path(__file__).resolve().parents[1] / 'scripts/task_console/task_context.ps1'


def module():
    assert OWNER.exists(), 'shared Scheduler context owner is missing'
    return importlib.import_module('task_console.task_context')


def test_retained_identity_wins_without_process_or_new_instance():
    m = module()
    env = {'TASK_RUN_ID': 'synthetic-original', 'TASK_SCHEDULER_NAME': 'AcmeRefresh'}
    def forbidden(*a, **kw):
        raise AssertionError('retained delivery ID must not query Scheduler')
    assert m.resolve_run_id('AcmeRefresh', environ=env, process=forbidden) == 'synthetic-original'
    assert env['SCHEDULE_RUN_ID'] == 'synthetic-original'


@pytest.mark.parametrize('env,explicit', [
    ({'TASK_RUN_ID':'one','SCHEDULE_RUN_ID':'two'}, None),
    ({'TASK_RUN_ID':'one'}, 'two'), ({}, None),
])
def test_conflicting_or_missing_identity_fails(env, explicit):
    m = module()
    with pytest.raises(ValueError):
        m.resolve_run_id('AcmeRefresh', explicit=explicit, environ=env)


def test_manual_explicit_and_dry_run():
    m = module()
    assert m.resolve_run_id('AcmeRefresh', explicit='manual-example', environ={}) == 'manual-example'
    assert m.resolve_run_id('AcmeRefresh', dry_run=True, environ={}) is None


def test_initial_scheduler_identity_uses_bounded_argv_process():
    m = module()
    from types import SimpleNamespace
    calls=[]
    def process(argv, **kwargs):
        calls.append((argv,kwargs))
        return SimpleNamespace(returncode=0,stdout=json.dumps({'task_name':'AcmeRefresh',
            'run_id':'scheduler:AcmeRefresh:{00000000-0000-0000-0000-000000000001}'}))
    env={'TASK_SCHEDULER_NAME':'AcmeRefresh','SystemRoot':r'C:\Windows'}
    result=m.resolve_run_id('AcmeRefresh',environ=env,process=process)
    assert result==env['TASK_RUN_ID']==env['SCHEDULE_RUN_ID']
    assert len(calls)==1 and calls[0][0][-2:]==['-QueryTaskName','AcmeRefresh']
    assert '-NoProfile' in calls[0][0] and calls[0][1]['timeout']==15
    assert 'shell' not in calls[0][1]


def test_scheduler_failure_and_foreign_response_are_visible():
    m=module()
    from types import SimpleNamespace
    for response in (SimpleNamespace(returncode=1,stdout=''),
                     SimpleNamespace(returncode=0,stdout='{"task_name":"Other","run_id":"random"}')):
        with pytest.raises(ValueError,match='task-context'):
            m.resolve_run_id('AcmeRefresh',scheduled=True,environ={},process=lambda *a,**k:response)


def ps_probe(tmp_path, body):
    assert OWNER.exists(), 'shared Scheduler context owner is missing'
    script = tmp_path / 'probe.ps1'
    script.write_text("$ErrorActionPreference='Stop'\n. '" + str(OWNER).replace("'", "''") + "'\n" + body, encoding='utf-8-sig')
    env = {k:v for k,v in os.environ.items() if k not in ('TASK_RUN_ID','SCHEDULE_RUN_ID','TASK_DEADLINE_UTC','TASK_SCHEDULER_NAME')}
    return subprocess.run([PS,'-NoProfile','-File',str(script)], env=env, capture_output=True, text=True, timeout=15)


FAKE = r'''
$script:count = 1
$script:path = '\AcmeRefresh'
$script:limit = 'PT1H'
function New-FakeService {
  $instance = [pscustomobject]@{InstanceGuid='{00000000-0000-0000-0000-000000000001}';Path=$script:path;State=4}
  $instances = [pscustomobject]@{Count=$script:count;Value=$instance}
  $instances | Add-Member ScriptMethod Item {param($index) if($index -ne 1){throw 'index'}; $this.Value}
  $task = [pscustomobject]@{Name='AcmeRefresh';Path='\AcmeRefresh';LastRunTime=[datetime]'2026-01-01T00:00:00Z';Definition=[pscustomobject]@{Settings=[pscustomobject]@{ExecutionTimeLimit=$script:limit}};Instances=$instances}
  $task | Add-Member ScriptMethod GetInstances {param($flags) if($flags -ne 0){throw 'flags'}; $this.Instances}
  $folder=[pscustomobject]@{Task=$task}
  $folder | Add-Member ScriptMethod GetTask {param($name) if($name -cne 'AcmeRefresh'){throw 'name'}; $this.Task}
  $service=[pscustomobject]@{Folder=$folder}
  $service | Add-Member ScriptMethod Connect {}
  $service | Add-Member ScriptMethod GetFolder {param($path) if($path -cne '\'){throw 'root'}; $this.Folder}
  return $service
}
'''


def test_scheduler_start_includes_external_gate_and_setup(tmp_path):
    p = ps_probe(tmp_path, FAKE + r'''
$c=Get-SchedulerTaskContext -TaskName AcmeRefresh -Service (New-FakeService) -Now ([datetimeoffset]'2026-01-01T00:15:00Z')
$b=New-TaskBudget -TimeoutSec 14400 -DeadlineUtc $c.deadline_utc -Now ([datetimeoffset]'2026-01-01T00:15:00Z')
$first=Get-TaskRemainingSeconds $b -Now ([datetimeoffset]'2026-01-01T00:15:10Z')
$second=Get-TaskRemainingSeconds $b -Now ([datetimeoffset]'2026-01-01T00:16:10Z')
@{context=$c;first=$first;second=$second} | ConvertTo-Json -Depth 4
''')
    assert p.returncode == 0, p.stderr
    d=json.loads(p.stdout)
    assert d['context']['run_id'] == 'scheduler:AcmeRefresh:{00000000-0000-0000-0000-000000000001}'
    assert d['first'] <= 2630 and d['second'] <= 2570
    assert 59 <= d['first']-d['second'] <= 61


@pytest.mark.parametrize('change', ["$script:count=0", "$script:count=2", "$script:path='\\Other'", "$script:limit='garbage'"])
def test_missing_ambiguous_or_wrong_scheduler_instance_refused(tmp_path, change):
    p=ps_probe(tmp_path, FAKE + change + "\nGet-SchedulerTaskContext -TaskName AcmeRefresh -Service (New-FakeService) -Now ([datetimeoffset]'2026-01-01T00:15:00Z')")
    assert p.returncode != 0
    assert 'task-context' in p.stderr


@pytest.mark.parametrize('deadline', ['garbage','NaN','2026-01-01T00:00:00','2026-01-01T00:00:00+03:00'])
def test_malformed_deadline_fails_visible(tmp_path, deadline):
    p=ps_probe(tmp_path, "New-TaskBudget -TimeoutSec 7200 -DeadlineUtc '"+deadline+"'")
    assert p.returncode != 0 and 'task-context' in p.stderr


def test_unlimited_scheduler_does_not_make_single_job_unlimited(tmp_path):
    p=ps_probe(tmp_path, FAKE + r'''
$script:limit='PT0S'
$c=Get-SchedulerTaskContext -TaskName AcmeRefresh -Service (New-FakeService) -Now ([datetimeoffset]'2026-01-01T00:15:00Z')
$b=New-TaskBudget -TimeoutSec 7200 -DeadlineUtc $c.deadline_utc
@{deadline=$c.deadline_utc;remaining=(Get-TaskRemainingSeconds $b)} | ConvertTo-Json
''')
    assert p.returncode == 0, p.stderr
    d=json.loads(p.stdout)
    assert d['deadline'] is None and 7100 < d['remaining'] <= 7140


def test_inherited_deadline_cannot_extend_actual_scheduler_limit(tmp_path):
    p=ps_probe(tmp_path,FAKE+r'''
function Get-SchedulerTaskContext { param($TaskName)
 return @{deadline_utc=[datetimeoffset]::UtcNow.AddSeconds(1800).UtcDateTime.ToString('o')}
}
$env:TASK_DEADLINE_UTC=[datetimeoffset]::UtcNow.AddHours(4).UtcDateTime.ToString('o')
$b=Initialize-TaskBudget -TaskName AcmeRefresh -TimeoutSec 14400 -Scheduled
$long=Get-TaskRemainingSeconds $b
$env:TASK_DEADLINE_UTC=[datetimeoffset]::UtcNow.AddSeconds(600).UtcDateTime.ToString('o')
$b=Initialize-TaskBudget -TaskName AcmeRefresh -TimeoutSec 14400 -Scheduled
@{long=$long;short=(Get-TaskRemainingSeconds $b);deadline=$env:TASK_DEADLINE_UTC} | ConvertTo-Json
''')
    assert p.returncode==0,p.stderr
    data=json.loads(p.stdout)
    assert 1738<=data['long']<=1740 and 538<=data['short']<=540
    assert data['deadline'].endswith('Z')


@pytest.mark.parametrize('name,relative,limit,want', [
    ('DemandMiningEOD','demand-mining/skills/demand-mining/scripts/wrapper.ps1','PT1H',2640),
    ('CcDailyTriage','claude/scripts/cc-daily-triage.ps1','PT2H',6240),
])
def test_actual_entry_budget_uses_scheduler_start_before_business(tmp_path,name,relative,limit,want,config_source):
    root=config_source if name=='CcDailyTriage' else Path(__file__).resolve().parents[2]
    source=(root/relative).read_text(encoding='utf-8-sig')
    if name=='DemandMiningEOD':
        prefix=source.split('function Resolve-Python',1)[0]
    else:
        prefix=source.split("$ErrorActionPreference = 'Continue'",1)[0]
    fake=FAKE.replace('AcmeRefresh',name).replace("'PT1H'",repr(limit)).replace(
        "[datetime]'2026-01-01T00:00:00Z'",'([datetime]::UtcNow.AddSeconds(-900))')
    body=fake+r'''
function New-Object {param($ComObject)
 if($ComObject -ne 'Schedule.Service'){throw 'unexpected object creation'}
 New-FakeService
}
& {
'''+prefix+"\n@{remaining=(Get-TaskRemainingSeconds $taskBudget);deadline=$env:TASK_DEADLINE_UTC} | ConvertTo-Json\n} -Scheduled -TaskContextScript '"+str(OWNER)+"'\n"
    p=ps_probe(tmp_path,body)
    assert p.returncode==0,p.stderr
    data=json.loads(p.stdout)
    assert want-3<=data['remaining']<=want
    assert data['deadline'].endswith('Z')


def test_budget_clock_stages_and_expiration(tmp_path):
    p=ps_probe(tmp_path,r'''
$b=New-TaskBudget -TimeoutSec 3600 -Now ([datetimeoffset]'2026-01-01T00:00:00Z')
$steps=@(0,30,930,950) | ForEach-Object {
 Get-TaskRemainingSeconds $b -Now ([datetimeoffset]'2026-01-01T00:00:00Z').AddSeconds($_)
}
$expired=$false
try { Get-TaskRemainingSeconds $b -Now ([datetimeoffset]'2026-01-01T00:59:10Z') } catch {$expired=$true}
@{steps=$steps;expired=$expired} | ConvertTo-Json
''')
    assert p.returncode==0,p.stderr
    data=json.loads(p.stdout)
    assert data['steps'][1:]==[3510,2610,2590]
    assert 3538<=data['steps'][0]<=3540 and data['expired']
