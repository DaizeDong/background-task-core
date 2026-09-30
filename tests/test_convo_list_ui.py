"""Pagination state and retries, independent of layout rendering."""
import json

from test_operations_ui import run
from tools.make_fixtures import operations_case
from tools.make_fixtures import synthetic_conversation


def setup():
    data = operations_case()["conversations"]
    for group in data["groups"]:
        group["id"] = group["cwd"]
        group["nextCursor"] = "synthetic-cursor"
        group["hasMore"] = True
    return "CONVOS=" + json.dumps(data) + ";cvPaintGroup=()=>{};cvObserve=()=>{};"


def test_old_search_response_cannot_replace_the_new_search():
    result = run("""(async()=>{
      let pending=[];api=(path)=>new Promise(resolve=>pending.push({path,resolve}));
      const a=loadConvos();CV_QUERY='new search';const b=loadConvos();
      pending[1].resolve({...CONVOS,summary:{...CONVOS.summary,files:99}});await b;
      pending[0].resolve({...CONVOS,summary:{...CONVOS.summary,files:1}});await a;
      return {files:CONVOS.summary.files,paths:pending.map(p=>p.path),loading:CV_LOADING};
    })()""", setup())
    assert result["files"] == 99 and result["loading"] is False
    assert "q=new%20search" in result["paths"][1]


def test_duplicate_load_requests_are_joined_and_rows_do_not_repeat():
    result = run("""(async()=>{
      let calls=0,finish;const g=CONVOS.groups[0];
      api=()=>{calls++;return new Promise(resolve=>finish=resolve);};
      const first=cvLoadMore(g.id),second=cvLoadMore(g.id);
      const row={...g.shown[0],id:'synthetic-new'};
      finish({available:true,groups:[{...g,shown:[g.shown[0],row],hasMore:false,nextCursor:null}]});
      await Promise.all([first,second]);return {calls,rows:g.shown.length,cursor:g.nextCursor};
    })()""", setup())
    assert result == {"calls": 1, "rows": 2, "cursor": None}


def test_failed_page_keeps_rows_and_cursor_for_an_explicit_retry():
    result = run("""(async()=>{
      const g=CONVOS.groups[0];api=async()=>{throw new Error('synthetic failure');};
      await cvLoadMore(g.id);
      const failed={rows:g.shown.length,cursor:g.nextCursor,error:CV_PAGES.get(g.id).error};
      api=async()=>({available:true,groups:[{...g,shown:[],hasMore:false,nextCursor:null}]});
      await cvLoadMore(g.id);return {failed,busy:CV_PAGES.get(g.id).busy,error:CV_PAGES.get(g.id).error};
    })()""", setup())
    assert result["failed"]["rows"] == 1 and result["failed"]["cursor"] == "synthetic-cursor"
    assert "synthetic failure" in result["failed"]["error"]
    assert result["busy"] is False and result["error"] is None


def test_disclosure_clicks_do_not_open_conversations():
    result = run("""(()=>{
      let opened=0;openConvoChain=()=>opened++;
      cvClick({target:{closest:selector=>selector==='details'?{}:null}});
      const before=opened;
      cvClick({target:{closest:selector=>selector==='[data-cvopen]'?{dataset:{cvopen:'synthetic'}}:null}});
      return {before,opened};
    })()""")
    assert result == {"before": 0, "opened": 1}


def test_open_details_survive_row_repainting():
    result = run("""(()=>{
      const g=CONVOS.groups[0];CV_DETAILS.add(g.shown[0].id);
      return cvRows(g);
    })()""", setup())
    assert ' open><summary title="更多操作" aria-label="更多操作">' in result


def deletion_setup():
    sid, _ = synthetic_conversation()
    return setup() + """
      const row={id:SID,projectDir:'C--Acme-source',title:'Example conversation'};
      const plan={files:3,bytes:1200,indexEntries:1,fingerprint:'a'.repeat(64)};
      for(const id of ['cv-submit','cv-cancel','cv-dialog']) $(id).classList={add(){},remove(){}};
      $('cv-dialog').showModal=function(){this.open=true;};
      $('cv-dialog').close=function(){this.open=false;};
      toast=()=>{};loadConvos=async()=>{};
    """.replace("SID", json.dumps(sid))


def test_delete_waits_for_confirmation_and_retries_the_original_request():
    result = run("""(async()=>{
      let calls=[];api=async(path,options)=>{
        calls.push({path,body:JSON.parse(options.body)});
        if(path.endsWith('delete-plan')) return plan;
        if(calls.length===2) throw new Error('synthetic lost reply');
        return {deleted:true};
      };
      CV_EDIT={kind:'delete',row};await cvOpenDelete(row);
      const before=calls.length;await cvDelete();const openAfterFailure=$('cv-dialog').open;
      await cvDelete();return {before,openAfterFailure,closed:!$('cv-dialog').open,
        first:calls[1].body,second:calls[2].body};
    })()""", deletion_setup())
    assert result["before"] == 1 and result["openAfterFailure"] and result["closed"]
    assert result["first"] == result["second"] and result["first"]["confirmed"] is True


def test_changed_preview_requires_another_preview_and_confirmation():
    result = run("""(async()=>{
      let calls=[];api=async(path)=>{
        calls.push(path);if(path.endsWith('delete-plan')) return plan;
        throw Object.assign(new Error('changed'),{status:409,payload:{code:'conflict'}});
      };
      CV_EDIT={kind:'delete',row};await cvOpenDelete(row);await cvDelete();await cvDelete();
      return {calls,disabled:$('cv-submit').disabled,repreview:!$('cv-repreview').hidden,
        pending:CV_DELETIONS.size};
    })()""", deletion_setup())
    assert result == {"calls": ["/api/convo/delete-plan", "/api/convo/delete"],
                      "disabled": True, "repreview": True, "pending": 0}


def test_cleanup_pending_keeps_the_original_retry_identity():
    result = run("""(async()=>{
      api=async(path)=>path.endsWith('delete-plan')?plan:Promise.reject(
        Object.assign(new Error('cleanup pending'),{status:409,payload:{code:'cleanup_pending'}}));
      CV_EDIT={kind:'delete',row};await cvOpenDelete(row);const id=CV_EDIT.deletion.requestId;
      await cvDelete();$('cv-dialog').close();CV_EDIT={kind:'delete',row};await cvOpenDelete(row);
      return {same:CV_EDIT.deletion.requestId===id,disabled:$('cv-submit').disabled,
        label:$('cv-submit').textContent};
    })()""", deletion_setup())
    assert result == {"same": True, "disabled": False, "label": "重试原删除请求"}
