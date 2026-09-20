import json
import subprocess
from types import SimpleNamespace

from test_operations_ui import run
from test_panel_parity import get, synthetic_server
from tools.make_fixtures import work_feed_case
import work_status


def test_work_projection_never_turns_failure_or_signal_into_a_decision():
    setup='const feed='+json.dumps(work_feed_case())+';'
    result=run('workProjection(feed)',setup)
    assert [row['id'] for row in result['active']]==['active']
    assert [row['id'] for row in result['results']]==['result']
    assert [row['id'] for row in result['interrupted']]==['draft']
    assert result['decisions'] is None
    assert [row['id'] for row in result['tracked']]==['reminder']
    assert run('workRows(feed).length',setup)==4
    assert run("workRows(feed,{role:'signal'}).map(row=>row.id)",setup)==['news']


def test_unknown_source_and_empty_feed_are_not_all_clear():
    assert run('workProjection({available:false})')['decisions'] is None
    assert run("workRows({available:false},{role:'all'})")==[]
    result=run("WORK=feed;renderWorkPlatform();$('work-coverage').textContent",'const feed='+json.dumps(work_feed_case())+';')
    assert '这里尚无独立验证结果' in result


def test_task_buttons_are_labeled_and_inapplicable_run_is_explained():
    result=run("taskActionButtons({name:'Acme',state:'Disabled'},true)")
    assert '启用</button>' in result and '停用并移出清单</button>' in result
    assert 'title="请先启用"' in result and 'disabled' in result
    assert 'class="mini ib' not in result


def test_read_only_preflight_blocks_network_write():
    setup="document.querySelector=()=>({content:'true'});"
    result=run("api('/api/act',{method:'POST'}).catch(error=>error.message)",setup)
    assert '只读预览' in result


def test_grouping_preserves_old_links_under_intent():
    assert run("['pipelines','tasks','convos','llm','repos','storage'].map(viewGroup)")==['automations','automations','work','work','resources','resources']


def test_work_refresh_waits_for_an_existing_read():
    setup="let finish,reads=0;renderWorkPlatform=()=>{};api=()=>{reads++;return new Promise(resolve=>finish=resolve);};"
    result=run("(async()=>{const a=loadWork(),b=loadWork();const same=a===b;finish({available:true});await b;return {same,reads,loading:WORK_LOADING};})()",setup)
    assert result=={'same':True,'reads':1,'loading':False}


def test_work_api_is_authenticated(synthetic_server,monkeypatch):
    monkeypatch.setattr(work_status,'read_configured',lambda:work_feed_case())
    port=synthetic_server[0]
    assert get(port,'/api/work')[0]==403
    status,_,body=get(port,'/api/work',token='synthetic-browser-token')
    assert status==200 and json.loads(body)==work_feed_case()


def test_owner_invocation_uses_fixed_read_command_and_rejects_bad_contract(tmp_path,monkeypatch):
    cli=tmp_path/'reminder.py';cli.write_text('# synthetic',encoding='utf-8')
    env={'TASK_CONSOLE_REMINDER_CLI':str(cli),'TASK_CONSOLE_REMINDER_DB':str(tmp_path/'work.sqlite3')}
    calls=[]
    def invoke(argv,**kwargs):
        calls.append((argv,kwargs));return SimpleNamespace(returncode=0,stdout=json.dumps(work_feed_case()))
    monkeypatch.setattr(subprocess,'run',invoke)
    assert work_status.read_configured(env)['available']
    assert calls[0][0][1:]==[str(cli),'--db',env['TASK_CONSOLE_REMINDER_DB'],'work-feed']
    assert not calls[0][1].get('shell')
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='[]'))
    assert work_status.read_configured(env)['reason']=='work_contract_invalid'
    assert work_status.read_configured({})['available'] is False
