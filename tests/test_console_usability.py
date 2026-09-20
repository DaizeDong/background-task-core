"""Interaction and factual boundaries for the compact console, using generated data."""
import json

from test_operations_ui import run
from tools.make_fixtures import usability_case


def setup_feed():
    return 'WORK='+json.dumps(usability_case()['feed'])+';'


def test_home_shortcut_counts_match_the_target_filters():
    result=run("""(() => {
      const p=workProjection(WORK);
      return [['agent_work','active',p.active],['agent_work','done',p.results],['tracked_item','tracked_active',p.tracked]].map(([role,state,expected])=>{
        setWorkFilters({role,state});
        return {actual:selectedWorkRows().map(r=>r.id).sort(),expected:expected.map(r=>r.id).sort()};
      });
    })()""",setup_feed())
    assert all(row['actual']==row['expected'] for row in result)
    assert result[2]['actual']==['reminder','tracked-blocked','tracked-snoozed']


def test_source_shortcut_clears_hidden_filters_and_reset_restores_default():
    result=run("""(() => {
      setWorkFilters({role:'agent_work',state:'done',query:'not-found'});
      setWorkFilters({role:'all',source:'synthetic'});
      const sourceCount=selectedWorkRows().length;
      setWorkFilters();
      return {sourceCount,query:WORK_QUERY,state:WORK_STATE,source:WORK_SOURCE,role:WORK_ROLE,hidden:$('work-reset').hidden};
    })()""",setup_feed())
    assert result=={'sourceCount':9,'query':'','state':'','source':'','role':'work','hidden':True}


def test_missing_source_remains_visible_and_unloaded_activity_has_no_dead_button():
    result=run("""WORK_SOURCE='previous-source';renderWorkPlatform();
      ({source:$('work-source').innerHTML,events:$('work-activity').innerHTML})""",setup_feed())
    assert 'previous-source（本次未读到）' in result['source']
    assert 'data-work-id="active"' in result['events']
    assert 'data-work-id="not-loaded"' not in result['events']
    assert '对应的工作记录未加载' in result['events']


def test_failed_refresh_retains_last_read_and_marks_it_as_old():
    result=run("""(async()=>{
      api=async()=>{throw new Error('synthetic read failure');};await loadWork();
      return {available:WORK.available,observed:WORK.observed_at,count:WORK.items.length,loading:WORK_LOADING,
        error:WORK_ERROR,notice:$('work-source-state').textContent,coverage:$('work-coverage').textContent};
    })()""",setup_feed())
    assert result['available'] and result['count']==9 and not result['loading']
    assert result['observed']=='2030-01-02T00:00:00Z'
    assert result['error']=='synthetic read failure'
    assert '刷新失败' in result['notice'] and '上次读取' in result['coverage']


def test_trigger_formatting_preserves_frequency_disabled_and_unknown_states():
    setup='const triggers='+json.dumps(usability_case()['triggers'])+';'
    summaries=run("triggers.map(t=>taskSchedule({triggersRaw:[t]}))",setup)
    assert summaries[0]=='每 5 分钟 重复'
    assert '10:15' not in summaries[0]  # The original boundary is not a daily restart.
    assert '每 2 天' in summaries[1] and '持续 3 小时' in summaries[1] and '已停用' in summaries[1]
    assert '每 2 周 周一、周三' in summaries[2] and '截止时间' in summaries[2]
    assert '未识别' in summaries[3] and 'AcmeCustom' in summaries[3]
    full=run("taskSchedule({triggersRaw:[triggers[0]]},true)",setup)
    assert '2030-01-02T10:15:00' in full
    assert run("taskDuration('AcmeDuration')")=='AcmeDuration'


def test_filter_reset_clears_task_visibility_without_running_tasks():
    result=run("""$('q').value='acme';$('cat').value='acme';$('only').checked=true;$('hideoff').checked=true;
      DATA=null;resetFilters('tasks');
      [$('q').value,$('cat').value,$('only').checked,$('hideoff').checked]""")
    assert result==['','',False,False]


def test_run_and_stop_feedback_do_not_claim_payload_success():
    assert '执行结果尚未确认' in run("taskOutcome({status:'run_requested'},'run')")
    assert '子进程退出情况及关联工作状态尚未确认' in run("taskOutcome({status:'scheduler_idle'},'stop')")
    assert '无法确认' in run("taskOutcome({status:'cleanup_uncertain'},'stop')")
    assert run("taskOutcome({status:'custom',message:'synthetic owner message'},'run')")=='synthetic owner message'


def test_partial_delete_reports_what_changed_and_refreshes_the_list():
    case=usability_case()
    setup=('CXL='+json.dumps(case['cleanup'])+';const reply='+json.dumps(case['partial_delete'])+';'+"""
      CXSEL=new Set(['acme.jsonl']);globalThis.confirm=()=>true;let report='',refreshes=0;
      globalThis.alert=text=>report=text;api=async()=>reply;toast=()=>{};
      loadCxList=async()=>refreshes++;loadCodex=loadSys=()=>{};
    """)
    result=run("cxDelete().then(()=>({report,refreshes}))",setup)
    assert '已删除 1 个文件' in result['report']
    assert 'sample.jsonl' in result['report'] and 'synthetic error' in result['report']
    assert result['refreshes']==1


def test_late_call_response_cannot_replace_newer_filter_results():
    result=run("""(async()=>{
      const pending=[];api=()=>new Promise(resolve=>pending.push(resolve));renderCalls=()=>{};
      const first=loadCalls();LMQ.q='synthetic';const second=loadCalls();
      pending[1]({rows:[],total:2});await second;
      pending[0]({rows:[],total:99});await first;
      return {total:LMTOTAL,query:LMQ.q};
    })()""")
    assert result=={'total':2,'query':'synthetic'}


def test_open_record_keeps_the_displayed_snapshot_after_feed_refresh():
    result=run("""$('work-detail').showModal=()=>{};openWorkRecord('active');
      const displayed=WORK_DETAIL_ITEM;
      WORK.items=WORK.items.map(item=>({...item,summary:'synthetic new summary'}));
      ({same:WORK_DETAIL_ITEM===displayed,displayed:WORK_DETAIL_ITEM.summary,newSummary:WORK.items[0].summary})""",setup_feed())
    assert result=={'same':True,'displayed':'Synthetic summary','newSummary':'synthetic new summary'}
