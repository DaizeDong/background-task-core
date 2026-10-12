import http.client
import json
import subprocess
from types import SimpleNamespace

import pytest

from test_panel_parity import synthetic_server, get
from tools.make_fixtures import work_action_case, work_feed_case
import work_actions
import work_status


def request():
    item = work_action_case()
    return {'item_id': item['id'], 'action_id': 'agent', 'revision': item['actions']['revision'],
            'request_id': 'synthetic-request-01'}


def test_action_http_requires_auth_before_dispatch(synthetic_server, monkeypatch):
    seen = []
    monkeypatch.setattr(work_actions, 'submit', lambda body, **kw: seen.append(body) or {'ok':True})
    port = synthetic_server[0]
    for token, expected in [(None,403),('synthetic-browser-token',200)]:
        client = http.client.HTTPConnection('127.0.0.1',port)
        headers = {'X-Console-Token':token} if token else {}
        client.request('POST','/api/work/action',json.dumps(request()),headers)
        response=client.getresponse()
        assert response.status == expected
        response.read();client.close()
    assert seen == [request()]


def test_readonly_and_arbitrary_payload_never_reach_owner(monkeypatch):
    monkeypatch.setattr(work_actions,'invoke',lambda *a:pytest.fail('must not invoke'))
    assert work_actions.submit(request(),env={'TASK_CONSOLE_READ_ONLY':'1'})['code']=='read_only'
    assert work_actions.submit(dict(request(),command='anything'),env={})['code']=='invalid_action_request'


def test_readonly_server_blocks_all_post_routes(synthetic_server,monkeypatch):
    monkeypatch.setenv('TASK_CONSOLE_READ_ONLY','1')
    port=synthetic_server[0]
    for path in ('/api/work/action','/api/act'):
        client=http.client.HTTPConnection('127.0.0.1',port)
        client.request('POST',path,'{}',{'X-Console-Token':'synthetic-browser-token'})
        response=client.getresponse();assert response.status==403
        response.read();client.close()


def test_owner_call_uses_only_bound_cli_and_json_stdin(tmp_path,monkeypatch):
    cli=tmp_path/'reminder.py';cli.write_text('# synthetic',encoding='utf-8')
    db=tmp_path/'work.sqlite3';db.touch()
    env={'TASK_CONSOLE_REMINDER_CLI':str(cli),'TASK_CONSOLE_REMINDER_DB':str(db),
         'TASK_CONSOLE_ACTION_WORKSPACE':str(tmp_path/'output')}
    seen=[]
    def invoke(argv,**kwargs):
        seen.append((argv,kwargs))
        return SimpleNamespace(returncode=0,stdout=json.dumps({'schemaVersion':1,'ok':True,'action':{}}))
    monkeypatch.setattr(subprocess,'run',invoke)
    assert work_actions.invoke('work-action',request(),env)['ok']
    argv,kwargs=seen[0]
    assert argv[1:]==[str(cli),'--db',str(db),'work-action'] and not kwargs.get('shell')
    assert json.loads(kwargs['input'])==request()
    assert kwargs['env']['SCHEDULE_ACTION_WORKSPACE']==env['TASK_CONSOLE_ACTION_WORKSPACE']
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:(_ for _ in ()).throw(subprocess.TimeoutExpired('synthetic',30)))
    with pytest.raises(work_actions.ActionError) as error:
        work_actions.invoke('work-action',request(),env)
    assert error.value.uncertain


def test_dispatch_uses_owner_target_and_persists_unconfirmed_result(monkeypatch):
    monkeypatch.setattr(work_actions.controller,'load_runtime',lambda **kwargs:object())
    calls=[]
    def invoke(verb,payload,env):
        calls.append((verb,payload))
        if verb=='work-action':
            return {'ok':True,'action':{},'dispatch':{'task_id':'synthetic/exact','action_id':'receipt'}}
        return {'ok':True,'status':'task_requested','action':{}}
    monkeypatch.setattr(work_actions,'invoke',invoke)
    monkeypatch.setattr(work_actions,'_run_task',lambda target,runtime:{'ok':target=='synthetic/exact','status':'run_requested'})
    result=work_actions.submit(dict(request(),action_id='task'),env={})
    assert result['status']=='task_requested'
    assert calls[1][0]=='work-action-result' and calls[1][1]['result']['ok']


def _capture_wakeups(monkeypatch, outcome=None):
    """Run the wakeup thread inline and record the scheduler argv it would have used."""
    seen = []
    class Inline:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            self.target()
    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        if outcome is not None:
            raise outcome
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(work_actions.threading, 'Thread', Inline)
    monkeypatch.setattr(work_actions.subprocess, 'run', run)
    return seen


def test_agent_action_wakes_the_tick_directly_without_the_controller(monkeypatch):
    monkeypatch.setattr(work_actions.controller,'load_runtime',lambda **kw:pytest.fail('an agent action must not load the controller'))
    monkeypatch.setattr(work_actions,'_run_task',lambda *args:pytest.fail('an agent action must not run through the controller'))
    payloads=[]
    monkeypatch.setattr(work_actions,'invoke',lambda verb,payload,env:payloads.append((verb,payload)) or {'ok':True,'status':'queued','action':{'id':'receipt'},'wakeup':True})
    seen=_capture_wakeups(monkeypatch)
    reply=work_actions.submit(request(),env={'TASK_CONSOLE_AGENT_TASK_ID':'synthetic/tick'})
    assert reply['ok'] and reply['action']['id']=='receipt' and 'wakeup' not in reply
    # The owner gets no conversation context: no producer writes a session link.
    assert payloads==[('work-action',{'request':request(),'context':''})]
    (argv,kwargs),=seen
    assert argv==['schtasks','/run','/tn',work_actions.AGENT_TICK] and not kwargs.get('shell')
    assert kwargs['creationflags']==getattr(subprocess,'CREATE_NO_WINDOW',0)


