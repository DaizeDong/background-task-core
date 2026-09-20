import http.client
import json
import subprocess
from types import SimpleNamespace

import pytest

from test_panel_parity import synthetic_server, get
from tools.make_fixtures import work_action_case, work_feed_case, action_session_fixture
import work_actions
import work_status
from work_context import read_session_context


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


def test_failed_wakeup_preserves_queued_action(monkeypatch):
    monkeypatch.setattr(work_actions.controller,'load_runtime',lambda **kwargs:object())
    monkeypatch.setattr(work_actions,'_context',lambda *args:'')
    monkeypatch.setattr(work_actions,'invoke',lambda *args:{'ok':True,'status':'queued','action':{'id':'receipt'},'wakeup':True})
    monkeypatch.setattr(work_actions,'_run_task',lambda *args:{'ok':False})
    reply=work_actions.submit(request(),env={'TASK_CONSOLE_AGENT_TASK_ID':'synthetic/tick'})
    assert reply['ok'] and reply['wakeup'] is False and reply['action']['id']=='receipt'


def test_context_uses_exact_session_identity_and_refuses_missing_or_ambiguous(tmp_path):
    session,path=action_session_fixture(tmp_path)
    text=read_session_context(session,str(tmp_path))
    assert 'Acme report' in text and 'UNRELATED_CANARY' not in text
    with pytest.raises(ValueError):
        read_session_context('../session',str(tmp_path))
    duplicate=tmp_path/'another-project'/path.name
    duplicate.parent.mkdir();duplicate.write_bytes(path.read_bytes())
    with pytest.raises(ValueError,match='ambiguous'):
        read_session_context(session,str(tmp_path))


def test_no_title_matching_or_inferred_session_context(monkeypatch):
    feed=work_feed_case();feed['items']=[work_action_case()]
    monkeypatch.setattr(work_status,'read_configured',lambda env:feed)
    assert work_actions._context(feed['items'][0]['id'],{})==''


def test_context_view_is_authenticated_and_uses_owner_item_id(synthetic_server,monkeypatch):
    seen=[]
    monkeypatch.setattr(work_actions,'context_view',lambda item_id:seen.append(item_id) or {'available':True,'text':'synthetic context'})
    port=synthetic_server[0]
    assert get(port,'/api/work/context?item_id=acme')[0]==403
    assert get(port,'/api/work/context?item_id=acme',token='synthetic-browser-token')[0]==200
    assert seen==['acme']
    assert get(port,'/api/work/context?item_id=acme&path=anything',token='synthetic-browser-token')[0]==400


def test_exact_task_mapping_rejects_unregistered_ids(monkeypatch):
    runtime=SimpleNamespace(load=lambda:{})
    monkeypatch.setattr(work_actions.controller.registration,'_compile',lambda value:{'task_specs':[{'task_id':'synthetic/exact','name':'AcmeTask'}]})
    calls=[]
    monkeypatch.setattr(work_actions.controller,'Controller',lambda value:SimpleNamespace(action=lambda name,verb:calls.append((name,verb)) or {'ok':True}))
    assert work_actions._run_task('synthetic/exact',runtime)['ok']
    with pytest.raises(work_actions.ActionError):work_actions._run_task('AcmeTask',runtime)
    assert calls==[('AcmeTask','run')]
