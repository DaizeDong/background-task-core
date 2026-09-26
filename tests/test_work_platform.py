import json
import subprocess
from types import SimpleNamespace

from test_operations_ui import run
from test_panel_parity import get, synthetic_server
from tools.make_fixtures import work_feed_case, linked_work_case
from tools.make_fixtures import consolidated_work_case, searchable_work_group_case
from tools.make_fixtures import blocked_agent_case


def test_queued_work_explains_cleanup_blocker_instead_of_ordinary_wait():
    setup='const item='+json.dumps(blocked_agent_case())+';'
    assert run('workLabel(item)',setup)=='队列受阻'
    for compact in ('false','true'):
        html=run('workItemRow(item,'+compact+')',setup)
        assert '进程清理尚未确认' in html and '查看阻塞任务' in html
        assert 'data-work-id="old-work"' in html
    detail_setup=setup+'WORK={available:true,items:[item],events:[],sources:[],coverage:{}};$("work-detail").open=true;'
    detail=run('openWorkRecord(item.id);$("work-detail-body").innerHTML',detail_setup)
    assert '进程清理尚未确认' in detail and 'data-work-id="old-work"' in detail


def test_reviewed_groups_preserve_history_alarms_and_filtered_orphans():
    setup='const rows='+json.dumps(consolidated_work_case())+';'
    result=run('groupWorkRows(rows)',setup)
    assert len(result)==4
    assert {r['id'] for r in result[0]['linked_records']}=={'old','alarm'}
    filtered=run("groupWorkRows(rows.filter(r=>r.id==='alarm'),rows)",setup)
    assert [r['id'] for r in filtered]==['alarm']
    assert run("groupWorkRows(rows.filter(r=>r.state==='pending'),rows)[0].linked_records.length",setup)==2
import work_status


def test_default_work_list_hides_history_but_history_filter_restores_it():
    setup = 'WORK=' + json.dumps(work_feed_case()) + ';'
    assert run('selectedWorkRows().map(row=>row.id)', setup) == ['active', 'draft', 'reminder']
    assert run("setWorkFilters({state:''});selectedWorkRows().length", setup) == 4


def test_linked_execution_is_grouped_only_when_parent_is_in_same_selection():
    setup = 'WORK=' + json.dumps(linked_work_case()) + ';'
    rows = run('selectedWorkRows()', setup)
    assert [row['id'] for row in rows] == ['draft', 'reminder']
    assert rows[1]['linked_work'][0]['id'] == 'active'
    assert run("setWorkFilters({role:'agent_work'});selectedWorkRows().map(r=>r.id)", setup) == ['active', 'draft']
    assert run("setWorkFilters({query:'Acme active'});selectedWorkRows().map(r=>r.id)", setup) == ['reminder']
    assert run("WORK.items[0].origin_item_id='missing';selectedWorkRows().length", setup) == 3
    assert run("WORK.items[3].state='done';selectedWorkRows().map(r=>r.id)", setup) == ['active', 'draft']
    html = run('renderWorkPlatform();$("work-list").innerHTML', setup)
    assert 'data-work-id="active"' in html and '关联执行' in html


def test_search_child_text_preserves_group_and_filter_boundaries():
    setup='WORK='+json.dumps(searchable_work_group_case())+';'
    assert run("setWorkFilters({query:'Leave for'});selectedWorkRows().map(r=>r.id)",setup)==['root']
    assert run("setWorkFilters({role:'all',query:'prepared'});selectedWorkRows().map(r=>r.id)",setup)==['root']
    assert run("setWorkFilters({query:'prepared'});selectedWorkRows().map(r=>r.id)",setup)==[]
    assert run("setWorkFilters({role:'all',source:'example-mail',query:'prepared'});selectedWorkRows().map(r=>r.id)",setup)==['old']
    assert run("setWorkFilters({query:'Separate task'});selectedWorkRows().map(r=>r.id)",setup)==['orphan']
    assert run("setWorkFilters({query:'Cycle A'});selectedWorkRows().map(r=>r.id)",setup)==['cycle-a']
    assert run("workProjection(WORK).tracked.map(r=>r.id)",setup)==['cycle-a','cycle-b','orphan','root']


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