def test_failed_wakeup_preserves_queued_action(monkeypatch):
    monkeypatch.setattr(work_actions,'invoke',lambda *args:{'ok':True,'status':'queued','action':{'id':'receipt'},'wakeup':True})
    seen=_capture_wakeups(monkeypatch,OSError('synthetic scheduler failure'))
    reply=work_actions.submit(request(),env={'TASK_CONSOLE_AGENT_TASK_ID':'synthetic/tick'})
    assert len(seen)==1 and reply['ok'] and reply['status']=='queued' and reply['action']['id']=='receipt'


def test_no_wakeup_when_the_owner_did_not_ask_for_one(monkeypatch):
    monkeypatch.setattr(work_actions,'invoke',lambda *args:{'ok':True,'status':'queued','action':{'id':'receipt'},'wakeup':False})
    seen=_capture_wakeups(monkeypatch)
    assert work_actions.submit(request(),env={'TASK_CONSOLE_AGENT_TASK_ID':'synthetic/tick'})['ok']
    assert seen==[]


def test_agent_action_without_a_queue_is_refused_before_the_owner(monkeypatch):
    monkeypatch.setattr(work_actions,'invoke',lambda *args:pytest.fail('nothing may be reserved without a queue'))
    reply=work_actions.submit(request(),env={})
    assert not reply['ok'] and reply['code']=='queue_not_configured'


def test_exact_task_mapping_rejects_unregistered_ids(monkeypatch):
    runtime=SimpleNamespace(load=lambda:{})
    monkeypatch.setattr(work_actions.controller.registration,'_compile',lambda value:{'task_specs':[{'task_id':'synthetic/exact','name':'AcmeTask'}]})
    calls=[]
    monkeypatch.setattr(work_actions.controller,'Controller',lambda value:SimpleNamespace(action=lambda name,verb:calls.append((name,verb)) or {'ok':True}))
    assert work_actions._run_task('synthetic/exact',runtime)['ok']
    with pytest.raises(work_actions.ActionError):work_actions._run_task('AcmeTask',runtime)
    assert calls==[('AcmeTask','run')]


def test_manual_completion_does_not_require_executor(monkeypatch):
    monkeypatch.setattr(work_actions.controller, 'load_runtime', lambda **kw: pytest.fail('completion must not require a scheduler'))
    seen = []
    monkeypatch.setattr(work_actions, 'invoke', lambda verb, payload, env: seen.append((verb, payload)) or {'ok': True, 'status': 'done', 'action': {}})
    payload = dict(request(), action_id='complete')
    assert work_actions.submit(payload, env={})['status'] == 'done'
    assert seen == [('work-action', {'request': payload, 'context': ''})]


def test_manual_completion_still_respects_readonly(monkeypatch):
    monkeypatch.setattr(work_actions, 'invoke', lambda *args: pytest.fail('read-only mutation'))
    reply = work_actions.submit(dict(request(), action_id='complete'), env={'TASK_CONSOLE_READ_ONLY': '1'})
    assert not reply['ok'] and reply['code'] == 'read_only'


def test_manual_completion_offer_survives_missing_executor(tmp_path, monkeypatch):
    cli = tmp_path / 'owner.py'
    cli.touch()
    feed = work_feed_case()
    item = work_action_case()
    item['actions']['offers'].append({'id': 'complete', 'kind': 'complete', 'label': '标记完成', 'enabled': True})
    feed['items'] = [item]
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(feed)))
    reply = work_status.read_configured({'TASK_CONSOLE_REMINDER_CLI': str(cli), 'TASK_CONSOLE_REMINDER_DB': str(tmp_path / 'db')})
    actions = reply['items'][0]['actions']
    assert actions['available'] and not actions['offers'][0]['enabled'] and actions['offers'][1]['enabled']


def test_agent_offer_needs_only_the_queue_and_a_task_offer_needs_the_controller(tmp_path, monkeypatch):
    cli = tmp_path / 'owner.py'
    cli.touch()
    feed = work_feed_case()
    item = work_action_case()
    item['actions']['offers'].append({'id': 'task', 'kind': 'task', 'label': 'run', 'enabled': True, 'target_id': 'synthetic/exact'})
    feed['items'] = [item]
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(feed)))
    reply = work_status.read_configured({'TASK_CONSOLE_REMINDER_CLI': str(cli), 'TASK_CONSOLE_REMINDER_DB': str(tmp_path / 'db'),
                                         'TASK_CONSOLE_AGENT_TASK_ID': 'synthetic/tick'})
    agent, task = reply['items'][0]['actions']['offers']
    assert agent['kind'] == 'agent' and agent['enabled'] is True
    assert task['kind'] == 'task' and task['enabled'] is False
